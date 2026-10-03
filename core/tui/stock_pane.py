"""
Dedicated Stock Management & Live Kit Inspector Workspace (Tab 6).

Provides an expansive, two-column workspace designed for warehouse floor operations:
1. Left Panel (65% width):
   - Operational Sub-Tabs:
     [1] 📤 Rapid Dispatch (Line Out): Instant barcode scanner stock check, Excel column paste, live dispatch table
     [2] 📊 Bond Reconciliation: Real-time PSV vs PMO daily reconciliation balance sheet
     [3] 📥 Inbound Delivery: Supplier delivery manifests, photo attachments, auto-kit generation
     [4] 🔄 Bond Transfers: Inter-bond transfer in/out tracking
     [5] ↩️ Line Returns: Uninstalled plates returned to stock (No-show, Defective, Rollover)
     [6] 🎯 Scheduled & Opening Balance: Shift targets, physical opening balance, handover remarks
     [7] 🔒 Safe Stock Taking: Physical barcode audit vs book closing stock
2. Right Panel (35% width):
   - Persistent live Kit Inspector (#inspector-stock):
     Automatically displays full hardware serials (GPS tracker, front/rear BLE beacons),
     warehouse location, stock verification status, and ITMS order linkage for any
     scanned or selected plate.
"""
from datetime import datetime
import os
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from core.tui.inspectors import InspectorPane, escape_markup, escape
from core.models import (
    DailyStockLedger,
    InstallationKit,
    InstallationOrder,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
)


