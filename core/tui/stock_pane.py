"""
Dedicated Minimalist Stock Management & Live Kit Inspector Workspace (Tab 6).

Provides a unified, streamlined two-column workspace:
1. Universal Command Bar (Top):
   - Shift Date Navigation (◀ Prev, Today, Next ▶, Shift Date [D])
   - 4 Clean Core Modes: [1 Dispatch], [2 Movements], [3 Safe Audit], [4 Ledger]
   - Unified Category Selector (PSV White / PMO Yellow)
   - Single Universal Barcode / Plate Scan Input (/)
   - Universal Paste Dialog (V), Unified Sync (S), and CSV Export (E)
2. Single 1-Line Status Strip:
   - Dynamic real-time metrics & instant verification feedback
3. Main Left Panel (65% width) with ContentSwitcher:
   - Mode 1: Rapid Line Dispatch Table (Instant verification & block non-stock kits)
   - Mode 2: Unified Movements Table (Inbound Deliveries, Transfers, Line Returns)
   - Mode 3: Physical Safe Stock Taking & Hardware Audit Table
   - Mode 4: Daily Stock Ledger & Bond Reconciliation Balance Sheet
4. Persistent Live Kit Inspector (35% width, #inspector-stock):
   - Displays full hardware profiles (GPS tracker, BLE front/rear beacons, order linkage)
     for any scanned, pasted, or selected plate across all modes.
"""
import csv
from datetime import datetime, timedelta
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
    ContentSwitcher,
    DataTable,
    Input,
    Select,
    Static,
)

from core.tui.inspectors import InspectorPane, escape_markup, escape
from core.models import (
    DailyStockLedger,
    InstallationKit,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
)


