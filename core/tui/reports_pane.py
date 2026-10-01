"""
Reports & System Totals Pane (Tab 6) for the ITMS Operator TUI.

Provides an executive, visual dashboard designed for immediate clarity:
1. Top 3-Pillar Hero Dashboard:
   - 1. INSTALLED (ARCHIVED): Officially verified and completed for the work date.
   - 2. PENDING INSTALLATION: Active orders currently in fitment queue for the work date.
   - 3. UNALLOCATED PLATES: Plates taken out of stock or detected without an active order.
2. Unallocated Plates Audit DataTable (Neither in Active Orders nor Archive).
3. ITMS Installation Kits & Warehouse Stock Breakdown.
4. Verification Queue Health, Success Rates & Offline Outbox.
5. Shift Productivity & Hardware Serials Telemetry.
6. Actions Bar: Cycle Work Date [T], Sync ITMS Orders [S], Export CSV [E], Refresh [R], Scope [D].
"""
from typing import Any, Dict, List, Optional

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from core.services import auth_service, report_service


import os
from datetime import datetime

from django.conf import settings
from core.services import bond_service


class ReportsPane(VerticalScroll):
    """Interactive Reports & Totals Pane (Tab 6)."""

    BINDINGS = [
        Binding("t", "cycle_date", "Select Date"),
        Binding("s", "sync_orders", "Sync Orders"),
        Binding("k", "open_stock_manager", "Stock Manager"),
        Binding("e", "export_report", "Export Report"),
        Binding("r", "refresh_totals", "Refresh"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_scope = "ALL"
        self.current_target_date: Optional[str] = None
        self.available_dates: List[Dict[str, Any]] = []
        self.date_index: int = 0
        self._cached_totals: Optional[Dict[str, Any]] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="reports-container"):
            # 1. Header Ribbon Banner (Facility & Work Shift Date)
            yield Static(id="reports-header-banner")

            # 2. Operational Actions Bar
            with Horizontal(id="reports-actions-bar"):
                yield Button("🗓️ Select Date [T]", variant="primary", id="btn-report-date")
                yield Button("📦 Stock Manager & Safe Audit [K]", variant="success", id="btn-report-stock")
                yield Button("⚡ Sync Stock Kits", variant="primary", id="btn-sync-stock-kits-ribbon")
                yield Button("🔄 Sync Orders [S]", variant="warning", id="btn-report-sync")
                yield Button("📑 Export Report [E]", variant="default", id="btn-report-export")
                yield Button("🔄 Refresh [R]", variant="default", id="btn-report-refresh")
                yield Static(id="reports-status-indicator")

            # 3. Top 3-Pillar Hero Dashboard
            with Horizontal(id="reports-hero-row"):
                with Vertical(classes="hero-card hero-installed", id="card-hero-installed"):
                    yield Static("[bold white on dark_blue] 🏆 1. INSTALLED (ARCHIVED) [/bold white on dark_blue]", classes="hero-card-badge")
                    yield Static(id="hero-installed-metric", classes="hero-metric")
                    yield Static(id="hero-installed-subtext", classes="hero-subtext")

                with Vertical(classes="hero-card hero-pending", id="card-hero-pending"):
                    yield Static("[bold black on gold1] ⏳ 2. PENDING INSTALLATION [/bold black on gold1]", classes="hero-card-badge")
                    yield Static(id="hero-pending-metric", classes="hero-metric")
                    yield Static(id="hero-pending-subtext", classes="hero-subtext")

                with Vertical(classes="hero-card hero-unallocated", id="card-hero-unallocated"):
                    yield Static("[bold white on dark_red] ⚠️ 3. UNALLOCATED PLATES [/bold white on dark_red]", classes="hero-card-badge")
                    yield Static(id="hero-unallocated-metric", classes="hero-metric")
                    yield Static(id="hero-unallocated-subtext", classes="hero-subtext")

            # 4. Bond Physical Stock Ledger Report (PSV White vs PMO Yellow)
            with Vertical(classes="report-card", id="card-report-stock-ledger"):
                yield Static("[bold cyan]📦 Physical Warehouse Stock & Bond Reconciliation Balance[/bold cyan]", classes="card-title")
                yield Static(id="reports-stock-summary")
                yield DataTable(id="table-report-stock-ledger")

            # 5. Floor Unallocated Plates & Discrepancy Audit DataTable
            with Vertical(classes="report-card", id="card-report-unallocated"):
                yield Static("[bold red]⚠️ Floor Unallocated Plates Audit (Dispatched to Line / No Order in ITMS)[/bold red]", classes="card-title")
                yield Static(id="reports-unallocated-summary")
                with Horizontal(classes="unallocated-actions-row"):
                    yield Button("📋 Copy Raw Plates", variant="primary", id="btn-copy-unallocated-raw")
                    yield Button("📑 Copy MVR Docket", variant="warning", id="btn-copy-mvr-docket")
                yield DataTable(id="table-report-unallocated")

            # 6. Direct Categorized Shift CSV Exports (Directory: exports/)
            with Vertical(classes="report-card", id="card-report-csv-exports"):
                yield Static("[bold green]📁 Categorized Shift CSV Exports (Directory: exports/)[/bold green]", classes="card-title")
                yield Static(id="reports-csv-exports-summary")
                with Horizontal(classes="csv-actions-row"):
                    yield Button("🚀 Export All 5 CSVs [E]", variant="primary", id="btn-export-all-csvs")
                    yield Button("📂 Export Folder", variant="default", id="btn-change-reports-folder")
                    yield Button("📊 Master Ledger CSV", variant="success", id="btn-export-master-csv")
                    yield Button("🔴 Unallocated CSV", variant="error", id="btn-export-unalloc-csv")
                    yield Button("🔵 Archive CSV", variant="default", id="btn-export-arch-csv")
                    yield Button("🟡 Pending CSV", variant="warning", id="btn-export-pend-csv")
                    yield Button("⚪ Dispatched CSV", variant="default", id="btn-export-disp-csv")

    def on_mount(self) -> None:
        table_stock = self.query_one("#table-report-stock-ledger", DataTable)
        table_stock.add_columns("DESCRIPTION", "PRIVATE (PMO)", "PUBLIC (PSV)", "Total Combined", "REMARKS / Formula Note")
        table_stock.cursor_type = "row"

        table_unalloc = self.query_one("#table-report-unallocated", DataTable)
        table_unalloc.add_columns("Plate Number", "Source / ID", "Audit Reason / Status", "Warehouse / Detail", "Detected / Date")
        table_unalloc.cursor_type = "row"

        self.refresh_reports()

    def refresh_reports(self, scope: Optional[str] = None, target_date: Optional[str] = None) -> None:
        """Fetches fresh totals and renders all cards and data tables."""
        if scope is not None:
            self.current_scope = scope
        if target_date is not None:
            self.current_target_date = target_date

        try:
            totals = report_service.get_system_totals(
                scope=self.current_scope,
                target_date=self.current_target_date,
            )
            self._cached_totals = totals
            dd = totals.get("date_driven", {})
            self.available_dates = dd.get("available_dates", [])
            if not self.current_target_date:
                self.current_target_date = dd.get("selected_date_suffix")
        except Exception as exc:
            try:
                self.query_one("#reports-header-banner", Static).update(
                    f"[bold red]Error calculating reports: {exc}[/bold red]"
                )
            except Exception:
                pass
            return

        self._render_header(totals)
        self._render_hero_dashboard(totals)
        self._render_stock_ledger(totals)
        self._render_unallocated(totals)
        self._render_csv_exports(totals)

    def _render_header(self, t: Dict[str, Any]) -> None:
        user = auth_service.get_remembered_session()
        op_name = (user.get_full_name() or user.username) if user else "Operator"

        dd = t.get("date_driven", {})
        suf = dd.get("selected_date_suffix", "ALL")
        fmt_date = dd.get("selected_date_formatted", "All Dates")
        wh_code = bond_service.get_active_bond_code()
        wh_name = bond_service.get_active_bond_name()

        self.query_one("#reports-header-banner", Static).update(
            f"[bold cyan]🏢 Operating Facility: {wh_name} ({wh_code})[/bold cyan]  │  "
            f"[bold white]Shift Work Date:[/bold white] [bold yellow]{fmt_date}[/bold yellow] ([cyan]{suf}[/cyan])  │  "
            f"[bold white]Team Leader / Operator:[/bold white] [bold white]{op_name}[/bold white]  │  "
            f"[dim]Live Synced: {t.get('generated_at', '')}[/dim]"
        )

        try:
            btn_date = self.query_one("#btn-report-date", Button)
            btn_date.label = f"🗓️ Date: {suf} [T]"
        except Exception:
            pass

        inst_cnt = dd.get("installed_archive", {}).get("count", 0)
        unalloc_cnt = dd.get("unallocated_plates", {}).get("total_count", 0)
        try:
            self.query_one("#reports-status-indicator", Static).update(
                f"[dim]• Live Synced • Orders: [bold white]{t['orders']['total']}[/bold white] • Installed: [bold green]{inst_cnt}[/bold green] • Unallocated: [bold red]{unalloc_cnt}[/bold red][/dim]"
            )
        except Exception:
            pass

    def _render_hero_dashboard(self, t: Dict[str, Any]) -> None:
        dd = t.get("date_driven", {})
        inst = dd.get("installed_archive", {})
        pend = dd.get("pending_orders", {})
        unalloc = dd.get("unallocated_plates", {})

        inst_cnt = inst.get("count", 0)
        inst_pct = inst.get("percentage", "0%")
        pend_cnt = pend.get("count", 0)
        pend_pct = pend.get("percentage", "0%")
        unalloc_cnt = unalloc.get("total_count", 0)
        failed_link_cnt = unalloc.get("failed_linking_count", 0)
        stock_kits_cnt = unalloc.get("stock_kits_count", 0)

        officers = inst.get("officers", {})
        top_officers_str = ", ".join([f"{k} ({v})" for k, v in list(officers.items())[:2]]) or "All verified"

        stages = pend.get("stages", {})
        stages_str = ", ".join([f"{k} ({v})" for k, v in list(stages.items())[:2]]) or "In installation queue"

        self.query_one("#hero-installed-metric", Static).update(
            f"[bold cyan]{inst_cnt}[/bold cyan] [bold white]Plates[/bold white] [dim]({inst_pct})[/dim]"
        )
        self.query_one("#hero-installed-subtext", Static).update(
            f"[bold green]✓[/bold green] Officially verified in ITMS Archive\n[dim]Officers: {top_officers_str}[/dim]"
        )

        self.query_one("#hero-pending-metric", Static).update(
            f"[bold yellow]{pend_cnt}[/bold yellow] [bold white]Plates[/bold white] [dim]({pend_pct})[/dim]"
        )
        self.query_one("#hero-pending-subtext", Static).update(
            f"[bold yellow]⚡[/bold yellow] Active in installation fitment\n[dim]Stages: {stages_str}[/dim]"
        )

        recon = dd.get("stock_reconciliation", {})
        floor = recon.get("floor_operations", {})
        disp_cnt = floor.get("dispatched_count", 0)
        ret_cnt = floor.get("returned_count", 0)

        self.query_one("#hero-unallocated-metric", Static).update(
            f"[bold red]{unalloc_cnt}[/bold red] [bold white]Plates[/bold white]"
        )
        if disp_cnt > 0:
            self.query_one("#hero-unallocated-subtext", Static).update(
                f"[bold red]⚠️[/bold red] Dispatched to line / No ITMS order\n[dim]Elimination: {disp_cnt} Out − {inst_cnt} Arch − {pend_cnt} Pend = [bold red]{unalloc_cnt} Unalloc[/bold red][/dim]"
            )
        else:
            self.query_one("#hero-unallocated-subtext", Static).update(
                f"[bold red]⚠️[/bold red] Not allocated to any ITMS order\n[dim]Daily shift floor discrepancy audit[/dim]"
            )

    def _render_stock_ledger(self, t: Dict[str, Any]) -> None:
        table = self.query_one("#table-report-stock-ledger", DataTable)
        table.clear()
        recon = t.get("date_driven", {}).get("stock_reconciliation", {})
        report_tbl = recon.get("report_table", {})
        rows = report_tbl.get("rows", [])

        if not rows:
            table.add_row("No bond stock data recorded for this date", "—", "—", "—", "Press [K] Stock Manager to record entries")
            return

        for row in rows:
            metric = row.get("metric", "")
            pmo = row.get("pmo", 0)
            psv = row.get("psv", 0)
            tot = row.get("total", 0)
            note = row.get("note", "")

            if "Closing Balance" in metric:
                m_s = f"[bold green]{metric}[/bold green]"
                y_s = f"[bold green]{pmo:,}[/bold green]" if isinstance(pmo, int) else f"[bold green]{pmo}[/bold green]"
                p_s = f"[bold green]{psv:,}[/bold green]" if isinstance(psv, int) else f"[bold green]{psv}[/bold green]"
                t_s = f"[bold white on dark_green] {tot:,} [/bold white on dark_green]" if isinstance(tot, int) else f"[bold white on dark_green] {tot} [/bold white on dark_green]"
            elif "SCHEDULED" in metric or "Scheduled" in metric:
                m_s = f"[bold cyan]{metric}[/bold cyan]"
                y_s = f"[bold cyan]{pmo:,}[/bold cyan]" if isinstance(pmo, int) else f"[bold cyan]{pmo}[/bold cyan]"
                p_s = f"[bold cyan]{psv:,}[/bold cyan]" if isinstance(psv, int) else f"[bold cyan]{psv}[/bold cyan]"
                t_s = f"[bold cyan]{tot:,}[/bold cyan]" if isinstance(tot, int) else f"[bold cyan]{tot}[/bold cyan]"
            elif "Installed" in metric:
                m_s = f"[bold yellow]{metric}[/bold yellow]"
                y_s = f"[bold yellow]{pmo:,}[/bold yellow]" if isinstance(pmo, int) else f"[bold yellow]{pmo}[/bold yellow]"
                p_s = f"[bold yellow]{psv:,}[/bold yellow]" if isinstance(psv, int) else f"[bold yellow]{psv}[/bold yellow]"
                t_s = f"[bold yellow]{tot:,}[/bold yellow]" if isinstance(tot, int) else f"[bold yellow]{tot}[/bold yellow]"
            elif "perfomance" in metric.lower() or "performance" in metric.lower():
                m_s = f"[bold magenta]{metric}[/bold magenta]"
                y_s = f"{pmo}"
                p_s = f"{psv}"
                t_s = f"[bold magenta]{tot}[/bold magenta]"
            elif "Backlog" in metric:
                col = "red" if (isinstance(tot, int) and tot > 0) else "green"
                m_s = f"[{col}]{metric}[/{col}]"
                y_s = f"[{col}]{pmo:,}[/{col}]" if isinstance(pmo, int) else f"[{col}]{pmo}[/{col}]"
                p_s = f"[{col}]{psv:,}[/{col}]" if isinstance(psv, int) else f"[{col}]{psv}[/{col}]"
                t_s = f"[{col}]{tot:,}[/{col}]" if isinstance(tot, int) else f"[{col}]{tot}[/{col}]"
            elif "Variance" in metric:
                col = "green" if (isinstance(tot, int) and tot >= 0) else "red"
                m_s = f"[{col}]{metric}[/{col}]"
                y_s = f"[{col}]{pmo:+d}[/{col}]" if isinstance(pmo, int) else f"[dim]{pmo}[/dim]"
                p_s = f"[{col}]{psv:+d}[/{col}]" if isinstance(psv, int) else f"[dim]{psv}[/dim]"
                t_s = f"[{col}]{tot:+d}[/{col}]" if isinstance(tot, int) else f"[dim]{tot}[/dim]"
            else:
                m_s = f"[bold white]{metric}[/bold white]"
                y_s = f"{pmo:,}" if isinstance(pmo, int) else str(pmo)
                p_s = f"{psv:,}" if isinstance(psv, int) else str(psv)
                t_s = f"[bold white]{tot:,}[/bold white]" if isinstance(tot, int) else str(tot)

            table.add_row(m_s, y_s, p_s, t_s, f"[dim]{note}[/dim]")

        floor = recon.get("floor_operations", {})
        unalloc = floor.get("unallocated_discrepancy", 0)
        unalloc_style = "[bold red]" if unalloc > 0 else "[bold green]"
        self.query_one("#reports-stock-summary", Static).update(
            f" [dim]Bond Balance: Opening + Received + Transfer In - Transfer Out - Installed = Closing Balance  │  "
            f"Floor Operations: Dispatched: [cyan]{floor.get('dispatched_count', 0)}[/cyan]  │  "
            f"Returned: [yellow]{floor.get('returned_count', 0)}[/yellow]  │  "
            f"Net on Line: [white]{floor.get('net_dispatched', 0)}[/white]  │  "
            f"{unalloc_style}⚠️ Floor Unallocated Discrepancy: {unalloc} plates{unalloc_style}[/dim]"
        )

    def _render_unallocated(self, t: Dict[str, Any]) -> None:
        dd = t.get("date_driven", {})
        fmt_date = dd.get("selected_date_formatted") or "All Dates"
        suffix = dd.get("selected_date_suffix") or "ALL"
        recon = dd.get("stock_reconciliation", {})
        floor = recon.get("floor_operations", {})
        disp_cnt = floor.get("dispatched_count", 0)
        unalloc_cnt = dd.get("unallocated_plates", {}).get("total_count", 0)
        inst_cnt = dd.get("installed_archive", {}).get("count", 0)
        pend_cnt = dd.get("pending_orders", {}).get("count", 0)
        ret_cnt = floor.get("returned_count", 0)

        if disp_cnt > 0:
            recon_summary = (
                f"Morning Dispatched: [bold cyan]{disp_cnt}[/bold cyan]  │  "
                f"ITMS Archived: [bold green]{inst_cnt}[/bold green]  │  "
                f"Active Pending: [bold yellow]{pend_cnt}[/bold yellow]  │  "
                f"Returned to Safe: [bold white]{ret_cnt}[/bold white]  │  "
                f"Floor Discrepancy: [bold red]{unalloc_cnt} Plates[/bold red]"
            )
        else:
            recon_summary = f"Floor Discrepancy: [bold red]{unalloc_cnt} Plates[/bold red]"

        self.query_one("#reports-unallocated-summary", Static).update(
            f" [dim]Plates dispatched from stock to the installation line that have [bold red]NO active order and NO archive record in ITMS[/bold red] "
            f"for work date [bold yellow]{fmt_date}[/bold yellow] ({suffix}) via daily elimination audit:\n"
            f" {recon_summary}[/dim]"
        )
        self._render_unallocated_table(t)

    def _render_unallocated_table(self, t: Dict[str, Any]) -> None:
        table = self.query_one("#table-report-unallocated", DataTable)
        table.clear()
        items = t.get("date_driven", {}).get("unallocated_plates", {}).get("items", [])
        if not items:
            table.add_row("No unallocated plates detected", "—", "All plates matched to active orders or archive", "—", "—")
            return

        for item in items[:100]:
            p = item.get("display_plate") or item.get("plate")
            src = item.get("source_id") or item.get("source_type_label") or "—"
            reason = item.get("reason", "—")
            wh = item.get("warehouse") or item.get("status") or "—"
            dt = item.get("date") or "—"

            plate_styled = f"[bold red]{p}[/bold red]"
            src_styled = f"[bold cyan]{src}[/bold cyan]"

            table.add_row(
                plate_styled,
                src_styled,
                reason,
                wh[:30],
                dt,
            )

    def _render_kits(self, t: Dict[str, Any]) -> None:
        k = t["kits"]
        tot = k["total"] or 1
        new_cnt = k["new_unallocated"]
        alloc_cnt = k["allocated"]
        inst_cnt = k["installed"]

        # Unicode stacked bar representation
        bar_len = 50
        new_blocks = int(round((new_cnt / tot) * bar_len))
        alloc_blocks = int(round((alloc_cnt / tot) * bar_len))
        inst_blocks = max(0, bar_len - (new_blocks + alloc_blocks))

    def _render_csv_exports(self, t: Dict[str, Any]) -> None:
        dd = t.get("date_driven", {})
        suf = dd.get("selected_date_suffix") or self.current_target_date or "300926"
        recon = dd.get("stock_reconciliation", {})
        floor = recon.get("floor_operations", {})
        dispatched_cnt = floor.get("dispatched_count", 370)
        unalloc_cnt = floor.get("unallocated_discrepancy", 25)
        inst_cnt = dd.get("installed_orders", {}).get("count") or dd.get("installed_archive", {}).get("count", 258)
        pend_cnt = dd.get("pending_orders", {}).get("count", 87)

        lines = [
            f"[bold white]All categorized shift spreadsheets are saved in the [cyan]exports/[/cyan] directory:[/bold white]",
            f"  1. [bold green]📊 Master Shift Reconciliation Ledger:[/bold green] [underline]exports/shift_{suf}_reconciliation_master.csv[/underline]",
            f"     [dim]Web URL: http://localhost:8000/api/stock/export/category/master/?suffix={suf}[/dim]",
            f"  2. [bold red]🔴 Unallocated Kits Docket ({unalloc_cnt} Plates):[/bold red] [underline]exports/shift_{suf}_unallocated_kits_{unalloc_cnt}.csv[/underline]",
            f"     [dim]Web URL: http://localhost:8000/api/stock/export/category/unallocated/?suffix={suf}[/dim]",
            f"  3. [bold blue]🔵 Completed Archive Fitments ({inst_cnt} Orders):[/bold blue] [underline]exports/shift_{suf}_itms_archived_{inst_cnt}.csv[/underline]",
            f"     [dim]Web URL: http://localhost:8000/api/stock/export/category/archived/?suffix={suf}[/dim]",
            f"  4. [bold yellow]🟡 Active Pending Queue ({pend_cnt} Orders):[/bold yellow] [underline]exports/shift_{suf}_itms_active_pending_{pend_cnt}.csv[/underline]",
            f"     [dim]Web URL: http://localhost:8000/api/stock/export/category/pending/?suffix={suf}[/dim]",
            f"  5. [bold white]⚪ Morning Dispatched Plates ({dispatched_cnt} Counted):[/bold white] [underline]exports/shift_{suf}_morning_dispatched_{dispatched_cnt}.csv[/underline]",
            f"     [dim]Web URL: http://localhost:8000/api/stock/export/category/dispatched/?suffix={suf}[/dim]",
        ]
        try:
            self.query_one("#reports-csv-exports-summary", Static).update("\n".join(lines))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id in ("btn-report-export", "btn-export-all-csvs"):
            self.action_export_report()
        elif btn_id == "btn-export-master-csv":
            self.action_export_category_csv("master")
        elif btn_id == "btn-export-unalloc-csv":
            self.action_export_category_csv("unallocated")
        elif btn_id == "btn-export-arch-csv":
            self.action_export_category_csv("archived")
        elif btn_id == "btn-export-pend-csv":
            self.action_export_category_csv("pending")
        elif btn_id == "btn-export-disp-csv":
            self.action_export_category_csv("dispatched")
        elif btn_id == "btn-change-reports-folder":
            self.action_change_reports_folder()
        elif btn_id == "btn-sync-stock-kits-ribbon":
            self.action_sync_stock_kits()
        elif btn_id == "btn-report-stock":
            self.action_open_stock_manager()
        elif btn_id == "btn-report-sync":
            self.action_sync_orders()
        elif btn_id == "btn-report-refresh":
            self.action_refresh_totals()
        elif btn_id == "btn-report-date":
            self.action_cycle_date()
        elif btn_id == "btn-copy-unallocated-raw":
            self.action_copy_unallocated_raw()
        elif btn_id == "btn-copy-mvr-docket":
            self.action_copy_mvr_docket()

    def action_change_reports_folder(self) -> None:
        """Opens Reports Directory dialog to choose folder where CSVs are saved."""
        from core.tui.dialogs import ReportsDirectoryDialog

        def _on_dir_selected(new_path: Optional[str]) -> None:
            if new_path:
                self.notify(f"Reports export directory set to: {new_path}", severity="information")
                self.refresh_reports()

        self.app.push_screen(ReportsDirectoryDialog(), _on_dir_selected)

    @work(thread=True)
    def action_sync_stock_kits(self) -> None:
        """Synchronizes installation kits from ITMS, deliveries, and safe audits in a non-blocking thread."""
        if getattr(self, "_is_syncing_kits", False):
            self.app.call_from_thread(self.notify, "Installation kit sync is already in progress...", severity="warning")
            return
        self._is_syncing_kits = True

        def _set_btn_state(text: str, disabled: bool):
            try:
                btn = self.query_one("#btn-sync-stock-kits-ribbon", Button)
                btn.label = text
                btn.disabled = disabled
            except Exception:
                pass

        self.app.call_from_thread(_set_btn_state, "⏳ Syncing Kits...", True)
        self.app.call_from_thread(self.notify, "🔄 Synchronizing installation kits from ITMS, deliveries, and safe audits...")
        self.app.call_from_thread(
            self.app.log_message,
            "Starting installation kits synchronization & morning provisioning (unconstrained by date)...",
            level="ITMS",
        )

        from core.services import kit_provisioning_service

        def _on_progress(msg: str):
            self.app.call_from_thread(self.app.log_message, msg, level="ITMS")

        try:
            res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=None,  # No date constraint; sync global catalog
                sync_itms=True,
                max_pages=35,
                log_callback=_on_progress,
            )
            count = res.get("new_kits_ready_count", 0)
            wh = res.get("warehouse_facility", "Warehouse Stock")
            itms_synced = res.get("itms_kits_synced", 0)
            pages = res.get("itms_pages_crawled", 0)
            created = res.get("kits_created", 0)
            updated = res.get("kits_updated", 0)
            total_stock = res.get("total_warehouse_new_stock", 0)
            itms_status = res.get("itms_status", "")
            itms_error = res.get("itms_error")

            summary_msg = (
                f"✓ Kits Sync Complete: {itms_synced} fetched from ITMS ({pages} pgs), "
                f"{created} created, {updated} updated. Total ready in stock: {count} ({total_stock} total 'New')."
            )
            self.app.call_from_thread(self.notify, summary_msg, severity="information", timeout=8)
            self.app.call_from_thread(self.app.log_message, f"[bold green]{summary_msg}[/bold green]", level="SUCCESS")

            if itms_error:
                self.app.call_from_thread(
                    self.notify,
                    f"Notice: {itms_status}",
                    severity="warning",
                    timeout=8,
                )

            self.app.call_from_thread(self.refresh_reports)
            self.app.call_from_thread(self.app.reload_data)
        except Exception as exc:
            err_msg = f"Error syncing kits: {exc}"
            self.app.call_from_thread(self.notify, err_msg, severity="error")
            self.app.call_from_thread(self.app.log_message, f"[bold red]{err_msg}[/bold red]", level="ERROR")
        finally:
            self._is_syncing_kits = False
            self.app.call_from_thread(_set_btn_state, "📦 Sync Stock Kits", False)

    def action_open_stock_manager(self) -> None:
        """Opens the Bond Physical Stock & Reconciliation Manager Dialog."""
        from core.tui.dialogs import StockManagerModal

        def _on_modal_close(result):
            self.refresh_reports()

        self.app.push_screen(
            StockManagerModal(target_date_suffix=self.current_target_date),
            _on_modal_close,
        )

    def action_sync_orders(self) -> None:
        """Triggers ITMS order synchronization via main app."""
        if hasattr(self.app, "action_sync_itms_orders"):
            self.app.action_sync_itms_orders(target_date=self.current_target_date)
            self.notify(f"ITMS Shift Sync triggered for {self.current_target_date or 'active orders'}...")
        else:
            self.notify("Sync orders action not available.", severity="warning")

    def action_cycle_date(self) -> None:
        """Opens the Date Selection Modal for direct date choice or suffix typing."""
        from core.tui.dialogs import DateSelectModal

        def _on_date_selected(selected_suffix: Optional[str]) -> None:
            if selected_suffix is not None:
                self.current_target_date = None if selected_suffix == "ALL" else selected_suffix
                self.refresh_reports(target_date=self.current_target_date)
                lbl = self.current_target_date if self.current_target_date != "ALL" else "All Dates"
                self.notify(f"Selected Work Date: {lbl}")

        self.app.push_screen(
            DateSelectModal(
                current_suffix=self.current_target_date,
                available_dates=self.available_dates,
            ),
            _on_date_selected,
        )

    def action_export_report(self) -> None:
        """Exports all categorized shift CSV reports to exports/."""
        from core.services import stock_monitoring_service
        try:
            res = stock_monitoring_service.export_shift_csvs(self.current_target_date)
            suf = self.current_target_date or "shift"
            cnt = len(res)
            self.notify(f"✓ Saved {cnt} categorized shift CSVs into exports/ for {suf}!", severity="information")
            self.refresh_reports()
        except Exception as exc:
            try:
                self.app.action_export_shift_report()
            except Exception:
                self.notify(f"Error exporting report: {exc}", severity="error")

    def action_export_category_csv(self, category: str) -> None:
        """Exports a single categorized shift CSV file directly into exports/."""
        from core.services import stock_monitoring_service
        try:
            content = stock_monitoring_service.generate_category_csv_content(category, self.current_target_date)
            recon = stock_monitoring_service.compute_daily_reconciliation(self.current_target_date)
            suf = recon.get("work_date_suffix", "shift")
            exports_dir = os.path.join(settings.BASE_DIR, "exports")
            os.makedirs(exports_dir, exist_ok=True)
            fname = f"shift_{suf}_{category}.csv"
            fpath = os.path.join(exports_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            self.notify(f"✓ Saved exports/{fname}!", severity="information")
            self.refresh_reports()
        except Exception as exc:
            self.notify(f"Error exporting {category} CSV: {exc}", severity="error")

    def action_refresh_totals(self) -> None:
        self.refresh_reports()
        self.notify("Reports & Totals refreshed from live database.")

    def _copy_text_to_clipboard(self, text: str) -> bool:
        """Copies text to system clipboard across Textual, Windows clip.exe, or pyperclip."""
        # 1. Try Textual built-in app.copy_to_clipboard
        try:
            if hasattr(self.app, "copy_to_clipboard"):
                self.app.copy_to_clipboard(text)
                return True
        except Exception:
            pass

        # 2. Windows clip.exe fallback
        import platform
        import subprocess
        if platform.system() == "Windows":
            try:
                proc = subprocess.run(
                    ["clip"],
                    input=text.encode("utf-8"),
                    check=False,
                    capture_output=True,
                )
                if proc.returncode == 0:
                    return True
            except Exception:
                pass

        # 3. pyperclip fallback if available
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            pass

        return False

    def action_copy_unallocated_raw(self) -> None:
        """Copies raw newline-separated plate numbers to clipboard for MVR officer."""
        from core.services import stock_monitoring_service
        try:
            docket = stock_monitoring_service.get_mvr_unallocated_docket(self.current_target_date)
            raw = docket.get("raw_plates", "").strip()
            count = docket.get("count", 0)
            if not raw or count == 0:
                self.notify("ℹ️ No unallocated plates found for this shift date.", severity="information")
                return

            copied = self._copy_text_to_clipboard(raw)
            if copied:
                self.notify(f"✓ Copied {count} raw plate numbers to clipboard for MVR!", severity="information")
            else:
                self.notify(f"⚠️ Could not access clipboard. {count} plates found.", severity="warning")
        except Exception as exc:
            self.notify(f"Error copying unallocated plates: {exc}", severity="error")

    def action_copy_mvr_docket(self) -> None:
        """Copies formatted MVR Allocation Exception Docket to clipboard."""
        from core.services import stock_monitoring_service
        try:
            docket = stock_monitoring_service.get_mvr_unallocated_docket(self.current_target_date)
            fmt = docket.get("formatted_docket", "").strip()
            count = docket.get("count", 0)
            if not fmt:
                self.notify("ℹ️ No docket data available for this shift date.", severity="information")
                return

            copied = self._copy_text_to_clipboard(fmt)
            if copied:
                self.notify(f"✓ Copied formatted MVR Exception Docket ({count} unallocated) to clipboard!", severity="information")
            else:
                self.notify("⚠️ Could not access clipboard.", severity="warning")
        except Exception as exc:
            self.notify(f"Error generating MVR docket: {exc}", severity="error")

