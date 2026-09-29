"""
Reports & System Totals Pane (Tab 7) for the ITMS Operator TUI.

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

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from core.services import auth_service, report_service


class ReportsPane(VerticalScroll):
    """Interactive Reports & Totals Pane (Tab 7)."""

    BINDINGS = [
        Binding("t", "cycle_date", "Cycle Date"),
        Binding("s", "sync_orders", "Sync Orders"),
        Binding("k", "open_stock_manager", "Stock Manager"),
        Binding("d", "toggle_scope", "Toggle Scope"),
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
            # 1. Header Ribbon Banner
            yield Static(id="reports-header-banner")

            # 2. Operational Actions Bar
            with Horizontal(id="reports-actions-bar"):
                yield Button("🗓️ Date: LATEST [T]", variant="primary", id="btn-report-date")
                yield Button("🔄 Sync Orders [S]", variant="warning", id="btn-report-sync")
                yield Button("📦 Stock Manager [K]", variant="success", id="btn-report-stock")
                yield Button("📑 Export CSV [E]", variant="default", id="btn-report-export")
                yield Button("🔄 Refresh [R]", variant="default", id="btn-report-refresh")
                yield Button("📅 Scope: ALL [D]", variant="default", id="btn-report-scope")
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

            # 3.5 Bond Physical Stock Ledger Report (PSV White vs PMO Yellow)
            with Vertical(classes="report-card", id="card-report-stock-ledger"):
                yield Static("[bold cyan]📦 Physical Warehouse Stock & Bond Reconciliation Balance[/bold cyan]", classes="card-title")
                yield Static(id="reports-stock-summary")
                yield DataTable(id="table-report-stock-ledger")

            # 4. Unallocated Plates Audit DataTable
            with Vertical(classes="report-card", id="card-report-unallocated"):
                yield Static("[bold red]⚠️ Unallocated Plates Audit (Taken Out of Stock / No Order in ITMS)[/bold red]", classes="card-title")
                yield Static(id="reports-unallocated-summary")
                with Horizontal(classes="unallocated-actions-row"):
                    yield Button("📋 Copy Raw Plates", variant="primary", id="btn-copy-unallocated-raw")
                    yield Button("📑 Copy MVR Docket", variant="warning", id="btn-copy-mvr-docket")
                yield DataTable(id="table-report-unallocated")

            # 5. ITMS Installation Kits & Warehouse Stock Breakdown
            with Vertical(classes="report-card", id="card-report-kits"):
                yield Static("[bold green]📦 ITMS Installation Kits & Warehouse Stock Breakdown[/bold green]", classes="card-title")
                yield Static(id="reports-kits-summary")
                yield Static("[bold white]Warehouse Distribution Breakdown:[/bold white]", classes="report-subheading")
                yield DataTable(id="table-report-warehouses")

            # 6. Verification Queue & Telemetry (Two-column Split Row)
            with Horizontal(id="reports-split-row"):
                with Vertical(classes="report-card half-card", id="card-report-queue"):
                    yield Static("[bold yellow]📋 Verification Queue & Success Rates[/bold yellow]", classes="card-title")
                    yield Static(id="reports-queue-summary")

                with Vertical(classes="report-card half-card", id="card-report-hardware"):
                    yield Static("[bold cyan]🏷️ Hardware Serials & Shift Telemetry[/bold cyan]", classes="card-title")
                    yield Static(id="reports-hardware-summary")

    def on_mount(self) -> None:
        table_stock = self.query_one("#table-report-stock-ledger", DataTable)
        table_stock.add_columns("Description Metric", "Public White (PSV)", "Private Yellow (PMO)", "Total Combined (Bond)", "Formula / Description")
        table_stock.cursor_type = "row"

        table_unalloc = self.query_one("#table-report-unallocated", DataTable)
        table_unalloc.add_columns("Plate Number", "Source / ID", "Audit Reason / Status", "Warehouse / Detail", "Detected / Date")
        table_unalloc.cursor_type = "row"

        table_wh = self.query_one("#table-report-warehouses", DataTable)
        table_wh.add_columns("Warehouse Location", "Total Kits", "New (Unallocated)", "Allocated", "Installed")
        table_wh.cursor_type = "row"

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
        self._render_kits(totals)
        self._render_warehouse_table(totals)
        self._render_queue(totals)
        self._render_hardware(totals)

    def _render_header(self, t: Dict[str, Any]) -> None:
        user = auth_service.get_remembered_session()
        op_name = (user.get_full_name() or user.username) if user else "Operator"
        scope_lbl = "TODAY'S SHIFT" if self.current_scope == "TODAY" else "ALL-TIME"

        dd = t.get("date_driven", {})
        suf = dd.get("selected_date_suffix", "ALL")
        fmt_date = dd.get("selected_date_formatted", "All Dates")

        self.query_one("#reports-header-banner", Static).update(
            f"[bold cyan]📊 DAILY WORK & PLATE ALLOCATION REPORT[/bold cyan]  │  "
            f"[bold white]Focus Date:[/bold white] [bold yellow]{fmt_date}[/bold yellow] ([cyan]{suf}[/cyan])  │  "
            f"[bold white]Scope:[/bold white] [bold green]{scope_lbl}[/bold green]  │  "
            f"[bold white]Operator:[/bold white] [bold white]{op_name}[/bold white]  │  "
            f"[dim]Synced: {t.get('generated_at', '')}[/dim]"
        )

        btn_scope = self.query_one("#btn-report-scope", Button)
        btn_scope.label = f"📅 Scope: {scope_lbl} [D]"

        btn_date = self.query_one("#btn-report-date", Button)
        btn_date.label = f"🗓️ Date: {suf} [T]"

        self.query_one("#reports-status-indicator", Static).update(
            f"[dim]• DB Live Synced • Orders: [bold white]{t['orders']['total']}[/bold white] • Kits: [bold white]{t['kits']['total']}[/bold white] • Pairs: [bold white]{t['pairs']['total']}[/bold white][/dim]"
        )

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

        self.query_one("#hero-unallocated-metric", Static).update(
            f"[bold red]{unalloc_cnt}[/bold red] [bold white]Plates[/bold white]"
        )
        self.query_one("#hero-unallocated-subtext", Static).update(
            f"[bold red]⚠️[/bold red] Not allocated to any ITMS order\n[dim][red]{failed_link_cnt}[/red] unlinked pairs  │  [green]{stock_kits_cnt}[/green] stock kits 'New'[/dim]"
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
            psv = row.get("psv", 0)
            pmo = row.get("pmo", 0)
            tot = row.get("total", 0)
            note = row.get("note", "")

            if "Closing Balance" in metric:
                m_s = f"[bold green]{metric}[/bold green]"
                p_s = f"[bold green]{psv:,}[/bold green]"
                y_s = f"[bold green]{pmo:,}[/bold green]"
                t_s = f"[bold white on dark_green] {tot:,} [/bold white on dark_green]"
            elif "Scheduled" in metric:
                m_s = f"[bold cyan]{metric}[/bold cyan]"
                p_s = f"[bold cyan]{psv:,}[/bold cyan]"
                y_s = f"[bold cyan]{pmo:,}[/bold cyan]"
                t_s = f"[bold cyan]{tot:,}[/bold cyan]"
            elif "Variance" in metric:
                col = "green" if tot >= 0 else "red"
                m_s = f"[{col}]{metric}[/{col}]"
                p_s = f"[{col}]{psv:+d}[/{col}]"
                y_s = f"[{col}]{pmo:+d}[/{col}]"
                t_s = f"[{col}]{tot:+d}[/{col}]"
            else:
                m_s = f"[bold white]{metric}[/bold white]"
                p_s = f"{psv:,}"
                y_s = f"{pmo:,}"
                t_s = f"[bold white]{tot:,}[/bold white]"

            table.add_row(m_s, p_s, y_s, t_s, f"[dim]{note}[/dim]")

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

        self.query_one("#reports-unallocated-summary", Static).update(
            f" [dim]Plates taken out from stock or detected by OCR that have [bold red]NO active order and NO archive record[/bold red] "
            f"for work date [bold yellow]{fmt_date}[/bold yellow] ({suffix}), plus stock kits whose status is still 'New' in warehouse stock.[/dim]"
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

            if item.get("source") == "PAIR_LINK_FAILED":
                plate_styled = f"[bold red]{p}[/bold red]"
                src_styled = f"[bold yellow]{src}[/bold yellow]"
            else:
                plate_styled = f"[bold green]{p}[/bold green]"
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

        bar_visual = (
            f"[green]{'█' * new_blocks}[/green]"
            f"[yellow]{'█' * alloc_blocks}[/yellow]"
            f"[blue]{'█' * inst_blocks}[/blue]"
        )

        lines = [
            f"[bold white]Installation Kits Stock Health:[/bold white] {bar_visual}  [dim](Total: {k['total']})[/dim]",
            f" • [bold green]🟢 New / Unallocated Stock:[/bold green] [bold white]{new_cnt}[/bold white] kits ({k['unallocated_pct']}) "
            f"[dim]— Plates created in ITMS stock but not yet assigned to an installation order.[/dim]",
            f" • [bold yellow]🟡 Allocated to Orders:[/bold yellow]     [bold white]{alloc_cnt}[/bold white] kits "
            f"[dim]— Actively matched to vehicle installations currently in progress.[/dim]",
            f" • [bold cyan]🔵 Installed (Archive):[/bold cyan]         [bold white]{inst_cnt}[/bold white] kits "
            f"[dim]— Physical fitment complete and photo evidence officially approved on ITMS.[/dim]",
        ]
        self.query_one("#reports-kits-summary", Static).update("\n".join(lines))

    def _render_warehouse_table(self, t: Dict[str, Any]) -> None:
        table = self.query_one("#table-report-warehouses", DataTable)
        table.clear()
        warehouses = t.get("kits", {}).get("warehouses", [])
        if not warehouses:
            table.add_row("No warehouses found in local database", "0", "0", "0", "0")
            return

        for w in warehouses:
            table.add_row(
                w.get("warehouse", "—"),
                str(w.get("total", 0)),
                f"[bold green]{w.get('new', 0)}[/bold green]",
                f"[bold yellow]{w.get('allocated', 0)}[/bold yellow]",
                f"[bold cyan]{w.get('installed', 0)}[/bold cyan]",
            )

    def _render_queue(self, t: Dict[str, Any]) -> None:
        pr = t["pairs"]
        lines = [
            f" [b]Approved (Ready to Submit):[/b] [bold green]{pr['approved']}[/bold green]  │  "
            f"[b]Submitted to ITMS:[/b] [bold cyan]{pr['submitted']}[/bold cyan]  │  "
            f"[b]Success Rate:[/b] [bold green]{pr['submission_success_rate']}[/bold green]",
            f" [b]Pending Operator Review:[/b]   [bold yellow]{pr['pending_review']}[/bold yellow]  │  "
            f"[b]Incomplete (1 Photo):[/b] [yellow]{pr['incomplete']}[/yellow]  │  "
            f"[b]Color/Plate Conflicts:[/b] [bold red]{pr['conflict']}[/bold red]",
            f" [b]Submission Failures:[/b]        [bold red]{pr['failed']}[/bold red]  │  "
            f"[b]Offline Outbox Queue:[/b] [bold magenta]{pr['offline_outbox']}[/bold magenta]  │  "
            f"[b]Manual Overrides:[/b] [cyan]{pr['manual_overrides']}[/cyan]",
        ]
        self.query_one("#reports-queue-summary", Static).update("\n".join(lines))

    def _render_hardware(self, t: Dict[str, Any]) -> None:
        hw = t["hardware"]
        cat = t["categories"]
        p = t["photos"]
        b = t["batches"]
        lines = [
            f" [b]Shift Telemetry Today:[/b] [bold green]{p['today']}[/bold green] photos  │  "
            f"[bold yellow]{b['today']}[/bold yellow] batches  │  "
            f"[bold cyan]{t['pairs']['today_submitted']}[/bold cyan] submitted",
            f" [b]Hardware Serials Accounted:[/b]",
            f"  • Front: [cyan]{hw['front_plates']}[/cyan]  │  Rear: [cyan]{hw['rear_plates']}[/cyan]  │  GPS: [cyan]{hw['gps_trackers']}[/cyan]  │  BLE: [cyan]{hw['ble_trackers']}[/cyan]",
            f" [b]Vehicle Classes:[/b] [bold white on dark_blue] PSV [/] [cyan]{cat['psv_commercial']}[/cyan]  │  "
            f"[bold black on gold1] PMO [/] [yellow]{cat['pmo_private']}[/yellow]  │  "
            f"[bold white on red] CONFLICTS [/] [bold red]{cat['color_conflicts']}[/bold red]",
        ]
        self.query_one("#reports-hardware-summary", Static).update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-report-export":
            try:
                self.app.action_export_shift_report()
            except Exception:
                pass
        elif btn_id == "btn-report-stock":
            self.action_open_stock_manager()
        elif btn_id == "btn-report-sync":
            self.action_sync_orders()
        elif btn_id == "btn-report-refresh":
            self.action_refresh_totals()
        elif btn_id == "btn-report-scope":
            self.action_toggle_scope()
        elif btn_id == "btn-report-date":
            self.action_cycle_date()
        elif btn_id == "btn-copy-unallocated-raw":
            self.action_copy_unallocated_raw()
        elif btn_id == "btn-copy-mvr-docket":
            self.action_copy_mvr_docket()

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
        """Cycles through available order dates or ALL."""
        options = [d["suffix"] for d in self.available_dates if d.get("order_count", 0) > 0]
        if "ALL" not in options:
            options.append("ALL")
        if not options:
            return

        try:
            curr_idx = options.index(self.current_target_date) if self.current_target_date in options else -1
        except ValueError:
            curr_idx = -1

        next_idx = (curr_idx + 1) % len(options)
        self.current_target_date = options[next_idx]
        self.refresh_reports(target_date=self.current_target_date)
        lbl = self.current_target_date if self.current_target_date != "ALL" else "All Dates"
        self.notify(f"Switched work date focus to: {lbl}")

    def action_toggle_scope(self) -> None:
        next_scope = "TODAY" if self.current_scope == "ALL" else "ALL"
        self.refresh_reports(scope=next_scope)
        self.notify(f"Reports scope switched to: {next_scope}")

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