class StockPane(Vertical):
    """Full-featured Stock & Reconciliation Workspace Pane (Tab 6)."""

    BINDINGS = [
        Binding("r", "refresh_stock", "Refresh"),
        Binding("p", "show_phone_scanner", "Phone Scanner"),
        Binding("e", "export_csv", "Export CSV"),
    ]

    def __init__(self, target_date_suffix: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.target_date_suffix = target_date_suffix or timezone.localdate().strftime("%d%m%y")
        self._cached_recon: Optional[Dict[str, Any]] = None
        self._is_syncing_kits: bool = False
        self._last_blocked_dispatch: List[str] = []
        self._last_unregistered_stocktake: List[str] = []

    def compose(self) -> ComposeResult:
        # Header banner (Date, Bond Name, Total kits ready)
        yield Static(id="stock-header-banner")

        with Horizontal(classes="tab-horizontal"):
            # LEFT PANEL (65% width): Operational Sub-Tabs
            with Vertical(classes="table-panel", id="stock-left-panel"):
                with TabbedContent(id="stock-sub-tabs"):
                    # SUB-TAB 1: 📤 Rapid Dispatch (Line Out) - Primary Floor View
                    with TabPane("📤 Rapid Dispatch (Line Out)", id="subtab-dispatch"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold yellow]📤 Rapid Dispatch & Physical Stock Verification[/bold yellow]  │  "
                                "[dim]Scan barcode or paste Excel column. Instant <50ms stock check blocks non-stock kits on the spot.[/dim]",
                                classes="stock-subtab-header",
                            )

                            # Instant Scan Verdict Alert Banner
                            yield Static(
                                "[bold green]⚡ READY FOR SCANNING:[/bold green] Scan plate barcode/QR code or paste Excel column below.",
                                id="lbl-stock-instant-feedback",
                            )

                            # Input Bar (Category, Rapid Scan Input, Dispatched Today Counter)
                            with Horizontal(classes="stock-row", id="row-dispatch-inputs"):
                                yield Select(
                                    [("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")],
                                    value="PSV",
                                    id="sel-stock-dispatch-category",
                                    prompt="Select Class",
                                )
                                yield Input(
                                    placeholder="⚡ Scan plate barcode / QR code [Enter to verify & dispatch]...",
                                    id="input-stock-dispatch-single",
                                    classes="stock-input-field",
                                )
                                yield Static("[bold green]✓ Dispatched: 0 kits[/bold green]", id="lbl-stock-dispatch-count", classes="stock-staged-badge")

                            # Excel Bulk Paste Section
                            yield Static("[dim]Optional: Paste multi-line Excel column below for instant batch dispatch (no commas required):[/dim]", classes="stock-table-title")
                            yield TextArea(
                                id="text-stock-dispatch-bulk",
                                classes="stock-textarea",
                            )

                            # Actions Row
                            with Horizontal(classes="stock-row"):
                                yield Button("⚡ Verify & Dispatch Excel Column", variant="primary", id="btn-process-excel-dispatch")
                                yield Button("📥 Export Blocked Kits (CSV)", variant="warning", id="btn-export-blocked-dispatch")
                                yield Button("🔍 Pre-verify Stock Only", variant="default", id="btn-preverify-stock")
                                yield Button("🧹 Clear Input", variant="default", id="btn-clear-dispatch-pane")
                                yield Button("📦 Sync ITMS Installation Kits", variant="success", id="btn-sync-stock-kits-tab1")

                            # Real-Time Live Dispatched Table for Shift
                            yield Static("[bold white]📋 Today's Dispatched Plates (Select row to inspect hardware serials & kit details):[/bold white]", classes="stock-table-title")
                            yield DataTable(id="table-stock-dispatch")

                    # SUB-TAB 2: 📊 Bond Reconciliation Balance
                    with TabPane("📊 Bond Reconciliation", id="subtab-recon"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold cyan]📋 Daily Bond Physical Stock & Reconciliation Balance[/bold cyan]  │  "
                                "[dim]Formula: Opening + Received + Transfer In - Transfer Out - Installed = Closing Balance[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Button("📦 Sync & Prep Morning Stock Kits", variant="success", id="btn-sync-stock-kits-recon")
                                yield Static("[dim]Safe Room Ready: [bold green]Checking...[/bold green][/dim]", id="lbl-stock-ready-badge", classes="stock-staged-badge")
                            yield DataTable(id="table-stock-report")
                            yield Static(id="stock-floor-summary")

                    # SUB-TAB 3: 📥 Inbound Deliveries
                    with TabPane("📥 Inbound Delivery", id="subtab-delivery"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold green]📥 Record Inbound Delivery Note Manifest[/bold green]  │  "
                                "[dim]Date-anchored deliveries for shift  │  Increases warehouse physical stock (+Received)[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Static("[bold white]Delivery Identifier:[/bold white] ", classes="stock-label-fixed")
                                yield Input(placeholder="Auto: DN-YYYYMMDD-01", id="input-deliv-number", classes="stock-input-field")
                                yield Static("[bold white]Category:[/bold white] ", classes="stock-label-fixed")
                                yield Select([("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")], value="PSV", id="sel-deliv-category")
                            with Horizontal(classes="stock-row"):
                                yield Static("[bold white]Supplier / Source:[/bold white] ", classes="stock-label-fixed")
                                yield Input(placeholder="e.g. Factory / Kampala Central Store", id="input-deliv-supplier", classes="stock-input-field")
                                yield Checkbox("Auto-create Kits in Inventory", value=True, id="chk-deliv-kits")
                            with Horizontal(classes="stock-row"):
                                yield Input(placeholder="⚡ Rapid scan delivery plate QR [Enter to add]...", id="input-deliv-single", classes="stock-input-field")
                                yield Static("[bold green]📦 Staged: 0 plates[/bold green]", id="lbl-deliv-staged", classes="stock-staged-badge")
                            yield TextArea(id="text-deliv-bulk", classes="stock-textarea")
                            with Horizontal(classes="stock-row"):
                                yield Button("📥 Ingest Delivery into Stock", variant="success", id="btn-save-delivery")
                                yield Button("🧹 Clear Scans", variant="default", id="btn-clear-deliv")
                            yield Static("[bold white]📋 Delivery Notes Logged for Shift:[/bold white]", classes="stock-table-title")
                            yield DataTable(id="table-stock-deliv-notes")

                    # SUB-TAB 4: 🔄 Bond Transfers
                    with TabPane("🔄 Bond Transfers", id="subtab-transfers"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold magenta]🔄 Record Inter-Bond Transfers (Transfer In / Transfer Out)[/bold magenta]  │  "
                                "[dim]Transfer In (+Stock) from another bond  │  Transfer Out (-Stock) to another bond[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Select(
                                    [("Bond Transfer In (Received)", "TRANSFER_IN"), ("Bond Transfer Out (Sent)", "TRANSFER_OUT")],
                                    value="TRANSFER_IN",
                                    id="sel-transfer-type",
                                )
                                yield Select([("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")], value="PSV", id="sel-transfer-category")
                            with Horizontal(classes="stock-row"):
                                yield Input(placeholder="Other Bond Location (e.g. Kampala Central Bond)", id="input-transfer-bond", classes="stock-input-field")
                                yield Input(placeholder="Plates Count (e.g. 50)", id="input-transfer-count", classes="stock-input-field")
                            with Horizontal(classes="stock-row"):
                                yield Input(placeholder="⚡ Rapid scan transfer plate QR [Enter to add]...", id="input-transfer-single", classes="stock-input-field")
                                yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-transfer-staged", classes="stock-staged-badge")
                            yield TextArea(id="text-transfer-bulk", classes="stock-textarea")
                            with Horizontal(classes="stock-row"):
                                yield Button("🔄 Record Bond Transfer", variant="primary", id="btn-save-transfer")
                                yield Button("🧹 Clear Transfer", variant="default", id="btn-clear-transfer")

                    # SUB-TAB 5: ↩️ Line Returns
                    with TabPane("↩️ Line Returns", id="subtab-returns"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold red]↩️ Record Uninstalled Plates Returned to Stock[/bold red]  │  "
                                "[dim]Scan plate QR or paste returned plates (Bike No-Show, Defective, Cancelled)[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Select([("Public White (PSV)", "PSV"), ("Private Yellow (PMO)", "PMO")], value="PSV", id="sel-return-category")
                                yield Select(
                                    [
                                        ("Bike No-Show (Owner did not arrive)", "BIKE_NO_SHOW"),
                                        ("Defective Plate (Damaged / Bad Print)", "DEFECTIVE_PLATE"),
                                        ("Cancelled Order", "CANCELLED_ORDER"),
                                        ("Line Rollover (Shift End)", "LINE_ROLLOVER"),
                                    ],
                                    value="BIKE_NO_SHOW",
                                    id="sel-return-reason",
                                )
                            with Horizontal(classes="stock-row"):
                                yield Input(placeholder="⚡ Rapid scan returned plate QR [Enter to add]...", id="input-return-single", classes="stock-input-field")
                                yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-return-staged", classes="stock-staged-badge")
                            yield TextArea(id="text-return-bulk", classes="stock-textarea")
                            with Horizontal(classes="stock-row"):
                                yield Button("↩️ Record Returned Plates", variant="warning", id="btn-save-return")
                                yield Button("🧹 Clear Returns", variant="default", id="btn-clear-return")

                    # SUB-TAB 6: 🎯 Scheduled Target & Opening Balances
                    with TabPane("🎯 Scheduled & Opening", id="subtab-scheduled"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold cyan]🎯 Shift Target & Physical Opening Balance[/bold cyan]  │  "
                                "[dim]Operational target does not alter physical inventory. Opening is physical stock at 06:00.[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Static("[bold white]Scheduled Target (Total):[/bold white] ", classes="stock-label-fixed")
                                yield Input(value="0", placeholder="e.g. 500", id="input-sched-target", classes="stock-input-field")
                            yield Static("[bold green]📊 Live Status:[/bold green] Target: 0  │  Installed: 0  │  Backlog: 0", id="lbl-sched-status-card")

                            yield Static("[bold green]Physical Opening Stock Balances (Safe Room at 06:00):[/bold green]")
                            with Horizontal(classes="stock-row"):
                                yield Static("[bold white]PRIVATE (Yellow):[/bold white] ", classes="stock-label-fixed")
                                yield Input(value="0", placeholder="Opening PMO count", id="input-open-pmo", classes="stock-input-field")
                                yield Static("[bold white]PUBLIC (White):[/bold white] ", classes="stock-label-fixed")
                                yield Input(value="0", placeholder="Opening PSV count", id="input-open-psv", classes="stock-input-field")
                                yield Static("[bold cyan]Total Opening: 0[/bold cyan]", id="lbl-open-total", classes="stock-staged-badge")

                            yield Static("[bold white]Shift Remarks & Handover Notes:[/bold white]")
                            yield TextArea(id="text-stock-remarks", classes="stock-textarea")
                            with Horizontal(classes="stock-row"):
                                yield Button("💾 Save Target, Balances & Remarks", variant="success", id="btn-save-scheduled")
                                yield Button("🔄 Auto-Carry Previous Shift Closing Stock", variant="primary", id="btn-autofill-opening")

                    # SUB-TAB 7: 🔒 Safe Stock Taking Audit
                    with TabPane("🔒 Safe Stock Taking", id="subtab-stocktake"):
                        with Vertical(classes="stock-subtab-container"):
                            yield Static(
                                "[bold cyan]🔒 Monthly Physical Stock Taking & Safe Room Audit[/bold cyan]  │  "
                                "[dim]Paste ~1,300 plates to audit safe storage, link hardware serials & reconcile with book stock[/dim]",
                                classes="stock-subtab-header",
                            )
                            with Horizontal(classes="stock-row"):
                                yield Input(placeholder="⚡ Scan safe room plate QR [Enter to add]...", id="input-stocktake-single", classes="stock-input-field")
                                yield Static("[bold cyan]Audit Scans: 0 plates[/bold cyan]", id="lbl-stocktake-staged", classes="stock-staged-badge")
                            yield Static("[dim]Paste multi-line Excel column of physical plates audited in Safe Room (~1,300 plates):[/dim]", classes="stock-table-title")
                            yield TextArea(id="text-stocktake-bulk", classes="stock-textarea")
                            with Horizontal(classes="stock-row"):
                                yield Button("🔒 Run Safe Room Stock Audit", variant="primary", id="btn-save-stocktake")
                                yield Button("📥 Export Unregistered Kits (CSV)", variant="warning", id="btn-export-unregistered-stocktake")
                                yield Button("📦 Sync ITMS Installation Kits", variant="success", id="btn-sync-itms-stocktake")
                                yield Button("🧹 Clear Scans", variant="default", id="btn-clear-stocktake")
                            with Horizontal(classes="stock-row"):
                                yield Static("[bold white]Manual Physical Count:[/bold white] ", classes="stock-label-fixed")
                                yield Input(value="0", placeholder="e.g. 1300", id="input-stocktake-manual-count", classes="stock-input-field")
                                yield Button("💾 Set Physical Count", variant="success", id="btn-stocktake-set-manual")
                            yield Static(id="lbl-stocktake-summary")
                            yield Static("[bold white]📋 Audited Physical Stock (Hardware Linking & Verification):[/bold white]", classes="stock-table-title")
                            yield DataTable(id="table-stocktake-results")

            # RIGHT PANEL (35% width): Persistent Live Kit Inspector
            yield InspectorPane(id="inspector-stock", classes="inspector-panel")

    def on_mount(self) -> None:
        # Initialize tables
        tbl_dispatch = self.query_one("#table-stock-dispatch", DataTable)
        tbl_dispatch.add_columns("#", "Time", "Plate", "Class", "Kit Code", "GPS Tracker", "Status", "Operator")
        tbl_dispatch.cursor_type = "row"

        tbl_recon = self.query_one("#table-stock-report", DataTable)
        tbl_recon.add_columns("DESCRIPTION", "PRIVATE (PMO)", "PUBLIC (PSV)", "Total Combined", "REMARKS / Formula Note")
        tbl_recon.cursor_type = "row"

        tbl_deliv = self.query_one("#table-stock-deliv-notes", DataTable)
        tbl_deliv.add_columns("Delivery Ref", "Category", "Plates", "Photo", "Sample", "Logged At")
        tbl_deliv.cursor_type = "row"

        tbl_stocktake = self.query_one("#table-stocktake-results", DataTable)
        tbl_stocktake.add_columns("#", "Plate", "Status", "Kit Code", "GPS Tracker", "Front BLE", "Rear BLE")
        tbl_stocktake.cursor_type = "row"

        self.action_refresh_stock()


    def action_refresh_stock(self) -> None:
        """Fetches latest stock reconciliation and updates all tables and inspector."""
        from core.services import stock_monitoring_service
        try:
            recon = stock_monitoring_service.compute_daily_reconciliation(self.target_date_suffix)
            self._cached_recon = recon
            self._render_header(recon)
            self._render_recon_table(recon)
            self._render_dispatch_table()
            self._render_delivery_notes_table()
            self._load_inputs(recon)

            from core.models import InstallationKit
            new_cnt = InstallationKit.objects.filter(status__iexact="New").count()
            try:
                self.query_one("#lbl-stock-ready-badge", Static).update(
                    f"[bold green]📦 Safe Room Ready: {new_cnt:,} kits ('New')[/bold green]"
                )
            except Exception:
                pass
        except Exception as exc:
            self.notify(f"Stock refresh error: {exc}", severity="error")

    def _render_header(self, r: Dict[str, Any]) -> None:
        fmt_date = r.get("formatted_date", "")
        suf = r.get("work_date_suffix", "")
        wh = r.get("storage_bond_name") or r.get("warehouse_name") or "AGM SPIRO/8/2"
        from core.models import InstallationKit, StockDispatchScan
        new_cnt = InstallationKit.objects.filter(status__iexact="New").count()
        today_dispatched = StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).count()

        self.query_one("#stock-header-banner", Static).update(
            f"[bold cyan]═══ 📦 ITMS BOND PHYSICAL STOCK & RECONCILIATION MANAGER ═══[/bold cyan]\n"
            f"[bold white]Shift Work Date:[/bold white] [bold yellow]{fmt_date}[/bold yellow] ([cyan]{suf}[/cyan])  │  "
            f"[bold white]Warehouse / Bond:[/bold white] [bold white]{wh}[/bold white]  │  "
            f"[bold green]Safe Room Ready:[/bold green] [bold green]{new_cnt:,} kits[/bold green]  │  "
            f"[bold yellow]Dispatched Today:[/bold yellow] [bold yellow]{today_dispatched:,} kits[/bold yellow]"
        )

    def _render_dispatch_table(self) -> None:
        """Populates the real-time live dispatch table for today."""
        table = self.query_one("#table-stock-dispatch", DataTable)
        table.clear()

        scans = StockDispatchScan.objects.filter(
            work_date_suffix=self.target_date_suffix
        ).order_by("-dispatched_at")

        dispatched_count = scans.count()
        try:
            self.query_one("#lbl-stock-dispatch-count", Static).update(
                f"[bold green]✓ Dispatched: {dispatched_count:,} kits[/bold green]"
            )
        except Exception:
            pass

        if not scans.exists():
            table.add_row("—", "—", "No plates dispatched today yet. Scan barcode or paste Excel column to begin.", "—", "—", "—", "—", "—")
            return

        from core.models import InstallationKit
        plate_numbers = [s.registration_number for s in scans[:100]]
        kit_map = {k.registration_number: k for k in InstallationKit.objects.filter(registration_number__in=plate_numbers)}

        for idx, scan in enumerate(scans):
            plate = scan.registration_number
            cat = scan.plate_category
            cat_badge = "[bold white on dark_blue] PSV [/]" if cat == "PSV" else "[bold black on gold1] PMO [/]"
            time_str = scan.dispatched_at.strftime("%H:%M:%S") if scan.dispatched_at else "—"
            kit = kit_map.get(plate)
            kit_code = kit.kit_code if kit else "—"
            gps = (getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or getattr(kit, "imei", "") or "—") if kit else "—"
            status = "[bold green]ON LINE[/bold green]" if scan.status == StockDispatchScan.Status.ON_LINE_ACTIVE else escape(scan.status)
            operator = escape(getattr(scan, "operator_name", "") or getattr(scan, "operator_username", "Operator"))

            table.add_row(
                f"#{idx + 1}",
                f"[dim]{time_str}[/dim]",
                f"[bold yellow]{escape(plate)}[/bold yellow]",
                cat_badge,
                f"[bold cyan]{escape(kit_code)}[/bold cyan]",
                f"[dim]{escape(gps)}[/dim]",
                status,
                f"[dim]{operator}[/dim]",
                key=plate,
            )

    def _render_recon_table(self, r: Dict[str, Any]) -> None:
        table = self.query_one("#table-stock-report", DataTable)
        table.clear()

        rows = r.get("report_table", {}).get("rows", [])
        for row in rows:
            metric = row.get("metric", "")
            pmo = row.get("pmo", 0)
            psv = row.get("psv", 0)
            tot = row.get("total", 0)
            note = row.get("note", "")

            if "Closing Balance" in metric:
                m_str = f"[bold green]{metric}[/bold green]"
                y_str = f"[bold green]{pmo:,}[/bold green]" if isinstance(pmo, int) else f"[bold green]{pmo}[/bold green]"
                p_str = f"[bold green]{psv:,}[/bold green]" if isinstance(psv, int) else f"[bold green]{psv}[/bold green]"
                t_str = f"[bold white on dark_green] {tot:,} [/bold white on dark_green]" if isinstance(tot, int) else f"[bold white on dark_green] {tot} [/bold white on dark_green]"
            elif "SCHEDULED" in metric or "Scheduled" in metric:
                m_str = f"[bold cyan]{metric}[/bold cyan]"
                y_str = f"[bold cyan]{pmo:,}[/bold cyan]" if isinstance(pmo, int) else f"[bold cyan]{pmo}[/bold cyan]"
                p_str = f"[bold cyan]{psv:,}[/bold cyan]" if isinstance(psv, int) else f"[bold cyan]{psv}[/bold cyan]"
                t_str = f"[bold cyan]{tot:,}[/bold cyan]" if isinstance(tot, int) else f"[bold cyan]{tot}[/bold cyan]"
            elif "Installed" in metric:
                m_str = f"[bold yellow]{metric}[/bold yellow]"
                y_str = f"[bold yellow]{pmo:,}[/bold yellow]" if isinstance(pmo, int) else f"[bold yellow]{pmo}[/bold yellow]"
                p_str = f"[bold yellow]{psv:,}[/bold yellow]" if isinstance(psv, int) else f"[bold yellow]{psv}[/bold yellow]"
                t_str = f"[bold yellow]{tot:,}[/bold yellow]" if isinstance(tot, int) else f"[bold yellow]{tot}[/bold yellow]"
            else:
                m_str = f"[bold white]{metric}[/bold white]"
                y_str = f"{pmo:,}" if isinstance(pmo, int) else str(pmo)
                p_str = f"{psv:,}" if isinstance(psv, int) else str(psv)
                t_str = f"[bold white]{tot:,}[/bold white]" if isinstance(tot, int) else str(tot)

            table.add_row(m_str, y_str, p_str, t_str, f"[dim]{note}[/dim]")

        floor = r.get("floor_operations", {})
        unalloc = floor.get("unallocated_discrepancy", 0)
        unalloc_style = "[bold red]" if unalloc > 0 else "[bold green]"
        self.query_one("#stock-floor-summary", Static).update(
            f" [b]Floor Operations:[/b] Dispatched: [cyan]{floor.get('dispatched_count', 0)}[/cyan]  │  "
            f"Returned: [yellow]{floor.get('returned_count', 0)}[/yellow]  │  "
            f"Net on Line: [white]{floor.get('net_dispatched', 0)}[/white]  │  "
            f"Pending Orders: [gold1]{floor.get('itms_pending_count', 0)}[/gold1]  │  "
            f"{unalloc_style}⚠️ Unallocated Discrepancy: {unalloc} plates{unalloc_style}"
        )

    def _render_delivery_notes_table(self) -> None:
        from core.services import stock_monitoring_service
        try:
            table = self.query_one("#table-stock-deliv-notes", DataTable)
            table.clear()
            notes = stock_monitoring_service.get_delivery_notes_for_date(self.target_date_suffix)
            if not notes:
                table.add_row("No delivery notes logged for this shift", "—", "—", "—", "—", "—")
                return
            for n in notes:
                cat_badge = "[bold white on dark_blue] PSV [/]" if n["plate_category"] == "PSV" else "[bold black on gold1] PMO [/]"
                has_photo = "[bold green]✓ Attached[/bold green]" if n.get("has_image") else "[dim]No photo[/dim]"
                plates = n.get("plates", [])
                sample = ", ".join(plates[:3]) + (f" (+{len(plates)-3} more)" if len(plates) > 3 else "")
                table.add_row(
                    f"[bold green]{n['delivery_number']}[/bold green]",
                    cat_badge,
                    f"[bold cyan]{n['total_plates_count']:,}[/bold cyan]",
                    has_photo,
                    f"[dim]{sample}[/dim]",
                    f"[dim]{n['created_at']}[/dim]",
                    key=str(n["id"]),
                )
        except Exception:
            pass

    def _load_inputs(self, r: Dict[str, Any]) -> None:
        try:
            work_d = r.get("work_date")
            ledger = DailyStockLedger.objects.filter(work_date=work_d).first()
            if ledger:
                target_val = ledger.scheduled_total if ledger.scheduled_total > 0 else (ledger.scheduled_psv + ledger.scheduled_pmo)
                try:
                    self.query_one("#input-sched-target", Input).value = str(target_val)
                    self.query_one("#input-open-pmo", Input).value = str(ledger.opening_balance_pmo)
                    self.query_one("#input-open-psv", Input).value = str(ledger.opening_balance_psv)
                    tot_open = ledger.opening_balance_pmo + ledger.opening_balance_psv
                    self.query_one("#lbl-open-total", Static).update(f"[bold cyan]Total Opening: {tot_open:,}[/bold cyan]")
                    self.query_one("#text-stock-remarks", TextArea).text = ledger.notes or ""
                except Exception:
                    pass

                sched_sum = r.get("scheduled_summary", {})
                inst_tot = sched_sum.get("installed_total", 0)
                perf = sched_sum.get("daily_performance_pct", 0.0)
                backlog = sched_sum.get("backlog_level", 0)
                try:
                    self.query_one("#lbl-sched-status-card", Static).update(
                        f"[bold green]📊 Live Status:[/bold green] Target: [bold cyan]{target_val:,}[/bold cyan]  │  "
                        f"Installed: [bold yellow]{inst_tot:,}[/bold yellow]  │  "
                        f"Performance: [bold magenta]{perf}%[/bold magenta]  │  "
                        f"Backlog: [bold red]{backlog:,} remaining[/bold red]"
                    )
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from core.services import stock_monitoring_service
            auto_ref = stock_monitoring_service.generate_delivery_note_reference(self.target_date_suffix)
            deliv_inp = self.query_one("#input-deliv-number", Input)
            deliv_inp.placeholder = f"Auto: {auto_ref}"
            if not deliv_inp.value.strip():
                deliv_inp.value = auto_ref
        except Exception:
            pass

    # ------------------------------------------------------------------------
    # Event Handlers & Instant Stock Verification
    # ------------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Rapid USB barcode scanner handler with instant verification."""
        inp_id = event.input.id
        raw_val = event.value.strip()

        if not raw_val:
            return

        if inp_id == "input-stock-dispatch-single":
            self._handle_instant_dispatch_scan(raw_val, event.input)
        elif inp_id == "input-deliv-single":
            self._append_to_textarea("#text-deliv-bulk", "#lbl-deliv-staged", raw_val, event.input)
        elif inp_id == "input-transfer-single":
            self._append_to_textarea("#text-transfer-bulk", "#lbl-transfer-staged", raw_val, event.input)
        elif inp_id == "input-return-single":
            self._append_to_textarea("#text-return-bulk", "#lbl-return-staged", raw_val, event.input)
        elif inp_id == "input-stocktake-single":
            self._append_to_textarea("#text-stocktake-bulk", "#lbl-stocktake-staged", raw_val, event.input)

    def _append_to_textarea(self, text_area_id: str, badge_id: str, raw_val: str, input_widget: Input) -> None:
        from core.services import stock_monitoring_service
        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.notify(f"⚠️ Invalid plate format: '{raw_val}'", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        text_area = self.query_one(text_area_id, TextArea)
        curr = text_area.text
        existing, _, _ = stock_monitoring_service.parse_plate_input_with_stats(curr)
        if plate in existing:
            self.notify(f"⚠️ Duplicate ignored: Plate {plate} is already staged!", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        text_area.text = f"{curr}\n{plate}".strip()
        input_widget.value = ""
        input_widget.focus()
        staged_count = len(existing) + 1
        try:
            self.query_one(badge_id, Static).update(f"[bold green]Staged: {staged_count} plates[/bold green]")
        except Exception:
            pass
        self.notify(f"✓ Added {plate} (Total Staged: {staged_count})", severity="information")

    def _handle_instant_dispatch_scan(self, raw_val: str, input_widget: Input) -> None:
        """
        Instant sub-50ms stock verification for barcode scanners.
        Validates the plate against stock immediately upon Enter.
        If valid: records dispatch, prepends to table, and shows kit in inspector.
        If invalid: blocks dispatch, alerts operator, and shows explanation.
        """
        from core.services import stock_monitoring_service
        from core.services.kit_provisioning_service import verify_scanned_kit_stock

        # If user pasted multiple lines into single input, route cleanly to batch handler
        if "\n" in raw_val or "\r" in raw_val or "\t" in raw_val:
            self._handle_batch_excel_dispatch(raw_val)
            input_widget.value = ""
            input_widget.focus()
            return

        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⚠️ INVALID FORMAT: '{raw_val}' [/bold white on dark_red] [red]Plate format must match e.g. UMA 338PZ or UMA338PZ[/red]"
            )
            input_widget.value = ""
            input_widget.focus()
            return

        # Check duplicate dispatch today
        already_dispatched = StockDispatchScan.objects.filter(
            work_date_suffix=self.target_date_suffix,
            registration_number=plate,
        ).exists()

        if already_dispatched:
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold black on gold1] ⚠️ ALREADY DISPATCHED: {plate} [/bold black on gold1] [yellow]This plate was already scanned and dispatched today![/yellow]"
            )
            self.notify(f"⚠️ Duplicate Scan: Plate {plate} was already dispatched today!", severity="warning")
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate)
            input_widget.value = ""
            input_widget.focus()
            return

        # Instant Stock Verification (Tier 1: Local DB, Tier 2: Live ITMS)
        cat_select = self.query_one("#sel-stock-dispatch-category", Select)
        category = str(cat_select.value or "PSV")

        check = verify_scanned_kit_stock(plate, check_itms_live=True)
        if isinstance(check, tuple):
            is_valid = bool(check[0])
            kit = check[1] if len(check) > 1 else None
            reason = check[2] if len(check) > 2 else ""
            source = getattr(check, "source", "local_db")
        elif isinstance(check, dict):
            is_valid = bool(check.get("is_valid") or check.get("valid"))
            kit = check.get("kit")
            reason = check.get("error") or check.get("reason") or ""
            source = check.get("source", "local_db")
        else:
            is_valid = bool(getattr(check, "is_valid", False))
            kit = getattr(check, "kit", None)
            reason = getattr(check, "reason", "")
            source = getattr(check, "source", "local_db")

        # Check if already installed / archived in ITMS
        status_str = getattr(check, "status", "")
        if not status_str and isinstance(check, dict):
            status_str = check.get("status", "")
        is_already_installed = (status_str == "ALREADY_INSTALLED") or ("already installed" in reason.lower()) or ("already been installed" in reason.lower())

        if is_already_installed:
            err_msg = reason or f"Plate {plate} is already installed in ITMS Archive!"
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⛔ ALREADY INSTALLED: {plate} [/bold white on dark_red] [bold red]{escape(err_msg)} - DO NOT FIT TO BIKE! Set physical box aside![/bold red]"
            )
            self.notify(f"⛔ REJECTED: {plate} is ALREADY INSTALLED in ITMS! Set aside!", severity="error")
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                plate=plate,
                kit=kit,
                error_message=err_msg,
                verification_result={"is_valid": False, "reason": err_msg, "kit": kit},
            )
            if plate not in self._last_blocked_dispatch:
                self._last_blocked_dispatch.append(plate)
            input_widget.value = ""
            input_widget.focus()
            return

        # Check if kit is NOT on stock in Safe Room / ITMS
        if not is_valid:
            err_msg = reason or f"Plate {plate} is NOT on stock in Safe Room / ITMS!"
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⛔ NOT ON STOCK (SET ASIDE): {plate} [/bold white on dark_red] [bold red]{escape(err_msg)} — DO NOT DISPATCH! Set physical box aside and swap with a kit on stock. Hand plate to ITMS Stock Transfer Officer.[/bold red]"
            )
            self.notify(f"⛔ SET ASIDE: {plate} is NOT on stock! Set box aside & swap.", severity="error")
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                plate=plate,
                kit=kit,
                error_message=f"NOT ON STOCK: {err_msg} (Set aside awaiting ITMS Stock Transfer Officer)",
                verification_result={"is_valid": False, "reason": err_msg, "kit": kit},
            )
            if plate not in self._last_blocked_dispatch:
                self._last_blocked_dispatch.append(plate)
            stock_monitoring_service.record_blocked_plates([plate], self.target_date_suffix)
            input_widget.value = ""
            input_widget.focus()
            return

        # Kit IS verified on stock! Physical kit is being dispatched and fitted to a bike!
        try:
            op_name = str(getattr(self.app, "current_user", "") or "Operator")
        except Exception:
            op_name = "Operator"

        from core.services import bond_service
        active_bond = bond_service.get_active_bond()
        wh_name = active_bond.get("name", "AGM SPIRO")
        b_code = active_bond.get("code", "AGM")

        # Record dispatch scan on line
        StockDispatchScan.objects.create(
            work_date_suffix=self.target_date_suffix,
            registration_number=plate,
            plate_category=category,
            status=StockDispatchScan.Status.ON_LINE_ACTIVE,
            bond_code=b_code,
            operator_name=op_name,
            dispatched_at=timezone.now(),
            notes="Dispatched on stock.",
        )

        # Clear input field immediately so operator can scan next plate without delay
        input_widget.value = ""
        input_widget.focus()

        synced_note = " (Synced live from ITMS)" if source == "itms_live" else ""
        self.query_one("#lbl-stock-instant-feedback", Static).update(
            f"[bold white on dark_green] ✓ ON STOCK & DISPATCHED: {plate} [/bold white on dark_green] [bold green]Kit verified on stock and dispatched to line! Clean to proceed.{synced_note}[/bold green]"
        )
        self.notify(f"✓ Dispatched {plate} ({category})", severity="information")

        # Refresh tables and select new item in inspector
        self._render_dispatch_table()
        self.action_refresh_stock()
        self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate=plate, kit=kit)

    def _handle_batch_excel_dispatch(self, raw_input: Optional[str] = None) -> None:
        """
        Handles multi-line Excel column paste (no commas required).
        Validates all plates against stock and ITMS:
        - Strictly blocks plates that are already installed / archived in ITMS (double-fitting protection).
        - Plates verified on stock are cleanly dispatched to the line.
        - Any plates NOT on stock are retained in the text area so the operator can set
          those physical boxes aside and swap them with valid kits from the 1,300 available in stock.
        - Adds non-stock plates to the Blocked List for export to the ITMS Stock Transfer Officer.
        """
        from core.services import stock_monitoring_service, kit_provisioning_service, bond_service

        bulk_text = raw_input or self.query_one("#text-stock-dispatch-bulk", TextArea).text
        single_text = self.query_one("#input-stock-dispatch-single", Input).value
        combined = f"{bulk_text}\n{single_text}".strip()
        cat_select = self.query_one("#sel-stock-dispatch-category", Select)
        category = str(cat_select.value or "PSV")

        if not combined:
            self.notify("Please paste an Excel column or scan plate numbers first.", severity="warning")
            return

        try:
            clean_plates, dup_count, dup_plates = stock_monitoring_service.parse_plate_input_with_stats(combined)
            if not clean_plates:
                self.notify("No valid license plate numbers found in input.", severity="warning")
                return

            active_bond = bond_service.get_active_bond()
            wh_name = active_bond.get("name", "AGM SPIRO")
            b_code = active_bond.get("code", "AGM")

            # Check stock & ITMS readiness
            verify_res = kit_provisioning_service.verify_scanned_kits_stock(
                clean_plates,
                check_itms_live=True,
                facility_name=wh_name,
            )
            verified = set(verify_res.get("verified_plates", []))
            already_installed = list(verify_res.get("already_installed", []))
            rejected = [p for p in clean_plates if p not in verified and p not in already_installed]

            # All non-stock or already installed kits are blocked and set aside
            blocked = list(already_installed) + list(rejected)
            self._last_blocked_dispatch = blocked
            if blocked:
                stock_monitoring_service.record_blocked_plates(blocked, self.target_date_suffix)

            # Check duplicate dispatches for today among verified on-stock plates
            existing_dispatches = set(
                StockDispatchScan.objects.filter(
                    work_date_suffix=self.target_date_suffix,
                    registration_number__in=verified,
                ).values_list("registration_number", flat=True)
            )

            to_dispatch = [p for p in clean_plates if p in verified and p not in existing_dispatches]
            already_cnt = len([p for p in clean_plates if p in verified and p in existing_dispatches])

            # Record dispatch scans ONLY for kits verified on stock
            try:
                op_name = str(getattr(self.app, "current_user", "") or "Operator")
            except Exception:
                op_name = "Operator"

            work_d, _ = stock_monitoring_service.resolve_date_and_suffix(self.target_date_suffix)
            new_scans = []
            for p in to_dispatch:
                new_scans.append(
                    StockDispatchScan(
                        registration_number=p,
                        plate_category=category,
                        work_date=work_d,
                        work_date_suffix=self.target_date_suffix,
                        bond_code=b_code,
                        operator_name=op_name,
                        status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                        notes="Dispatched on stock.",
                    )
                )

            if new_scans:
                StockDispatchScan.objects.bulk_create(new_scans)

            new_cnt = len(new_scans)

            # Visual feedback and UI state updates:
            # Leave non-stock / blocked plates in the text area so operator can set physical boxes aside!
            if blocked:
                self.query_one("#text-stock-dispatch-bulk", TextArea).text = "\n".join(blocked)
                blocked_sample = ", ".join(blocked[:5])
                if len(blocked) > 5:
                    blocked_sample += f" (+{len(blocked) - 5} more)"

                self.query_one("#lbl-stock-instant-feedback", Static).update(
                    f"[bold white on dark_red] ⛔ {len(blocked)} KIT(S) NOT ON STOCK (SET ASIDE): [/bold white on dark_red] [bold red]{blocked_sample} — Dispatched {new_cnt} on stock. Set {len(blocked)} physical boxes aside and swap with kits on stock! Click 'Export Blocked Kits' for ITMS Stock Officer.[/bold red]"
                )
                self.notify(f"⚠️ Dispatched {new_cnt} on-stock kits. {len(blocked)} non-stock kits left in text box to set aside & swap.", severity="warning")
                self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                    plate=blocked[0],
                    error_message=f"{len(blocked)} kit(s) NOT ON STOCK (Set aside awaiting ITMS Stock Transfer Officer)",
                )
            else:
                self.query_one("#text-stock-dispatch-bulk", TextArea).text = ""
                self.query_one("#lbl-stock-instant-feedback", Static).update(
                    f"[bold white on dark_green] ✓ EXCEL BATCH SUCCESS: [/bold white on dark_green] [green]All {new_cnt} plates verified on stock and dispatched to line![/green]"
                )
                self.notify(f"✓ Dispatched all {new_cnt} {category} plates from Excel.", severity="information")

                if to_dispatch:
                    from core.models import InstallationKit
                    first_kit = InstallationKit.objects.filter(registration_number=to_dispatch[0]).first()
                    self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                        plate=to_dispatch[0],
                        kit=first_kit,
                    )

            self.query_one("#input-stock-dispatch-single", Input).value = ""
            try:
                today_cnt = StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).count()
                self.query_one("#lbl-stock-dispatch-count", Static).update(f"[bold green]✓ Dispatched: {today_cnt:,} kits[/bold green]")
            except Exception:
                pass

            self._render_dispatch_table()
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error processing Excel dispatch: {exc}", severity="error")

    def _handle_export_blocked_dispatch(self) -> None:
        from core.services import stock_monitoring_service
        stored = stock_monitoring_service.get_blocked_plates_for_date(self.target_date_suffix)
        plates_to_export = list(set(self._last_blocked_dispatch + stored))
        if not plates_to_export:
            bulk_text = self.query_one("#text-stock-dispatch-bulk", TextArea).text.strip()
            if bulk_text:
                parsed_bulk, _, _ = stock_monitoring_service.parse_plate_input_with_stats(bulk_text)
                plates_to_export.extend(parsed_bulk)

        if not plates_to_export:
            self.notify("No blocked kits to export. All scanned plates were clean or empty.", severity="warning")
            return

        try:
            file_path, filename, cnt = stock_monitoring_service.export_blocked_kits_csv(
                blocked_plates=plates_to_export,
                target_date_suffix=self.target_date_suffix,
            )
            self.notify(f"✓ Exported {cnt} blocked kits to {filename}! Hand to ITMS Stock Transfer Officer.", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting blocked kits: {exc}", severity="error")


    def _handle_preverify_stock_only(self) -> None:
        """Pre-checks all plates in the text area without recording dispatches."""
        from core.services import stock_monitoring_service
        from core.services.kit_provisioning_service import verify_scanned_kits_stock

        bulk_text = self.query_one("#text-stock-dispatch-bulk", TextArea).text
        single_text = self.query_one("#input-stock-dispatch-single", Input).value
        combined = f"{bulk_text}\n{single_text}".strip()

        plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(combined)
        if not plates:
            self.notify("No plates to verify. Scan or paste plates first.", severity="warning")
            return

        self.notify(f"🔍 Pre-verifying {len(plates)} plate(s) against warehouse stock & ITMS...", severity="information")
        results = verify_scanned_kits_stock(plates, check_itms_live=True)
        valid_cnt = len(results.get("verified_plates", []))
        invalid = results.get("rejected_not_on_stock", [])
        already_inst = results.get("already_installed", [])

        if not invalid and not already_inst:
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_green] ✓ 100% ON STOCK: [/bold white on dark_green] [green]All {valid_cnt} plate(s) verified on stock. Ready to dispatch![/green]"
            )
            self.notify(f"✓ All {valid_cnt} plates verified on stock!", severity="information")
        else:
            err_parts = []
            if invalid:
                err_parts.append(f"{len(invalid)} not on stock ({', '.join(invalid[:4])})")
            if already_inst:
                err_parts.append(f"{len(already_inst)} already installed ({', '.join(already_inst[:4])})")
            summary_err = "; ".join(err_parts)
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⚠️ BLOCKED KIT(S): [/bold white on dark_red] [red]{escape(summary_err)}[/red]"
            )
            self.notify(f"⚠️ {summary_err}", severity="warning")
            first_rejected = (invalid or already_inst)[0]
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                plate=first_rejected,
                error_message=summary_err,
            )

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Updates counter in real-time as operator pastes or types."""
        from core.services import stock_monitoring_service
        t_id = event.text_area.id
        if t_id == "text-stock-dispatch-bulk":
            plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(event.text_area.text)
            cnt = len(plates)
            try:
                if cnt > 0:
                    self.query_one("#lbl-stock-dispatch-count", Static).update(
                        f"[bold cyan]Pasted: {cnt} plates[/bold cyan]"
                    )
                else:
                    today_cnt = StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).count()
                    self.query_one("#lbl-stock-dispatch-count", Static).update(
                        f"[bold green]✓ Dispatched: {today_cnt:,} kits[/bold green]"
                    )
            except Exception:
                pass
        elif t_id == "text-deliv-bulk":
            plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(event.text_area.text)
            try:
                self.query_one("#lbl-deliv-staged", Static).update(f"[bold green]📦 Staged: {len(plates)} plates[/bold green]")
            except Exception:
                pass
        elif t_id == "text-stocktake-bulk":
            plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(event.text_area.text)
            try:
                self.query_one("#lbl-stocktake-staged", Static).update(f"[bold cyan]Audit Scans: {len(plates)} plates[/bold cyan]")
            except Exception:
                pass

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Live inspector updates when navigating any table in the Stock workspace."""
        if event.data_table.id == "table-stock-dispatch":
            row_key = getattr(event.row_key, "value", str(event.row_key)) if event.row_key else None
            if row_key and row_key != "—":
                self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate=row_key)
        elif event.data_table.id == "table-stock-deliv-notes":
            row_key = getattr(event.row_key, "value", str(event.row_key)) if event.row_key else None
            if row_key and row_key.isdigit():
                note = StockDelivery.objects.filter(id=int(row_key)).first()
                if note:
                    plates = note.items.values_list("registration_number", flat=True)
                    sample_plate = plates[0] if plates else None
                    if sample_plate:
                        self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate=sample_plate)
        elif event.data_table.id == "table-stocktake-results":
            row_key = getattr(event.row_key, "value", str(event.row_key)) if event.row_key else None
            if row_key and row_key != "—":
                self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate=row_key)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-process-excel-dispatch":
            self._handle_batch_excel_dispatch()
        elif bid == "btn-export-blocked-dispatch":
            self._handle_export_blocked_dispatch()
        elif bid == "btn-preverify-stock":
            self._handle_preverify_stock_only()
        elif bid == "btn-clear-dispatch-pane":
            self.query_one("#text-stock-dispatch-bulk", TextArea).text = ""
            self.query_one("#input-stock-dispatch-single", Input).value = ""
            try:
                today_cnt = StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).count()
                self.query_one("#lbl-stock-dispatch-count", Static).update(f"[bold green]✓ Dispatched: {today_cnt:,} kits[/bold green]")
            except Exception:
                pass
        elif bid in ("btn-sync-stock-kits-tab1", "btn-sync-stock-kits-recon", "btn-sync-itms-stocktake"):
            self._handle_sync_stock_kits()
        elif bid == "btn-save-delivery":
            self._handle_save_delivery()
        elif bid == "btn-clear-deliv":
            self.query_one("#text-deliv-bulk", TextArea).text = ""
            self.query_one("#input-deliv-single", Input).value = ""
        elif bid == "btn-save-transfer":
            self._handle_save_transfer()
        elif bid == "btn-clear-transfer":
            self.query_one("#text-transfer-bulk", TextArea).text = ""
            self.query_one("#input-transfer-single", Input).value = ""
        elif bid == "btn-save-return":
            self._handle_save_return()
        elif bid == "btn-clear-return":
            self.query_one("#text-return-bulk", TextArea).text = ""
            self.query_one("#input-return-single", Input).value = ""
        elif bid == "btn-save-scheduled":
            self._handle_save_scheduled()
        elif bid == "btn-autofill-opening":
            self._handle_autofill_opening()
        elif bid == "btn-save-stocktake":
            self._handle_save_stocktake()
        elif bid == "btn-export-unregistered-stocktake":
            self._handle_export_unregistered_stocktake()
        elif bid == "btn-clear-stocktake":
            self.query_one("#text-stocktake-bulk", TextArea).text = ""
            self.query_one("#input-stocktake-single", Input).value = ""
        elif bid == "btn-stocktake-set-manual":
            self._handle_set_manual_physical_count()


    @work(thread=True)
    def _handle_sync_stock_kits(self) -> None:
        if self._is_syncing_kits:
            self.app.call_from_thread(self.notify, "Kit sync is already in progress...", severity="warning")
            return
        self._is_syncing_kits = True

        def _update_btn_state(text: str, disabled: bool):
            for bid in ("#btn-sync-stock-kits-tab1", "#btn-sync-stock-kits-recon", "#btn-sync-itms-stocktake"):
                try:
                    btn = self.query_one(bid, Button)
                    btn.label = text
                    btn.disabled = disabled
                except Exception:
                    pass

        self.app.call_from_thread(_update_btn_state, "⏳ Syncing Kits...", True)
        self.app.call_from_thread(self.notify, "🔄 Synchronizing installation kits from ITMS...")

        from core.services import kit_provisioning_service

        def _on_progress(msg: str):
            if hasattr(self.app, "log_message"):
                self.app.call_from_thread(self.app.log_message, msg, level="ITMS")

        try:
            res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=None,
                sync_itms=True,
                max_pages=35,
                log_callback=_on_progress,
            )
            count = res.get("new_kits_ready_count", 0)
            wh = res.get("warehouse_facility", "Warehouse Stock")
            itms_cnt = res.get("itms_kits_synced", 0)
            created = res.get("kits_created", 0)
            updated = res.get("kits_updated", 0)
            self.app.call_from_thread(
                self.notify,
                f"✓ Synced kits: {itms_cnt} from ITMS ({created} new, {updated} updated). {count} ready in {wh}.",
                severity="information",
                timeout=8,
            )
            self.app.call_from_thread(self.action_refresh_stock)
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Error syncing kits: {exc}", severity="error")
        finally:
            self._is_syncing_kits = False
            def _reset_btn_labels():
                for bid, default_lbl in (
                    ("#btn-sync-stock-kits-tab1", "📦 Sync ITMS Installation Kits"),
                    ("#btn-sync-stock-kits-recon", "📦 Sync & Prep Stock Kits"),
                    ("#btn-sync-itms-stocktake", "📦 Sync ITMS Installation Kits"),
                ):
                    try:
                        btn = self.query_one(bid, Button)
                        btn.label = default_lbl
                        btn.disabled = False
                    except Exception:
                        pass
            self.app.call_from_thread(_reset_btn_labels)

    def _handle_save_delivery(self) -> None:
        from core.services import stock_monitoring_service
        deliv_no = self.query_one("#input-deliv-number", Input).value.strip()
        supplier = self.query_one("#input-deliv-supplier", Input).value.strip()
        cat_select = self.query_one("#sel-deliv-category", Select)
        category = str(cat_select.value or "PSV")
        bulk_text = self.query_one("#text-deliv-bulk", TextArea).text.strip()
        single_text = self.query_one("#input-deliv-single", Input).value.strip()
        combined = f"{bulk_text}\n{single_text}".strip()
        auto_kits = self.query_one("#chk-deliv-kits", Checkbox).value

        if not combined:
            self.notify("Please scan or paste incoming plates for this delivery.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_delivery(
                delivery_number=deliv_no,
                supplier=supplier,
                plates=combined,
                plate_category=category,
                target_date_suffix=self.target_date_suffix,
                auto_create_kits=auto_kits,
            )
            p_cnt = res.get("plates_count", 0)
            k_cnt = res.get("created_kits_count", 0)
            self.notify(f"✓ Ingested delivery {res.get('delivery_number')} with {p_cnt} plates ({k_cnt} kits created). Background ITMS sync starting...", severity="information")
            self.query_one("#text-deliv-bulk", TextArea).text = ""
            self.query_one("#input-deliv-single", Input).value = ""
            self.action_refresh_stock()
            # Auto-trigger background ITMS sync so new inbound plates link to hardware details
            self._handle_sync_stock_kits()
        except Exception as exc:
            self.notify(f"Error saving delivery: {exc}", severity="error")

    def _handle_save_transfer(self) -> None:
        from core.services import stock_monitoring_service
        t_type = str(self.query_one("#sel-transfer-type", Select).value or "TRANSFER_IN")
        cat = str(self.query_one("#sel-transfer-category", Select).value or "PSV")
        bond_name = self.query_one("#input-transfer-bond", Input).value.strip() or "Other Bond"
        cnt_val = self.query_one("#input-transfer-count", Input).value.strip()
        bulk_text = self.query_one("#text-transfer-bulk", TextArea).text.strip()
        single_text = self.query_one("#input-transfer-single", Input).value.strip()
        combined = f"{bulk_text}\n{single_text}".strip()

        count = int(cnt_val) if cnt_val.isdigit() else 0
        if not count and not combined:
            self.notify("Please enter plates count or scan transfer plates.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_bond_transfer(
                transfer_type=t_type,
                plate_category=cat,
                plates_count=count,
                other_bond_name=bond_name,
                plates=combined if combined else None,
                target_date_suffix=self.target_date_suffix,
            )
            lbl = "Transfer In" if t_type == "TRANSFER_IN" else "Transfer Out"
            self.notify(f"Recorded {lbl} of {res.get('plates_count')} {cat} plates ({bond_name})!")
            self.query_one("#text-transfer-bulk", TextArea).text = ""
            self.query_one("#input-transfer-single", Input).value = ""
            self.query_one("#input-transfer-count", Input).value = ""
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving bond transfer: {exc}", severity="error")

    def _handle_save_return(self) -> None:
        from core.services import stock_monitoring_service
        cat = str(self.query_one("#sel-return-category", Select).value or "PSV")
        reason = str(self.query_one("#sel-return-reason", Select).value or "BIKE_NO_SHOW")
        bulk_text = self.query_one("#text-return-bulk", TextArea).text.strip()
        single_text = self.query_one("#input-return-single", Input).value.strip()
        combined = f"{bulk_text}\n{single_text}".strip()

        if not combined:
            self.notify("Please scan or paste returned plates first.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_return_scans(
                plates=combined,
                plate_category=cat,
                reason=reason,
                target_date_suffix=self.target_date_suffix,
            )
            new_cnt = res.get("newly_returned", 0)
            self.notify(f"✓ Recorded {new_cnt} returned {cat} plates.", severity="information")
            self.query_one("#text-return-bulk", TextArea).text = ""
            self.query_one("#input-return-single", Input).value = ""
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving return scans: {exc}", severity="error")

    def _handle_save_scheduled(self) -> None:
        from core.services import stock_monitoring_service
        try:
            target_raw = self.query_one("#input-sched-target", Input).value.strip()
            s_target = int(target_raw) if target_raw.isdigit() else 0
            o_pmo = int(self.query_one("#input-open-pmo", Input).value.strip() or 0)
            o_psv = int(self.query_one("#input-open-psv", Input).value.strip() or 0)
            remarks_text = self.query_one("#text-stock-remarks", TextArea).text.strip()

            stock_monitoring_service.set_scheduled_target(
                scheduled_target=s_target,
                target_date_suffix=self.target_date_suffix,
            )
            stock_monitoring_service.set_opening_balances(
                opening_pmo=o_pmo,
                opening_psv=o_psv,
                target_date_suffix=self.target_date_suffix,
            )
            if remarks_text:
                stock_monitoring_service.set_shift_remarks(
                    remarks=remarks_text,
                    target_date_suffix=self.target_date_suffix,
                )
            self.notify(f"✓ Saved scheduled target ({s_target:,}) & opening balances (Total: {o_pmo + o_psv:,})!", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error saving scheduled values: {exc}", severity="error")

    def _handle_autofill_opening(self) -> None:
        from core.services import stock_monitoring_service
        try:
            carried = stock_monitoring_service.get_previous_shift_closing_balances(self.target_date_suffix)
            pmo = carried.get("pmo", 0)
            psv = carried.get("psv", 0)
            tot = carried.get("total", 0)
            prev_suf = carried.get("previous_date_suffix") or "previous shift"

            self.query_one("#input-open-pmo", Input).value = str(pmo)
            self.query_one("#input-open-psv", Input).value = str(psv)
            self.query_one("#lbl-open-total", Static).update(f"[bold cyan]Total Opening: {tot:,}[/bold cyan]")

            if carried.get("source_ledger_exists"):
                self.notify(f"✓ Auto-carried previous shift ({prev_suf}) closing stock: {tot:,} plates (PMO: {pmo:,}, PSV: {psv:,})", severity="information")
            else:
                self.notify(f"⚠️ No previous shift closing stock found before {self.target_date_suffix}. Opening set to 0.", severity="warning")
        except Exception as exc:
            self.notify(f"Error auto-carrying previous closing stock: {exc}", severity="error")

    def _handle_save_stocktake(self) -> None:
        from core.services import stock_monitoring_service
        bulk_text = self.query_one("#text-stocktake-bulk", TextArea).text.strip()
        single_text = self.query_one("#input-stocktake-single", Input).value.strip()
        combined = f"{bulk_text}\n{single_text}".strip()

        if not combined:
            self.notify("Please scan or paste safe room physical plates first.", severity="warning")
            return

        try:
            res = stock_monitoring_service.record_stock_taking_audit(
                scanned_plates=combined,
                target_date_suffix=self.target_date_suffix,
            )
            scanned = res.get("total_scanned", 0)
            verified = res.get("verified_count", 0)
            unregistered = res.get("unregistered_count", 0)
            unreg_plates = res.get("unregistered_plates", [])
            self._last_unregistered_stocktake = unreg_plates

            book = res.get("book_closing_total", 0)
            variance = res.get("variance", 0)
            var_color = "green" if variance == 0 else ("yellow" if variance > 0 else "red")
            summary_text = (
                f"[bold cyan]Safe Audit Summary:[/bold cyan]  Physical Scanned: [bold white]{scanned:,}[/bold white]  │  "
                f"Verified on Stock: [bold green]{verified:,}[/bold green]  │  "
                f"Unregistered / Missing: [bold red]{unregistered:,}[/bold red]  │  "
                f"Book Closing: [bold white]{book:,}[/bold white]  │  "
                f"Variance: [bold {var_color}]{variance:+d}[/bold {var_color}]"
            )
            try:
                self.query_one("#lbl-stocktake-summary", Static).update(summary_text)
            except Exception:
                pass

            # Populate DataTable with verified hardware profiles and unregistered plates
            tbl = self.query_one("#table-stocktake-results", DataTable)
            tbl.clear()

            hardware_profiles = res.get("hardware_profiles", [])
            row_idx = 1
            for p_info in hardware_profiles:
                plate = p_info.get("plate", "")
                tbl.add_row(
                    str(row_idx),
                    f"[bold green]{plate}[/bold green]",
                    "[bold green]✓ ON STOCK[/bold green]",
                    f"[white]{p_info.get('kit_code', '—')}[/white]",
                    f"[cyan]{p_info.get('gps_tracker', '—')}[/cyan]",
                    f"[dim]{p_info.get('front_ble', '—')}[/dim]",
                    f"[dim]{p_info.get('rear_ble', '—')}[/dim]",
                    key=plate,
                )
                row_idx += 1

            for u_plate in unreg_plates:
                tbl.add_row(
                    str(row_idx),
                    f"[bold red]{u_plate}[/bold red]",
                    "[bold white on dark_red] ⛔ UNREGISTERED [/]",
                    "[dim]Awaiting Registration[/dim]",
                    "—",
                    "—",
                    "—",
                    key=u_plate,
                )
                row_idx += 1

            if unregistered > 0:
                self.notify(
                    f"⚠️ Stock audit completed: {verified} on stock, {unregistered} unregistered kits! Click 'Export Unregistered Kits (CSV)'.",
                    severity="warning",
                    timeout=8,
                )
            else:
                self.notify(
                    f"✓ Safe stock taking complete: All {scanned} plates verified and linked! Variance: {variance:+d}.",
                    severity="information",
                )
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error performing stock audit: {exc}", severity="error")

    def _handle_export_unregistered_stocktake(self) -> None:
        from core.services import stock_monitoring_service
        plates_to_export = self._last_unregistered_stocktake
        if not plates_to_export:
            bulk_text = self.query_one("#text-stocktake-bulk", TextArea).text.strip()
            single_text = self.query_one("#input-stocktake-single", Input).value.strip()
            combined = f"{bulk_text}\n{single_text}".strip()
            if combined:
                plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(combined)
                from core.models import InstallationKit
                from django.db.models import Q
                target_codes = [f"IK-{p}" for p in plates]
                found = set(InstallationKit.objects.filter(
                    Q(registration_number__in=plates) | Q(kit_code__in=target_codes)
                ).values_list("registration_number", flat=True))
                plates_to_export = [p for p in plates if p not in found and f"IK-{p}" not in found]

        if not plates_to_export:
            self.notify("No unregistered stocktake kits to export. Run stock taking audit first or all plates were verified.", severity="warning")
            return

        try:
            file_path, filename, cnt = stock_monitoring_service.export_unregistered_stocktake_csv(
                unregistered_plates=plates_to_export,
                target_date_suffix=self.target_date_suffix,
            )
            self.notify(f"✓ Exported {cnt} unregistered kits to {filename}! Hand to ITMS Stock Transfer Officer.", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting unregistered kits: {exc}", severity="error")

    def _handle_set_manual_physical_count(self) -> None:
        from core.services import stock_monitoring_service
        cnt_val = self.query_one("#input-stocktake-manual-count", Input).value.strip()
        if not cnt_val.isdigit():
            self.notify("Please enter a valid numeric physical count.", severity="warning")
            return

        try:
            res = stock_monitoring_service.set_physical_count(
                physical_count=int(cnt_val),
                target_date_suffix=self.target_date_suffix,
            )
            cnt = res.get("physical_count", 0)
            variance = res.get("variance", 0)
            self.notify(f"✓ Physical count set to {cnt}. Audit variance: {variance:+d}.", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error setting physical count: {exc}", severity="error")

    def action_export_csv(self) -> None:
        from core.services import stock_monitoring_service
        try:
            content = stock_monitoring_service.export_stock_reconciliation_csv(self.target_date_suffix)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"itms_bond_stock_{self.target_date_suffix}_{date_str}.csv"
            out_dir = stock_monitoring_service.get_configured_export_dir()
            out_path = out_dir / filename
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.notify(f"Exported stock report to {out_path}!", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting CSV: {exc}", severity="error")

    def action_show_phone_scanner(self) -> None:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        url = f"http://{local_ip}:8000/mobile/"
        self.notify(f"📱 Phone Scanner URL: {url}\nSelect Mode 3 (WAREHOUSE & BOND STOCK SCANNER).", severity="information", timeout=8)