class StockPane(Vertical):
    """Minimalist, high-efficiency Stock & Reconciliation Workspace Pane (Tab 6)."""

    BINDINGS = [
        Binding("f", "cycle_subtab", "Cycle Mode (F)"),
        Binding("d", "select_date", "Shift Date (D)"),
        Binding("v", "paste_clipboard", "Paste Excel (V)"),
        Binding("s", "sync_all", "Sync All (S)"),
        Binding("r", "refresh_stock", "Refresh (R)"),
        Binding("e", "export_csv", "Export CSV (E)"),
        Binding("o", "open_opening_target_modal", "Opening / Target (O)"),
        Binding("p", "show_phone_scanner", "Phone Scanner (P)"),
        Binding("slash", "focus_scan_input", "Focus Scan (/)", show=False),
        Binding("1", "mode_1", "Dispatch [1]", show=False),
        Binding("2", "mode_2", "Movements [2]", show=False),
        Binding("3", "mode_3", "Safe Audit [3]", show=False),
        Binding("4", "mode_4", "Ledger [4]", show=False),
    ]

    MODES = [
        ("mode-dispatch", "btn-mode-dispatch", "1. 📤 Dispatch"),
        ("mode-movements", "btn-mode-movements", "2. 🔄 Movements"),
        ("mode-audit", "btn-mode-audit", "3. 🔒 Safe Audit"),
        ("mode-ledger", "btn-mode-ledger", "4. 📊 Ledger"),
    ]

    def __init__(self, target_date_suffix: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.target_date_suffix = target_date_suffix or timezone.localdate().strftime("%d%m%y")
        self._cached_recon: Optional[Dict[str, Any]] = None
        self._is_syncing: bool = False
        self._last_blocked_dispatch: List[str] = []
        self._last_unregistered_stocktake: List[str] = []
        self._last_stocktake_profiles: List[Dict[str, Any]] = []
        self._staged_movements: List[str] = []
        self._staged_audit: List[str] = []

    def compose(self) -> ComposeResult:
        # 1. Universal Top Command Bar
        with Horizontal(id="stock-command-bar"):
            yield Button("◀", id="btn-stock-prev-day", classes="stock-cmd-date-btn")
            yield Button("Today", id="btn-stock-today", classes="stock-cmd-date-btn")
            yield Button("▶", id="btn-stock-next-day", classes="stock-cmd-date-btn")
            yield Static(f"[bold cyan]Shift: {self.target_date_suffix}[/bold cyan]", id="lbl-stock-date-display")

            yield Button("1 Dispatch", id="btn-mode-dispatch", classes="mode-btn -active")
            yield Button("2 Movements", id="btn-mode-movements", classes="mode-btn")
            yield Button("3 Safe Audit", id="btn-mode-audit", classes="mode-btn")
            yield Button("4 Ledger", id="btn-mode-ledger", classes="mode-btn")

            yield Select([("PSV White", "PSV"), ("PMO Yellow", "PMO")], id="sel-stock-category", value="PSV", allow_blank=False)
            yield Input(placeholder="Scan barcode or type plate (/)...", id="input-stock-scan")

            yield Button("📋 Paste (V)", id="btn-stock-paste")
            yield Button("⟳ Sync (S)", id="btn-stock-sync", variant="primary")
            yield Button("📥 Export (E)", id="btn-stock-export")

        # 2. Universal 1-Line Status Strip & Feedback Alert
        yield Static(id="stock-status-strip")
        yield Static(
            "[bold green]⚡ READY FOR SCANNING:[/bold green] Scan plate or press [b]V[/b] to paste from Excel.",
            id="lbl-stock-instant-feedback",
        )

        # 3. Two-Column Workspace: Left Operations (65%) | Right Inspector (35%)
        with Horizontal(id="stock-body-container"):
            with Vertical(id="stock-left-panel"):
                with ContentSwitcher(initial="mode-dispatch", id="stock-content-switcher"):
                    # MODE 1: Rapid Line Dispatch
                    with Vertical(id="mode-dispatch", classes="stock-mode-container"):
                        yield DataTable(id="table-stock-dispatch")
                        with Horizontal(classes="stock-mode-footer"):
                            yield Static(id="lbl-dispatch-footer-summary", classes="stock-footer-summary")
                            yield Button("Clear Dispatches Today", id="btn-clear-dispatches", variant="error")

                    # MODE 2: Unified Stock Movements (Deliveries, Transfers, Returns)
                    with Vertical(id="mode-movements", classes="stock-mode-container"):
                        yield DataTable(id="table-stock-movements")
                        with Horizontal(classes="stock-mode-footer"):
                            yield Select(
                                [
                                    ("Inbound Delivery", "DELIVERY"),
                                    ("Bond Transfer In", "TRANSFER_IN"),
                                    ("Bond Transfer Out", "TRANSFER_OUT"),
                                    ("Line Return", "RETURN"),
                                ],
                                id="sel-movement-type",
                                value="DELIVERY",
                                allow_blank=False,
                            )
                            yield Input(placeholder="Partner / Bond / Reason...", id="input-movement-partner", classes="stock-input-compact")
                            yield Input(placeholder="Batch ref / Waybill...", id="input-movement-ref", classes="stock-input-compact")
                            yield Static("[dim]Staged: 0 plates[/dim]", id="lbl-movement-staged")
                            yield Button("✓ Commit Movement", id="btn-commit-movement", variant="success")
                            yield Button("Clear Staged", id="btn-clear-movement-staged", variant="default")

                    # MODE 3: Safe Room Stock Taking & Physical Barcode Audit
                    with Vertical(id="mode-audit", classes="stock-mode-container"):
                        yield DataTable(id="table-stocktake-results")
                        with Horizontal(classes="stock-mode-footer"):
                            yield Static(id="lbl-audit-footer-summary", classes="stock-footer-summary")
                            yield Input(placeholder="Physical Count", id="input-audit-manual-count", classes="stock-input-compact")
                            yield Button("Set Count", id="btn-audit-set-manual")
                            yield Button("Export Unregistered", id="btn-audit-export-unregistered", variant="error")

                    # MODE 4: Daily Stock Ledger & Bond Reconciliation Balance Sheet
                    with Vertical(id="mode-ledger", classes="stock-mode-container"):
                        yield DataTable(id="table-stock-report")
                        with Horizontal(classes="stock-mode-footer"):
                            yield Button("✎ Edit Opening & Target (O)", id="btn-open-opening-target-modal", variant="primary")
                            yield Button("Refresh Ledger (R)", id="btn-refresh-ledger")

            # Persistent Kit Inspector (Right Column 35%)
            with Vertical(id="stock-right-panel"):
                yield InspectorPane(id="inspector-stock")

    def on_mount(self) -> None:
        self._init_tables()
        self.action_refresh_stock()

    def _init_tables(self) -> None:
        # Table 1: Dispatch
        tbl_disp = self.query_one("#table-stock-dispatch", DataTable)
        tbl_disp.clear(columns=True)
        tbl_disp.add_columns("#", "Time", "Plate Number", "Category", "Kit Code", "GPS Tracker", "Status", "Operator")
        tbl_disp.cursor_type = "row"

        # Table 2: Movements
        tbl_mov = self.query_one("#table-stock-movements", DataTable)
        tbl_mov.clear(columns=True)
        tbl_mov.add_columns("#", "Time", "Movement Type", "Category", "Source / Dest / Reason", "Reference / Waybill", "Plates Count", "Sample Plates")
        tbl_mov.cursor_type = "row"

        # Table 3: Safe Audit
        tbl_audit = self.query_one("#table-stocktake-results", DataTable)
        tbl_audit.clear(columns=True)
        tbl_audit.add_columns("#", "Plate Number", "Verification Status", "Kit Code", "GPS Tracker", "Front BLE", "Rear BLE")
        tbl_audit.cursor_type = "row"

        # Table 4: Ledger Report
        tbl_rep = self.query_one("#table-stock-report", DataTable)
        tbl_rep.clear(columns=True)
        tbl_rep.add_columns("Stock Movement Category", "PMO Yellow", "PSV White", "Total Combined", "Notes")
        tbl_rep.cursor_type = "row"

    # ------------------------------------------------------------------------
    # Navigation & Mode Switching
    # ------------------------------------------------------------------------

    def get_active_mode(self) -> str:
        try:
            return self.query_one("#stock-content-switcher", ContentSwitcher).active or "mode-dispatch"
        except Exception:
            return "mode-dispatch"

    def set_mode(self, mode_id: str) -> None:
        try:
            switcher = self.query_one("#stock-content-switcher", ContentSwitcher)
            switcher.active = mode_id
            for m_id, b_id, _ in self.MODES:
                try:
                    btn = self.query_one(f"#{b_id}", Button)
                    if m_id == mode_id:
                        btn.add_class("-active")
                    else:
                        btn.remove_class("-active")
                except Exception:
                    pass
            self.query_one("#input-stock-scan", Input).focus()
        except Exception:
            pass

    def action_cycle_subtab(self, forward: bool = True) -> None:
        """Cycles through the 4 operational modes using the [F] key."""
        cur = self.get_active_mode()
        mode_ids = [m[0] for m in self.MODES]
        idx = mode_ids.index(cur) if cur in mode_ids else 0
        next_idx = (idx + 1) % len(mode_ids) if forward else (idx - 1) % len(mode_ids)
        self.set_mode(mode_ids[next_idx])
        lbl = self.MODES[next_idx][2]
        self.notify(f"Switched mode to: {lbl}")

    def action_mode_1(self) -> None:
        self.set_mode("mode-dispatch")

    def action_mode_2(self) -> None:
        self.set_mode("mode-movements")

    def action_mode_3(self) -> None:
        self.set_mode("mode-audit")

    def action_mode_4(self) -> None:
        self.set_mode("mode-ledger")

    def action_focus_scan_input(self) -> None:
        try:
            self.query_one("#input-stock-scan", Input).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------------
    # Shift Date Controls
    # ------------------------------------------------------------------------

    def _update_date_display(self) -> None:
        try:
            self.query_one("#lbl-stock-date-display", Static).update(
                f"[bold cyan]Shift: {self.target_date_suffix}[/bold cyan]"
            )
        except Exception:
            pass

    def action_select_date(self) -> None:
        from core.tui.dialogs import DateSelectModal

        def _on_date_selected(selected_date: Optional[str]) -> None:
            if not selected_date:
                return
            from core.services import stock_monitoring_service
            _, clean_suf = stock_monitoring_service.resolve_date_and_suffix(selected_date)
            self.target_date_suffix = clean_suf
            self._update_date_display()
            self.action_refresh_stock()
            self.notify(f"✓ Stock shift date switched to: {clean_suf}", severity="information")

        self.app.push_screen(DateSelectModal(current_suffix=self.target_date_suffix), _on_date_selected)

    def action_prev_day(self) -> None:
        try:
            curr_d = datetime.strptime(self.target_date_suffix, "%d%m%y").date()
            prev_d = curr_d - timedelta(days=1)
            self.target_date_suffix = prev_d.strftime("%d%m%y")
            self._update_date_display()
            self.action_refresh_stock()
            self.notify(f"Shift date stepped back to: {self.target_date_suffix}")
        except Exception:
            pass

    def action_next_day(self) -> None:
        try:
            curr_d = datetime.strptime(self.target_date_suffix, "%d%m%y").date()
            next_d = curr_d + timedelta(days=1)
            self.target_date_suffix = next_d.strftime("%d%m%y")
            self._update_date_display()
            self.action_refresh_stock()
            self.notify(f"Shift date stepped forward to: {self.target_date_suffix}")
        except Exception:
            pass

    def action_today(self) -> None:
        self.target_date_suffix = timezone.localdate().strftime("%d%m%y")
        self._update_date_display()
        self.action_refresh_stock()
        self.notify(f"Shift date set to today: {self.target_date_suffix}")

    # ------------------------------------------------------------------------
    # Data Refresh & Table Rendering
    # ------------------------------------------------------------------------

    def action_refresh_stock(self) -> None:
        """Reloads all tables, status strips, and summaries from database."""
        from core.services import stock_monitoring_service
        try:
            recon = stock_monitoring_service.get_stock_reconciliation_summary(self.target_date_suffix)
            self._cached_recon = recon
            self._render_dispatch_table()
            self._render_movements_table()
            self._render_stocktake_table()
            self._render_ledger_table(recon)
            self._update_status_strip()
        except Exception as exc:
            self.notify(f"Error refreshing stock data: {exc}", severity="error")

    def _update_status_strip(self, custom_message: Optional[str] = None) -> None:
        try:
            recon = self._cached_recon or {}
            tot = recon.get("total", {})
            disp = tot.get("dispatched", 0)
            pmo_disp = recon.get("pmo", {}).get("dispatched", 0)
            psv_disp = recon.get("psv", {}).get("dispatched", 0)
            closing = tot.get("closing_stock", 0)
            variance = tot.get("variance", 0)
            var_color = "green" if variance == 0 else ("yellow" if variance > 0 else "red")

            sync_status = "⟳ Syncing..." if self._is_syncing else "✓ Ready"
            if custom_message:
                status_text = (
                    f"Shift: [bold cyan]{self.target_date_suffix}[/bold cyan] │ "
                    f"Dispatched: [bold yellow]{disp:,}[/bold yellow] (PSV: {psv_disp:,}, PMO: {pmo_disp:,}) │ "
                    f"Book Closing: [bold white]{closing:,}[/bold white] │ "
                    f"Variance: [bold {var_color}]{variance:+d}[/bold {var_color}] │ "
                    f"[bold yellow]{custom_message}[/bold yellow]"
                )
            else:
                status_text = (
                    f"Shift: [bold cyan]{self.target_date_suffix}[/bold cyan] │ "
                    f"Dispatched: [bold yellow]{disp:,}[/bold yellow] (PSV: {psv_disp:,}, PMO: {pmo_disp:,}) │ "
                    f"Book Closing: [bold white]{closing:,}[/bold white] │ "
                    f"Variance: [bold {var_color}]{variance:+d}[/bold {var_color}] │ "
                    f"Sync: [dim]{sync_status}[/dim]"
                )
            self.query_one("#stock-status-strip", Static).update(status_text)
        except Exception:
            pass

    def _render_dispatch_table(self) -> None:
        table = self.query_one("#table-stock-dispatch", DataTable)
        table.clear()

        scans = StockDispatchScan.objects.filter(
            work_date_suffix=self.target_date_suffix
        ).order_by("-dispatched_at")

        dispatched_count = scans.count()
        psv_cnt = scans.filter(plate_category="PSV").count()
        pmo_cnt = scans.filter(plate_category="PMO").count()
        try:
            self.query_one("#lbl-dispatch-footer-summary", Static).update(
                f"[bold green]Dispatched Today: {dispatched_count:,} kits[/bold green] (PSV White: {psv_cnt:,} │ PMO Yellow: {pmo_cnt:,})"
            )
        except Exception:
            pass

        if not scans.exists():
            table.add_row("—", "—", "No plates dispatched today yet. Scan barcode or press [V] to paste Excel column.", "—", "—", "—", "—", "—")
            return

        plate_numbers = [s.registration_number for s in scans[:150]]
        kit_map = {k.registration_number: k for k in InstallationKit.objects.filter(registration_number__in=plate_numbers)}

        for idx, scan in enumerate(scans[:150]):
            plate = scan.registration_number
            cat = scan.plate_category
            cat_badge = "[bold white on dark_blue] PSV [/]" if cat == "PSV" else "[bold black on gold1] PMO [/]"
            time_str = scan.dispatched_at.strftime("%H:%M:%S") if scan.dispatched_at else "—"
            kit = kit_map.get(plate)
            kit_code = kit.kit_code if kit else "—"
            gps = (getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or "—") if kit else "—"
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

    def _render_movements_table(self) -> None:
        table = self.query_one("#table-stock-movements", DataTable)
        table.clear()

        # Gather Deliveries
        deliveries = StockDelivery.objects.filter(
            work_date_suffix=self.target_date_suffix
        ).order_by("-received_at")[:50]

        # Gather Returns
        returns = StockReturnScan.objects.filter(
            work_date_suffix=self.target_date_suffix
        ).order_by("-returned_at")[:50]

        row_idx = 1
        for d in deliveries:
            t_str = d.received_at.strftime("%H:%M:%S") if d.received_at else "—"
            cat_badge = "[bold white on dark_blue] PSV [/]" if d.plate_category == "PSV" else "[bold black on gold1] PMO [/]"
            items = list(StockDeliveryItem.objects.filter(delivery=d).values_list("registration_number", flat=True)[:5])
            sample = ", ".join(items)
            if d.total_plates_count > 5:
                sample += f" (+{d.total_plates_count - 5} more)"
            first_key = items[0] if items else str(d.id)
            table.add_row(
                str(row_idx),
                f"[dim]{t_str}[/dim]",
                "[bold green]INBOUND DELIVERY[/bold green]",
                cat_badge,
                "Supplier / Factory",
                f"[cyan]{d.delivery_note_ref or '—'}[/cyan]",
                f"[bold white]{d.total_plates_count:,}[/bold white]",
                f"[dim]{sample or '—'}[/dim]",
                key=first_key,
            )
            row_idx += 1

        for r in returns:
            t_str = r.returned_at.strftime("%H:%M:%S") if r.returned_at else "—"
            cat_badge = "[bold white on dark_blue] PSV [/]" if r.plate_category == "PSV" else "[bold black on gold1] PMO [/]"
            table.add_row(
                str(row_idx),
                f"[dim]{t_str}[/dim]",
                "[bold yellow]LINE RETURN[/bold yellow]",
                cat_badge,
                f"[yellow]{r.reason}[/yellow]",
                "—",
                "1",
                f"[bold yellow]{r.registration_number}[/bold yellow]",
                key=r.registration_number,
            )
            row_idx += 1

        if row_idx == 1:
            table.add_row("—", "—", "No stock movements recorded for this shift yet.", "—", "—", "—", "—", "—")

    def _render_stocktake_table(self) -> None:
        tbl = self.query_one("#table-stocktake-results", DataTable)
        tbl.clear()

        profiles = getattr(self, "_last_stocktake_profiles", None)
        unreg = getattr(self, "_last_unregistered_stocktake", None)

        if profiles or unreg:
            row_idx = 1
            if profiles:
                for p_info in profiles:
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
            if unreg:
                for u_plate in unreg:
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
            return

        # Default: show active safe room stock kits
        kits = InstallationKit.objects.filter(status__iexact="New").order_by("-updated_at")[:1000]
        if kits.exists():
            for idx, kit in enumerate(kits, 1):
                plate = kit.registration_number or (kit.kit_code.replace("IK-", "") if kit.kit_code else "—")
                gps = getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or "—"
                front_ble = getattr(kit, "front_tracker", "") or getattr(kit, "ble_beacon_front", "") or "—"
                rear_ble = getattr(kit, "rear_tracker", "") or getattr(kit, "ble_beacon_rear", "") or "—"
                tbl.add_row(
                    str(idx),
                    f"[bold green]{plate}[/bold green]",
                    "[bold green]✓ SAFE ROOM STOCK[/bold green]",
                    f"[white]{kit.kit_code or '—'}[/white]",
                    f"[cyan]{gps}[/cyan]",
                    f"[dim]{front_ble}[/dim]",
                    f"[dim]{rear_ble}[/dim]",
                    key=plate,
                )
        else:
            tbl.add_row("—", "No stock audits recorded yet.", "Click '📋 Paste (V)' to run safe room audit", "—", "—", "—", "—")

    def _render_ledger_table(self, recon: Dict[str, Any]) -> None:
        table = self.query_one("#table-stock-report", DataTable)
        table.clear()

        rows = recon.get("report_table", {}).get("rows", [])
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
            elif "Physical Count" in metric:
                m_str = f"[bold cyan]{metric}[/bold cyan]"
                y_str = f"[bold cyan]{pmo}[/bold cyan]"
                p_str = f"[bold cyan]{psv}[/bold cyan]"
                t_str = f"[bold white on dark_cyan] {tot:,} [/bold white on dark_cyan]" if isinstance(tot, int) else f"[bold white on dark_cyan] {tot} [/bold white on dark_cyan]"
            elif "Variance (Physical" in metric:
                var_str = str(tot)
                var_style = "bold green" if ("0" in var_str or "Balanced" in var_str) else ("bold yellow" if "+" in var_str else "bold red")
                m_str = f"[{var_style}]{metric}[/{var_style}]"
                y_str = "—"
                p_str = "—"
                t_str = f"[{var_style}] {var_str} [/{var_style}]"
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
                m_str = metric
                y_str = f"{pmo:,}" if isinstance(pmo, int) else str(pmo)
                p_str = f"{psv:,}" if isinstance(psv, int) else str(psv)
                t_str = f"{tot:,}" if isinstance(tot, int) else str(tot)

            table.add_row(m_str, y_str, p_str, t_str, note or "—")

    # ------------------------------------------------------------------------
    # Universal Input Scanning & Verification Routing
    # ------------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_val = event.value.strip()
        if not raw_val:
            return

        if event.input.id == "input-stock-scan":
            mode = self.get_active_mode()
            if mode == "mode-dispatch":
                self._handle_instant_dispatch_scan(raw_val, event.input)
            elif mode == "mode-movements":
                self._handle_movement_scan(raw_val, event.input)
            elif mode == "mode-audit":
                self._handle_audit_scan(raw_val, event.input)
            elif mode == "mode-ledger":
                self._handle_instant_dispatch_scan(raw_val, event.input)
        elif event.input.id == "input-audit-manual-count":
            self._handle_set_manual_physical_count()

    def _handle_instant_dispatch_scan(self, raw_val: str, input_widget: Input) -> None:
        """Instant sub-50ms stock verification against warehouse stock."""
        from core.services import stock_monitoring_service
        from core.services.kit_provisioning_service import verify_scanned_kit_stock

        if "\n" in raw_val or "\r" in raw_val or "\t" in raw_val:
            plates, _, _ = stock_monitoring_service.parse_plate_input_with_stats(raw_val)
            self._handle_batch_excel_dispatch(plates)
            input_widget.value = ""
            input_widget.focus()
            return

        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⚠️ INVALID FORMAT: '{raw_val}' [/bold white on dark_red] [red]Must match e.g. UMA 338PZ or UMA338PZ[/red]"
            )
            input_widget.value = ""
            input_widget.focus()
            return

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

        cat_select = self.query_one("#sel-stock-category", Select)
        category = str(cat_select.value or "PSV")

        check = verify_scanned_kit_stock(plate, check_itms_live=True)
        if isinstance(check, tuple):
            is_valid = bool(check[0])
            kit = check[1] if len(check) > 1 else None
            reason = check[2] if len(check) > 2 else ""
        elif isinstance(check, dict):
            is_valid = bool(check.get("is_valid") or check.get("valid"))
            kit = check.get("kit")
            reason = check.get("error") or check.get("reason") or ""
        else:
            is_valid = bool(getattr(check, "is_valid", False))
            kit = getattr(check, "kit", None)
            reason = getattr(check, "reason", "")

        status_str = getattr(check, "status", "")
        if not status_str and isinstance(check, dict):
            status_str = check.get("status", "")
        is_already_installed = (status_str == "ALREADY_INSTALLED") or ("already installed" in reason.lower())

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
            )
            if plate not in self._last_blocked_dispatch:
                self._last_blocked_dispatch.append(plate)
            input_widget.value = ""
            input_widget.focus()
            return

        if not is_valid:
            err_msg = reason or f"Plate {plate} is NOT on stock in Safe Room / ITMS!"
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_red] ⛔ NOT ON STOCK (SET ASIDE): {plate} [/bold white on dark_red] [bold red]{escape(err_msg)} — DO NOT DISPATCH! Set physical box aside.[/bold red]"
            )
            self.notify(f"⛔ SET ASIDE: {plate} is NOT on stock! Set box aside & swap.", severity="error")
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                plate=plate,
                kit=kit,
                error_message=f"NOT ON STOCK: {err_msg}",
            )
            if plate not in self._last_blocked_dispatch:
                self._last_blocked_dispatch.append(plate)
            stock_monitoring_service.record_blocked_plates([plate], self.target_date_suffix)
            input_widget.value = ""
            input_widget.focus()
            return

        try:
            from core.services import bond_service
            active_bond = bond_service.get_active_bond()
            b_code = active_bond.get("code", "AGM")
            try:
                op_name = str(getattr(self.app, "current_user", "") or "Operator")
            except Exception:
                op_name = "Operator"
            work_d, _ = stock_monitoring_service.resolve_date_and_suffix(self.target_date_suffix)

            scan = StockDispatchScan.objects.create(
                registration_number=plate,
                plate_category=category,
                work_date=work_d,
                work_date_suffix=self.target_date_suffix,
                bond_code=b_code,
                operator_name=op_name,
                status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                notes="Instant single scan dispatch.",
            )

            kit_code = kit.kit_code if kit else "IK-Direct"
            self.query_one("#lbl-stock-instant-feedback", Static).update(
                f"[bold white on dark_green] ✓ VERIFIED & DISPATCHED: {plate} ({category}) [/bold white on dark_green] [green]Kit: {kit_code} │ Fit to Bike![/green]"
            )
            self.notify(f"✓ Dispatched {plate} ({category}) -> Installation Line", severity="information")
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(
                plate=plate,
                kit=kit,
            )
            self._render_dispatch_table()
            self._update_status_strip()
        except Exception as exc:
            self.notify(f"Error saving dispatch: {exc}", severity="error")
        finally:
            input_widget.value = ""
            input_widget.focus()

    def _handle_movement_scan(self, raw_val: str, input_widget: Input) -> None:
        """Stages a single scanned plate into movements in-memory buffer."""
        from core.services import stock_monitoring_service
        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.notify(f"⚠️ Invalid plate format: '{raw_val}'", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        if plate in self._staged_movements:
            self.notify(f"⚠️ Plate {plate} is already staged for movement!", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        self._staged_movements.append(plate)
        input_widget.value = ""
        input_widget.focus()
        try:
            self.query_one("#lbl-movement-staged", Static).update(
                f"[bold green]Staged: {len(self._staged_movements)} plates[/bold green]"
            )
        except Exception:
            pass
        self.notify(f"✓ Added {plate} to movements (Total staged: {len(self._staged_movements)})", severity="information")
        try:
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate)
        except Exception:
            pass

    def _handle_audit_scan(self, raw_val: str, input_widget: Input) -> None:
        """Stages a single scanned plate into Safe Room physical audit."""
        from core.services import stock_monitoring_service
        plate = stock_monitoring_service.extract_single_plate(raw_val)
        if not plate:
            self.notify(f"⚠️ Invalid plate format: '{raw_val}'", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        if plate in self._staged_audit:
            self.notify(f"⚠️ Duplicate audit scan: Plate {plate} already recorded!", severity="warning")
            input_widget.value = ""
            input_widget.focus()
            return

        self._staged_audit.append(plate)
        input_widget.value = ""
        input_widget.focus()
        self._apply_stocktake_plates(self._staged_audit)
        try:
            self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate)
        except Exception:
            pass

    # ------------------------------------------------------------------------
    # Batch Operations & Modal Handlers
    # ------------------------------------------------------------------------

    def action_paste_clipboard(self) -> None:
        """Context-sensitive paste: opens StockPasteModal for active mode."""
        cur_mode = self.get_active_mode()
        modal_mode = "dispatch"
        if cur_mode == "mode-movements":
            modal_mode = "movements"
        elif cur_mode in ("mode-audit", "mode-ledger"):
            modal_mode = "stocktake"

        def _on_paste_modal_result(result: Optional[Dict[str, Any]]) -> None:
            if not result or not result.get("plates"):
                return
            plates = result.get("plates", [])
            if modal_mode == "dispatch":
                self._handle_batch_excel_dispatch(plates)
            elif modal_mode == "movements":
                self._apply_movements_plates(plates)
            elif modal_mode == "stocktake":
                self._apply_stocktake_plates(plates)

        from core.tui.dialogs import StockPasteModal
        self.app.push_screen(
            StockPasteModal(mode=modal_mode, target_date_suffix=self.target_date_suffix),
            _on_paste_modal_result,
        )

    def _handle_batch_excel_dispatch(self, plates: Optional[List[str]] = None) -> None:
        """Processes a large batch of plates (~1,200) for line dispatch."""
        from core.services import stock_monitoring_service, kit_provisioning_service, bond_service

        if not plates:
            return

        cat_select = self.query_one("#sel-stock-category", Select)
        category = str(cat_select.value or "PSV")

        try:
            active_bond = bond_service.get_active_bond()
            wh_name = active_bond.get("name", "AGM SPIRO")
            b_code = active_bond.get("code", "AGM")

            verify_res = kit_provisioning_service.verify_scanned_kits_stock(
                plates,
                check_itms_live=True,
                facility_name=wh_name,
            )
            verified = set(verify_res.get("verified_plates", []))
            already_installed = list(verify_res.get("already_installed", []))
            rejected = [p for p in plates if p not in verified and p not in already_installed]
            blocked = list(already_installed) + list(rejected)
            self._last_blocked_dispatch = blocked

            if blocked:
                stock_monitoring_service.record_blocked_plates(blocked, self.target_date_suffix)

            existing_dispatches = set(
                StockDispatchScan.objects.filter(
                    work_date_suffix=self.target_date_suffix,
                    registration_number__in=verified,
                ).values_list("registration_number", flat=True)
            )

            to_dispatch = [p for p in plates if p in verified and p not in existing_dispatches]
            op_name = str(getattr(self.app, "current_user", "") or "Operator")
            work_d, _ = stock_monitoring_service.resolve_date_and_suffix(self.target_date_suffix)

            new_scans = [
                StockDispatchScan(
                    registration_number=p,
                    plate_category=category,
                    work_date=work_d,
                    work_date_suffix=self.target_date_suffix,
                    bond_code=b_code,
                    operator_name=op_name,
                    status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                    notes="Batch Excel dispatch.",
                )
                for p in to_dispatch
            ]

            if new_scans:
                StockDispatchScan.objects.bulk_create(new_scans)

            new_cnt = len(new_scans)
            if blocked:
                self.query_one("#lbl-stock-instant-feedback", Static).update(
                    f"[bold white on dark_red] ⛔ {len(blocked)} KIT(S) BLOCKED / NOT ON STOCK: [/bold white on dark_red] [bold red]Dispatched {new_cnt} on stock. Set {len(blocked)} physical boxes aside and swap with kits on stock![/bold red]"
                )
                self.notify(f"⚠️ Dispatched {new_cnt} kits. {len(blocked)} non-stock kits blocked to set aside & swap.", severity="warning")
            else:
                self.query_one("#lbl-stock-instant-feedback", Static).update(
                    f"[bold white on dark_green] ✓ BATCH DISPATCH SUCCESS: [/bold white on dark_green] [green]All {new_cnt} plates verified on stock and dispatched![/green]"
                )
                self.notify(f"✓ Dispatched all {new_cnt} {category} plates.", severity="information")

            self._render_dispatch_table()
            self._update_status_strip()
            if to_dispatch:
                self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(to_dispatch[0])
        except Exception as exc:
            self.notify(f"Error processing batch dispatch: {exc}", severity="error")

    def _apply_movements_plates(self, plates: List[str]) -> None:
        """Appends plates from paste modal to in-memory movements staging."""
        merged = list(dict.fromkeys(self._staged_movements + plates))
        self._staged_movements = merged
        try:
            self.query_one("#lbl-movement-staged", Static).update(
                f"[bold green]Staged: {len(merged)} plates[/bold green]"
            )
        except Exception:
            pass
        self.notify(f"✓ Ingested {len(plates):,} plates into staging! Total staged: {len(merged):,}", severity="information")

    def _apply_stocktake_plates(self, plates: List[str]) -> None:
        """Executes physical safe audit against book closing."""
        from core.services import stock_monitoring_service
        self._staged_audit = plates
        self.notify(f"🔒 Auditing {len(plates):,} plates against Safe Room stock...", severity="information")
        try:
            res = stock_monitoring_service.record_stock_taking_audit(
                scanned_plates=plates,
                target_date_suffix=self.target_date_suffix,
            )
            scanned = res.get("total_scanned", 0)
            verified = res.get("verified_count", 0)
            unregistered = res.get("unregistered_count", 0)
            unreg_plates = res.get("unregistered_plates", [])
            self._last_unregistered_stocktake = unreg_plates
            self._last_stocktake_profiles = res.get("hardware_profiles", [])

            book = res.get("book_closing_total", 0)
            variance = res.get("variance", 0)
            var_color = "green" if variance == 0 else ("yellow" if variance > 0 else "red")
            summary_text = (
                f"[bold cyan]Safe Audit Summary:[/bold cyan]  Physical Audited: [bold white]{scanned:,}[/bold white]  │  "
                f"Verified: [bold green]{verified:,}[/bold green]  │  "
                f"Unregistered: [bold red]{unregistered:,}[/bold red]  │  "
                f"Book Closing: [bold white]{book:,}[/bold white]  │  "
                f"Variance: [bold {var_color}]{variance:+d}[/bold {var_color}]"
            )
            try:
                self.query_one("#lbl-audit-footer-summary", Static).update(summary_text)
                self.query_one("#input-audit-manual-count", Input).value = str(scanned)
            except Exception:
                pass

            self._render_stocktake_table()
            self._update_status_strip()

            if unregistered > 0:
                self.notify(
                    f"⚠️ Stock audit completed: {verified:,} on stock, {unregistered:,} unregistered kits. Click 'Export Unregistered'.",
                    severity="warning",
                    timeout=8,
                )
            else:
                self.notify(
                    f"✓ Safe audit complete: All {scanned:,} plates verified and linked! Variance: {variance:+d}.",
                    severity="information",
                )
        except Exception as exc:
            self.notify(f"Error performing stock audit: {exc}", severity="error")

    # ------------------------------------------------------------------------
    # Movements Commit & Actions
    # ------------------------------------------------------------------------

    def _handle_commit_movement(self) -> None:
        from core.services import stock_monitoring_service
        if not self._staged_movements:
            self.notify("Please scan or paste plates to stage for movement first.", severity="warning")
            return

        cat_select = self.query_one("#sel-stock-category", Select)
        category = str(cat_select.value or "PSV")
        m_type = str(self.query_one("#sel-movement-type", Select).value or "DELIVERY")
        partner = self.query_one("#input-movement-partner", Input).value.strip()
        ref = self.query_one("#input-movement-ref", Input).value.strip()

        plates_str = "\n".join(self._staged_movements)
        try:
            if m_type == "DELIVERY":
                res = stock_monitoring_service.record_inbound_delivery(
                    plates=plates_str,
                    plate_category=category,
                    delivery_note_ref=ref,
                    target_date_suffix=self.target_date_suffix,
                )
                self.notify(f"✓ Recorded delivery of {res.get('plates_count')} {category} plates ({res.get('new_kits_created')} new kits created)!", severity="information")
            elif m_type in ("TRANSFER_IN", "TRANSFER_OUT"):
                lbl = "Transfer In" if m_type == "TRANSFER_IN" else "Transfer Out"
                res = stock_monitoring_service.record_bond_transfer(
                    transfer_type=m_type,
                    plate_category=category,
                    plates_count=len(self._staged_movements),
                    other_bond_name=partner or "Other Bond",
                    plates=plates_str,
                    target_date_suffix=self.target_date_suffix,
                )
                self.notify(f"✓ Recorded {lbl} of {res.get('plates_count')} {category} plates ({partner})!", severity="information")
            elif m_type == "RETURN":
                res = stock_monitoring_service.record_return_scans(
                    plates=plates_str,
                    plate_category=category,
                    reason=partner or "BIKE_NO_SHOW",
                    target_date_suffix=self.target_date_suffix,
                )
                new_cnt = res.get("newly_returned", 0)
                self.notify(f"✓ Recorded {new_cnt} returned {category} plates.", severity="information")

            self._staged_movements = []
            try:
                self.query_one("#lbl-movement-staged", Static).update("[dim]Staged: 0 plates[/dim]")
                self.query_one("#input-movement-partner", Input).value = ""
                self.query_one("#input-movement-ref", Input).value = ""
            except Exception:
                pass

            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error committing movement: {exc}", severity="error")

    def _handle_clear_movement_staged(self) -> None:
        self._staged_movements = []
        try:
            self.query_one("#lbl-movement-staged", Static).update("[dim]Staged: 0 plates[/dim]")
        except Exception:
            pass
        self.notify("Cleared staged movement plates.")

    def _handle_clear_dispatches(self) -> None:
        count = StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).count()
        if count == 0:
            self.notify("No dispatches to clear today.", severity="warning")
            return
        StockDispatchScan.objects.filter(work_date_suffix=self.target_date_suffix).delete()
        self.notify(f"Cleared {count} dispatched plates for shift {self.target_date_suffix}.")
        self.action_refresh_stock()

    def _handle_set_manual_physical_count(self) -> None:
        from core.services import stock_monitoring_service
        raw = self.query_one("#input-audit-manual-count", Input).value.strip()
        if not raw or not raw.isdigit():
            self.notify("Please enter a valid numeric physical count (e.g. 1200).", severity="warning")
            return
        cnt = int(raw)
        try:
            stock_monitoring_service.set_manual_physical_count(cnt, self.target_date_suffix)
            self.notify(f"✓ Updated physical safe stock count to {cnt:,} plates!", severity="information")
            self.action_refresh_stock()
        except Exception as exc:
            self.notify(f"Error updating physical count: {exc}", severity="error")

    def _handle_export_unregistered_stocktake(self) -> None:
        plates_to_export = self._last_unregistered_stocktake
        if not plates_to_export:
            self.notify("No unregistered kits to export. Run audit first or all plates were verified.", severity="warning")
            return

        try:
            export_dir = os.path.join(settings.BASE_DIR, "exports")
            os.makedirs(export_dir, exist_ok=True)
            filename = f"unregistered_stocktake_kits_{self.target_date_suffix}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = os.path.join(export_dir, filename)

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Row", "Plate Number", "Audit Shift Date", "Audit Status", "Action Required"])
                for i, plate in enumerate(plates_to_export, 1):
                    writer.writerow([i, plate, self.target_date_suffix, "UNREGISTERED_IN_SAFE_ROOM", "Manual Registration Required"])

            self.notify(f"Exported {len(plates_to_export)} unregistered kits to exports/{filename}!", severity="information")
        except Exception as exc:
            self.notify(f"Error exporting CSV: {exc}", severity="error")

    def action_open_opening_target_modal(self) -> None:
        from core.tui.dialogs import OpeningTargetModal

        def _on_modal_close(result: Optional[Dict[str, Any]]) -> None:
            if result:
                self.action_refresh_stock()

        self.app.push_screen(
            OpeningTargetModal(target_date_suffix=self.target_date_suffix),
            _on_modal_close,
        )

    # ------------------------------------------------------------------------
    # Unified Sync Action (S)
    # ------------------------------------------------------------------------

    def action_sync_all(self) -> None:
        """Unified sync: provisions warehouse kits from ITMS and refreshes ledger."""
        if self._is_syncing:
            self.notify("Sync is already running...", severity="warning")
            return
        self._is_syncing = True
        self.notify("⟳ Starting unified sync (ITMS Installation Kits & Shift Orders)...", severity="information")
        self._run_sync_worker()

    @work(exclusive=True, thread=True)
    def _run_sync_worker(self) -> None:
        try:
            from core.services import kit_provisioning_service
            res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=None,
                sync_itms=True,
                max_pages=100,
            )
            synced = res.get("itms_kits_synced", 0)
            created = res.get("kits_created", 0)
            updated = res.get("kits_updated", 0)
            total = res.get("new_kits_ready_count", 0)
            msg = f"✓ Sync Complete: {synced} ITMS kits ({created} new, {updated} updated). Ready in stock: {total:,}."
            self.app.call_from_thread(self.notify, msg, severity="information")
            self.app.call_from_thread(self.action_refresh_stock)
        except Exception as exc:
            err = f"Sync failed: {exc}"
            self.app.call_from_thread(self.notify, err, severity="error")
        finally:
            self._is_syncing = False
            self.app.call_from_thread(self._update_status_strip)

    # ------------------------------------------------------------------------
    # CSV Export & Phone Scanner
    # ------------------------------------------------------------------------

    def action_export_csv(self) -> None:
        from core.services import stock_monitoring_service
        try:
            filepath = stock_monitoring_service.export_stock_reconciliation_csv(self.target_date_suffix)
            filename = os.path.basename(filepath)
            self.notify(f"✓ Exported stock report to exports/{filename}!", severity="information")
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

    # ------------------------------------------------------------------------
    # Table Selection & Inspection
    # ------------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._inspect_table_selection(event.row_key)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        self._inspect_table_selection(event.cell_key.row_key)

    def _inspect_table_selection(self, row_key: Any) -> None:
        key_str = str(row_key.value if hasattr(row_key, "value") else row_key)
        from core.services import stock_monitoring_service
        plate = stock_monitoring_service.extract_single_plate(key_str)
        if plate:
            try:
                self.query_one("#inspector-stock", InspectorPane).show_stock_kit_details(plate)
            except Exception:
                pass

    # ------------------------------------------------------------------------
    # Button Router
    # ------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-stock-prev-day":
            self.action_prev_day()
        elif btn_id == "btn-stock-next-day":
            self.action_next_day()
        elif btn_id == "btn-stock-today":
            self.action_today()
        elif btn_id == "btn-mode-dispatch":
            self.set_mode("mode-dispatch")
        elif btn_id == "btn-mode-movements":
            self.set_mode("mode-movements")
        elif btn_id == "btn-mode-audit":
            self.set_mode("mode-audit")
        elif btn_id == "btn-mode-ledger":
            self.set_mode("mode-ledger")
        elif btn_id == "btn-stock-paste":
            self.action_paste_clipboard()
        elif btn_id == "btn-stock-sync":
            self.action_sync_all()
        elif btn_id == "btn-stock-export":
            self.action_export_csv()
        elif btn_id == "btn-clear-dispatches":
            self._handle_clear_dispatches()
        elif btn_id == "btn-commit-movement":
            self._handle_commit_movement()
        elif btn_id == "btn-clear-movement-staged":
            self._handle_clear_movement_staged()
        elif btn_id == "btn-audit-set-manual":
            self._handle_set_manual_physical_count()
        elif btn_id == "btn-audit-export-unregistered":
            self._handle_export_unregistered_stocktake()
        elif btn_id == "btn-open-opening-target-modal":
            self.action_open_opening_target_modal()
        elif btn_id == "btn-refresh-ledger":
            self.action_refresh_stock()
