"""
ITMS Web Application Connection & Safe Read-Only Verification Hub.

Provides:
- Real-time session monitoring for https://stock.itms.ug
- Sub-view navigation filter (Press [F] to cycle: Connect -> Orders -> Archive)
- Standard cookie authentication & 30-day session persistence
- Interactive DataTables for Active Orders & Completed Archive
- Side-by-side photographic evidence viewer (Press [V] on any order)
- Safe vault storage for downloaded evidence photos (media/vault/itms_photos/<order_number>/)
- Zero write / zero live fitment submission safety enforcement
"""
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    RichLog,
    Static,
)

from core.services.itms_web_client import ITMSWebClient, get_web_client
from core.services import viewer
from core.services.config_service import get_config_service
from core.tui.inspectors import InspectorPane

ITMS_SUBVIEWS = ["CONNECT", "ORDERS", "ARCHIVE"]
SUBVIEW_LABELS = {
    "CONNECT": "🔑 Connect to Account",
    "ORDERS": "📥 Active Orders",
    "ARCHIVE": "🏛️ Completed Archive",
}


class ITMSConnectionPane(Vertical):
    """Main panel widget for the [4] ITMS WebApp navigation tab."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client: ITMSWebClient = get_web_client()
        self.current_subview: str = "CONNECT"
        self._last_fetched_orders: List[Dict[str, Any]] = []
        self._last_active_orders: List[Dict[str, Any]] = []
        self._last_archive_orders: List[Dict[str, Any]] = []
        self._active_orders_map: Dict[str, Dict[str, Any]] = {}
        self._archive_orders_map: Dict[str, Dict[str, Any]] = {}
        self._highlighted_order: Optional[Dict[str, Any]] = None
        self._is_archive_mode: bool = False

    def compose(self) -> ComposeResult:
        # Top Banner (Dynamic mode badge based on loaded config)
        yield Static(self._render_top_bar(), id="itms-top-bar")

        # Subview Filter Navigation Bar (Press [F] to cycle)
        yield Static(self._render_filter_bar(), id="itms-subview-filter-bar")

        # ──────────────────────────────────────────────────────────────────────
        # SUB-VIEW 1: Connect to Account
        # ──────────────────────────────────────────────────────────────────────
        with Horizontal(id="itms-view-connect", classes="itms-view-container"):
            # Left Column: Live Status & Safety Guarantees
            with Vertical(id="itms-status-column"):
                yield Static(self._render_status_card(), id="itms-status-card")
                show_safety = get_config_service().get_setting("system.show_safety_guarantee", False)
                yield Static(
                    self._render_safety_card(),
                    id="itms-safety-card",
                    classes="" if show_safety else "hidden",
                )

            # Right Column: Authentication & Server Controls
            with Vertical(id="itms-controls-column"):
                yield Static("[bold white]🔑 ITMS WebApp Session Credentials[/bold white]", classes="itms-section-title")
                yield Input(
                    placeholder="ITMS WebApp Base URL",
                    value=self.client.base_url,
                    id="input-itms-url",
                )
                yield Input(
                    placeholder="ITMS Username / Email",
                    id="input-itms-email",
                )
                yield Input(
                    placeholder="ITMS Password",
                    password=True,
                    id="input-itms-password",
                )
                yield Checkbox(
                    "Remember Session (30 Days Cookie)",
                    value=True,
                    id="chk-itms-remember",
                )

                with Horizontal(classes="itms-action-row"):
                    yield Button("Connect & Sign In", variant="primary", id="btn-itms-login")
                    yield Button("Ping Server", variant="default", id="btn-itms-ping")
                    yield Button("Verify Session", variant="success", id="btn-itms-verify")

                with Horizontal(classes="itms-action-row"):
                    yield Button("Preview Dashboard", variant="default", id="btn-itms-preview-dash")
                    yield Button("Sign Out ITMS Account", variant="error", id="btn-itms-logout")

        # ──────────────────────────────────────────────────────────────────────
        # SUB-VIEW 2: Active Orders (65% Table Panel / 35% Inspector Panel)
        # ──────────────────────────────────────────────────────────────────────
        with Horizontal(id="itms-view-orders", classes="itms-view-container tab-horizontal"):
            with Vertical(classes="table-panel"):
                yield Static("[bold white]📥 Live Active Fitment Orders (GET /installation-orders/index)[/bold white]", classes="itms-section-title")
                with Horizontal(classes="itms-toolbar-row"):
                    yield Input(placeholder="Filter Plate (e.g. UMA 946DQ) / VIN / Status or paste URL", id="input-itms-search-plate", classes="itms-input-filter")
                    yield Input(placeholder="Pg", value="1", id="input-itms-page", classes="itms-input-page")
                    yield Button("Fetch", variant="primary", id="btn-itms-fetch-orders", classes="itms-btn-fetch")
                    yield Button("◄", variant="default", id="btn-itms-prev-page", classes="itms-btn-nav")
                    yield Button("►", variant="default", id="btn-itms-next-page", classes="itms-btn-nav")

                with Horizontal(classes="itms-actions-toolbar"):
                    yield Button("👁️ Photos (V)", variant="warning", id="btn-itms-view-photos")
                    yield Button("🔍 Order Details", variant="default", id="btn-itms-order-info")
                    yield Button("💾 Download Vault", variant="default", id="btn-itms-download-photos")
                    yield Button("🔄 Sync to DB", variant="success", id="btn-itms-sync-orders")

                yield DataTable(id="table-itms-orders", classes="itms-table")

            yield InspectorPane(id="inspector-itms-orders", classes="inspector-panel")

        # ──────────────────────────────────────────────────────────────────────
        # SUB-VIEW 3: Completed Archive (65% Table Panel / 35% Inspector Panel)
        # ──────────────────────────────────────────────────────────────────────
        with Horizontal(id="itms-view-archive", classes="itms-view-container tab-horizontal"):
            with Vertical(classes="table-panel"):
                yield Static("[bold white]🏛️ Completed Installation Orders Archive (GET /installation-orders/archive)[/bold white]", classes="itms-section-title")
                with Horizontal(classes="itms-toolbar-row"):
                    yield Input(placeholder="Filter Plate (e.g. UMA 282PG) / Order # / VIN or paste URL", id="input-itms-archive-filter", classes="itms-input-filter")
                    yield Input(placeholder="Pg", value="1", id="input-itms-archive-page", classes="itms-input-page")
                    yield Button("Fetch", variant="warning", id="btn-itms-fetch-archive", classes="itms-btn-fetch")
                    yield Button("◄", variant="default", id="btn-itms-archive-prev", classes="itms-btn-nav")
                    yield Button("►", variant="default", id="btn-itms-archive-next", classes="itms-btn-nav")

                with Horizontal(classes="itms-actions-toolbar"):
                    yield Button("👁️ Photos (V)", variant="warning", id="btn-itms-archive-view-photos")
                    yield Button("🔍 Order Details", variant="default", id="btn-itms-archive-info")
                    yield Button("💾 Download Vault", variant="default", id="btn-itms-archive-download")
                    yield Button("🔄 Sync to DB", variant="success", id="btn-itms-archive-sync")

                yield DataTable(id="table-itms-archive", classes="itms-table")

            yield InspectorPane(id="inspector-itms-archive", classes="inspector-panel")

    def on_mount(self) -> None:
        self._last_fetched_orders = []
        self._last_active_orders = []
        self._last_archive_orders = []
        self._active_orders_map = {}
        self._archive_orders_map = {}
        self._highlighted_order = None

        # Setup Active Orders Table
        orders_table = self.query_one("#table-itms-orders", DataTable)
        orders_table.add_columns("# (Order)", "Plate", "VIN / Chassis", "Status", "Sales Order", "Warehouse")
        orders_table.cursor_type = "row"

        # Setup Archive Table
        archive_table = self.query_one("#table-itms-archive", DataTable)
        archive_table.add_columns("# (Order)", "Plate", "VIN / Chassis", "Order Status", "Reg Status", "Officer", "Date")
        archive_table.cursor_type = "row"

        # Pre-fill email if operator preferences or session exist
        status = self.client.get_status()
        if status.get("user_email"):
            email_input = self.query_one("#input-itms-email", Input)
            email_input.value = status["user_email"]

        self._refresh_status_card()
        # Initialize sub-view display
        self.set_subview(self.current_subview)

        self._log_preview("[dim]🌐 ITMS Hub ready. Press [b yellow]F[/b yellow] to cycle views (Connect, Orders, Archive). Press [b yellow]V[/b yellow] to view photos side-by-side.[/dim]")
        try:
            self.query_one("#inspector-itms-orders", InspectorPane).show_itms_order(None, is_archive=False)
            self.query_one("#inspector-itms-archive", InspectorPane).show_itms_order(None, is_archive=True)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Sub-View Filter Navigation (Cycle with [F])
    # ──────────────────────────────────────────────────────────────────────────

    def _render_filter_bar(self) -> str:
        pills = []
        for sv in ITMS_SUBVIEWS:
            lbl = SUBVIEW_LABELS[sv]
            if sv == self.current_subview:
                pills.append(f"[bold green]▶ {lbl}[/bold green]")
            else:
                pills.append(f"[dim]{lbl}[/dim]")
        pills_str = "   ".join(pills)
        return f"[b]ITMS Navigation View:[/b]   {pills_str}   [dim](Press [b yellow]F[/b yellow] to cycle sub-view)[/dim]"

    def action_cycle_filter(self) -> None:
        """Cycles between Connect, Active Orders, and Archive subviews."""
        idx = (ITMS_SUBVIEWS.index(self.current_subview) + 1) % len(ITMS_SUBVIEWS)
        self.set_subview(ITMS_SUBVIEWS[idx])

    def set_subview(self, subview: str) -> None:
        if subview not in ITMS_SUBVIEWS:
            return
        self.current_subview = subview
        self._is_archive_mode = (subview == "ARCHIVE")

        # Update filter pill bar
        bar = self.query_one("#itms-subview-filter-bar", Static)
        bar.update(self._render_filter_bar())

        # Toggle container visibility
        connect_view = self.query_one("#itms-view-connect")
        orders_view = self.query_one("#itms-view-orders")
        archive_view = self.query_one("#itms-view-archive")

        connect_view.display = (subview == "CONNECT")
        orders_view.display = (subview == "ORDERS")
        archive_view.display = (subview == "ARCHIVE")

        if subview == "CONNECT":
            self._refresh_status_card()
            self._set_feedback("Sub-view: Account Connection & Session Management", "cyan")
        elif subview == "ORDERS":
            self._set_feedback("Sub-view: Active Fitment Orders (↑/↓ select row │ [V] view photos)", "cyan")
            table = self.query_one("#table-itms-orders", DataTable)
            if table.row_count == 0 and self.client.session_store.session.is_cookie_valid() and not getattr(self, "_is_fetching_orders", False):
                self.action_fetch_orders(archive=False)
        elif subview == "ARCHIVE":
            self._set_feedback("Sub-view: Completed Orders Archive (↑/↓ select row │ [V] view photos)", "cyan")
            table = self.query_one("#table-itms-archive", DataTable)
            if table.row_count == 0 and self.client.session_store.session.is_cookie_valid() and not getattr(self, "_is_fetching_orders", False):
                self.action_fetch_orders(archive=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Status & Feedback Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _render_top_bar(self) -> str:
        cfg = get_config_service()
        dry_run = cfg.get_setting("submission.dry_run_mode", True)
        base_url = cfg.get_setting("network.itms_base_url", "https://stock.itms.ug")
        if dry_run:
            badge = "[bold white on dark_green] 🔒 READ-ONLY AUDIT (DRY-RUN) [/bold white on dark_green]"
            desc = "[dim]Safe inspection mode │ Submissions simulated[/dim]"
        else:
            badge = "[bold white on dark_red] ⚡ LIVE SUBMISSION MODE [/bold white on dark_red]"
            desc = "[dim]Mutating fitment & evidence upload enabled[/dim]"
        return f"🌐 [bold cyan]ITMS Hub[/bold cyan] [dim]({base_url})[/dim]  │  {badge}  {desc}"

    def _render_safety_card(self) -> str:
        cfg = get_config_service()
        dry_run = cfg.get_setting("submission.dry_run_mode", True)
        step3 = cfg.get_setting("submission.submit_step3", True)
        timeout = cfg.get_setting("submission.request_timeout_seconds", 30)
        cb = cfg.get_setting("submission.circuit_breaker_threshold", 3)
        retention = cfg.get_setting("storage.vault_retention_days", 7)
        db_conf = cfg.get_database_config()
        db_engine = db_conf.get("ENGINE", "").split(".")[-1].upper() or "SQLITE"

        if dry_run:
            return (
                "[bold green]🔒 SAFE AUDIT GUARANTEE (CONFIG LOADED)[/bold green]\n"
                "[dim]• Verification mode is STRICTLY READ-ONLY (Simulated GET requests only).\n"
                "• ZERO vehicle registration or fitment updates are transmitted.\n"
                f"• Active Storage: {db_engine} Database │ Vault retention: {retention}d.\n"
                f"• Rate-Limit & Protection: {timeout}s timeout │ Circuit Breaker: {cb} errors.\n"
                "• Cookies cached in secure vault (.itms_web_session.json).[/dim]"
            )
        else:
            step3_status = "Auto-Finalize Enabled" if step3 else "Manual Confirmation Required"
            return (
                "[bold red]⚡ LIVE SUBMISSION ACTIVE (CONFIG LOADED)[/bold red]\n"
                "[dim]• Live Mode: POST requests & remote mutations ACTIVE.\n"
                "• Vehicle registration updates WILL be sent to remote ITMS.\n"
                f"• Step 3 Installation Finalization: {step3_status}.\n"
                f"• Safeguards: {timeout}s timeout │ Circuit Breaker trips after {cb} failures.\n"
                f"• Vault Retention: {retention} days for evidence archives.[/dim]"
            )

    def _render_status_card(self) -> str:
        status = self.client.get_status()
        is_auth = status.get("authenticated", False)
        auth_color = "bold green" if is_auth else "bold yellow"
        auth_tag = "ACTIVE (Authenticated)" if is_auth else "DISCONNECTED"

        user_email = status.get("user_email") or "[dim]Not signed in[/dim]"
        user_uuid = status.get("user_uuid") or "[dim]N/A[/dim]"
        days_left = status.get("expires_in_days", 0)
        expiry_info = f"{days_left} days remaining" if days_left > 0 else "[dim]None[/dim]"

        verified_at = status.get("last_verified_at", 0)
        if verified_at > 0:
            ago_secs = int(time.time() - verified_at)
            verified_str = f"{ago_secs}s ago (Cached)"
        else:
            verified_str = "[dim]Never[/dim]"

        sys_user = getattr(self.app, "current_user", None)
        sys_username = sys_user.username if sys_user else "Guest (Local)"

        return (
            f"[bold]Session Roles & Account Status:[/bold]\n"
            f" • [bold white]System Operator:[/bold white] [bold green]● {sys_username}[/bold green] [dim](Verification Copilot DB & Audit)[/dim]\n"
            f" • [bold white]ITMS Web Session:[/bold white] [{auth_color}]● {user_email}[/{auth_color}] [dim](stock.itms.ug Sync)[/dim]\n"
            f"\n"
            f"[bold]Target Endpoint:[/bold]  [cyan]{status['url']}[/cyan]\n"
            f"[bold]ITMS State:[/bold]       [{auth_color}]● {auth_tag}[/{auth_color}]\n"
            f"[bold]Cookie Expiry:[/bold]    {expiry_info} [dim](Vault: 30-day session)[/dim]\n"
            f"[bold]Last Verified:[/bold]    {verified_str}\n"
            f"[bold]Status Message:[/bold]   [dim]{status.get('status_message') or 'Ready'}[/dim]"
        )

    def _refresh_status_card(self) -> None:
        try:
            card = self.query_one("#itms-status-card", Static)
            card.update(self._render_status_card())
        except Exception:
            pass

        try:
            top_bar = self.query_one("#itms-top-bar", Static)
            top_bar.update(self._render_top_bar())
        except Exception:
            pass

        try:
            safety_card = self.query_one("#itms-safety-card", Static)
            cfg = get_config_service()
            show_card = cfg.get_setting("system.show_safety_guarantee", False)
            dry_run = cfg.get_setting("submission.dry_run_mode", True)
            safety_card.display = show_card
            if show_card:
                safety_card.update(self._render_safety_card())
                if dry_run:
                    safety_card.remove_class("live-mode")
                else:
                    safety_card.add_class("live-mode")
        except Exception:
            pass

    def _log_preview(self, msg: Any) -> None:
        try:
            log = self.app.query_one("#activity-log", RichLog)
            log.write(msg)
        except Exception:
            pass

    def _set_feedback(self, msg: str, color: str = "cyan") -> None:
        self._log_preview(f"[{color}]{msg}[/{color}]")

    # ──────────────────────────────────────────────────────────────────────────
    # Table Event Handlers (Row selection & highlighting)
    # ──────────────────────────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table_id = event.data_table.id
        row_key = str(event.row_key.value)
        if table_id == "table-itms-orders":
            order = self._active_orders_map.get(row_key)
            if order:
                self._highlighted_order = order
                try:
                    self.query_one("#inspector-itms-orders", InspectorPane).show_itms_order(order, is_archive=False)
                except Exception:
                    pass
        elif table_id == "table-itms-archive":
            order = self._archive_orders_map.get(row_key)
            if order:
                self._highlighted_order = order
                try:
                    self.query_one("#inspector-itms-archive", InspectorPane).show_itms_order(order, is_archive=True)
                except Exception:
                    pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_fetch_order_info(download_photos=False)

    def _get_target_order_identifier(self) -> str:
        """Resolves order identifier from highlighted row, active inputs, or fallback order list."""
        if self._highlighted_order:
            order_key = self._highlighted_order.get("order_key")
            order_num = self._highlighted_order.get("order_number")
            if order_key or order_num:
                return order_key or order_num

        if self.current_subview == "ORDERS":
            try:
                search_val = self.query_one("#input-itms-search-plate", Input).value.strip()
                if search_val:
                    return search_val
            except Exception:
                pass
        elif self.current_subview == "ARCHIVE":
            try:
                search_val = self.query_one("#input-itms-archive-filter", Input).value.strip()
                if search_val:
                    return search_val
            except Exception:
                pass

        orders_list = self._last_archive_orders if self.current_subview == "ARCHIVE" else self._last_active_orders
        if orders_list:
            first = orders_list[0]
            return first.get("order_key") or first.get("order_number") or first.get("registration_number", "")

        return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Button Event Dispatcher
    # ──────────────────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-itms-ping":
            self.action_ping()
        elif btn_id == "btn-itms-login":
            self.action_login()
        elif btn_id == "btn-itms-verify":
            self.action_verify()
        elif btn_id == "btn-itms-preview-dash":
            self.action_preview_dash()
        elif btn_id in ("btn-itms-fetch-orders", "btn-itms-orders-fetch"):
            self.action_fetch_orders(archive=False)
        elif btn_id in ("btn-itms-fetch-archive", "btn-itms-archive-fetch"):
            self.action_fetch_orders(archive=True)
        elif btn_id in ("btn-itms-order-info", "btn-itms-orders-info", "btn-itms-archive-info"):
            self.action_fetch_order_info(download_photos=False)
        elif btn_id in ("btn-itms-view-photos", "btn-itms-orders-view-photos", "btn-itms-archive-view-photos"):
            self.action_view_photos()
        elif btn_id in ("btn-itms-download-photos", "btn-itms-orders-download", "btn-itms-archive-download"):
            self.action_fetch_order_info(download_photos=True)
        elif btn_id in ("btn-itms-sync-orders", "btn-itms-orders-sync", "btn-itms-archive-sync"):
            self.action_sync_orders()
        elif btn_id in ("btn-itms-prev-page", "btn-itms-orders-prev", "btn-itms-archive-prev"):
            self.action_prev_page()
        elif btn_id in ("btn-itms-next-page", "btn-itms-orders-next", "btn-itms-archive-next"):
            self.action_next_page()
        elif btn_id == "btn-itms-logout":
            self.action_itms_logout()

    # ──────────────────────────────────────────────────────────────────────────
    # Side-by-Side Photographic Evidence Viewer (Keybinding [V])
    # ──────────────────────────────────────────────────────────────────────────

    @work(thread=True)
    def action_view_photos(self) -> None:
        """
        Views front and rear evidence photos side-by-side for the highlighted or specified order.
        If photos have not been downloaded yet, downloads them into the safe vault folder first.
        """
        target = self._get_target_order_identifier()
        if not target:
            self.app.call_from_thread(
                self.app.notify,
                "Select an order from the table (or enter plate/order # in search box) to view photos.",
                severity="warning",
            )
            return

        self.app.call_from_thread(self._set_feedback, f"Resolving evidence photos for '{target}'...", "yellow")
        self.app.call_from_thread(
            self._log_preview,
            f"[dim]Initiating side-by-side photo inspection for '{target}'...[/dim]",
        )

        # 1. Fetch order info & ensure photos are downloaded to the safe vault
        info = self.client.fetch_order_info(target, download_photos=True)
        if not info.get("success"):
            err = info.get("error", "Failed fetching order evidence photos.")
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(self._log_preview, f"[bold red]✗ Photo Inspection Failed:[/bold red] {err}")
            self.app.call_from_thread(self.app.notify, f"Error: {err}", severity="error")
            return

        photos = info.get("photos", [])
        order_num = info.get("order_number", target)
        plate = info.get("registration_number", "")

        front_path = ""
        rear_path = ""
        for p in photos:
            if p.get("orientation") == "FRONT" and not front_path:
                front_path = p.get("local_path", "")
            elif p.get("orientation") == "REAR" and not rear_path:
                rear_path = p.get("local_path", "")
            elif not front_path:
                front_path = p.get("local_path", "")
            elif not rear_path:
                rear_path = p.get("local_path", "")

        has_front = bool(front_path and os.path.isfile(front_path))
        has_rear = bool(rear_path and os.path.isfile(rear_path))

        if not has_front and not has_rear:
            self.app.call_from_thread(
                self._set_feedback,
                f"✗ No photo files available for order #{order_num}.",
                "bold red",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"No photos found on ITMS for #{order_num}.",
                severity="warning",
            )
            return

        window_title = f"ITMS Order #{order_num} ({plate}) - Evidence Photos"

        try:
            if has_front and has_rear:
                viewer.show_side_by_side(front_path, rear_path, window_title=window_title, block=False)
                mode_desc = "Side-by-side (Front | Rear)"
            elif has_front:
                viewer.show_single_image(front_path, window_title=f"{window_title} - Front", plate_label=plate, orient_label="FRONT")
                mode_desc = "Single photo (Front)"
            else:
                viewer.show_single_image(rear_path, window_title=f"{window_title} - Rear", plate_label=plate, orient_label="REAR")
                mode_desc = "Single photo (Rear)"

            manifest_msg = f" (Manifest: {info.get('manifest_path', 'safe vault')})" if info.get("manifest_path") else ""
            self.app.call_from_thread(self._set_feedback, f"✓ {mode_desc} viewer launched for #{order_num}", "bold green")
            self.app.call_from_thread(
                self._log_preview,
                f"[bold green]✓ Evidence Photo Viewer:[/bold green] Opened {mode_desc} for order [bold white]#{order_num}[/bold white] ({plate}).\n"
                f"  • Safe Vault Directory: [cyan]{os.path.dirname(front_path or rear_path)}[/cyan]{manifest_msg}\n"
                f"  • Front: {os.path.basename(front_path) if front_path else 'None'}\n"
                f"  • Rear: {os.path.basename(rear_path) if rear_path else 'None'}",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"{mode_desc} viewer opened for #{order_num} ({plate})!",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"Launched evidence viewer for ITMS order #{order_num} ({plate})",
                level="SUCCESS",
            )
        except Exception as exc:
            self.app.call_from_thread(self._set_feedback, f"✗ Error launching viewer: {exc}", "bold red")
            self.app.call_from_thread(self.app.notify, f"Viewer error: {exc}", severity="error")

    # ──────────────────────────────────────────────────────────────────────────
    # Threaded Async Worker Actions
    # ──────────────────────────────────────────────────────────────────────────

    @work(thread=True)
    def action_ping(self) -> None:
        """Pings ITMS WebApp to test reachability and latency."""
        url = self.query_one("#input-itms-url", Input).value.strip() or self.client.base_url
        self.app.call_from_thread(self._set_feedback, "Pinging server...", "yellow")
        self.app.call_from_thread(
            self._log_preview,
            f"[dim]Initiating reachability probe to {url}...[/dim]",
        )

        client = ITMSWebClient(base_url=url, session_store=self.client.session_store)
        res = client.test_connection()

        if res.get("success"):
            msg = f"Server Online (HTTP {res['status_code']}, {res['latency_ms']}ms, {res['server']})"
            self.app.call_from_thread(self._set_feedback, f"✓ {msg}", "bold green")
            self.app.call_from_thread(
                self._log_preview,
                f"[bold green]✓ Reachability Verified:[/bold green] {res['message']}",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"ITMS server online at {url} ({res['latency_ms']}ms)",
                level="ITMS",
            )
        else:
            err = res.get("error", "Connection failed")
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(
                self._log_preview,
                f"[bold red]✗ Reachability Failed:[/bold red] {err}",
            )
            self.app.call_from_thread(
                self.app.log_message,
                f"ITMS ping failed: {err}",
                level="ERROR",
            )

    @work(thread=True)
    def action_login(self) -> None:
        """Performs Yii2 form login and stores 30-day session cookies."""
        url = self.query_one("#input-itms-url", Input).value.strip() or self.client.base_url
        email = self.query_one("#input-itms-email", Input).value.strip()
        password = self.query_one("#input-itms-password", Input).value
        remember = self.query_one("#chk-itms-remember", Checkbox).value

        if not email or not password:
            self.app.call_from_thread(
                self._set_feedback,
                "Please enter ITMS username/email and password.",
                "bold red",
            )
            return

        self.app.call_from_thread(self._set_feedback, "Authenticating with ITMS WebApp...", "yellow")
        self.app.call_from_thread(
            self._log_preview,
            f"[dim]Executing Yii2 session authentication for {email}...[/dim]",
        )

        client = ITMSWebClient(base_url=url, session_store=self.client.session_store)
        res = client.login(email, password, remember_me=remember)

        # client.login returns a 3-tuple: (success: bool, message: str, session_data: dict)
        if isinstance(res, tuple):
            success = bool(res[0])
            msg = res[1] if len(res) > 1 else ""
            session_data = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
        elif isinstance(res, dict):
            success = bool(res.get("success", False))
            msg = res.get("message") or res.get("error", "")
            session_data = res
        else:
            success = bool(res)
            msg = str(res)
            session_data = {}

        if success:
            self.client = client
            user_uuid = session_data.get("user_uuid") or client.session_store.session.user_uuid or "Active"

            # Clear any leftover cached orders from the previous account before loading new account data
            self._last_fetched_orders = []
            self._last_active_orders = []
            self._last_archive_orders = []
            self._active_orders_map.clear()
            self._archive_orders_map.clear()
            self._highlighted_order = None

            def on_login_success():
                self._set_feedback(f"✓ Authenticated as {email}", "bold green")
                try:
                    pw_input = self.query_one("#input-itms-password", Input)
                    pw_input.value = ""
                except Exception:
                    pass
                try:
                    self.query_one("#table-itms-orders", DataTable).clear()
                    self.query_one("#table-itms-archive", DataTable).clear()
                    self.query_one("#inspector-itms-orders", InspectorPane).show_itms_order(None, is_archive=False)
                    self.query_one("#inspector-itms-archive", InspectorPane).show_itms_order(None, is_archive=True)
                except Exception:
                    pass

            self.app.call_from_thread(on_login_success)
            self.app.call_from_thread(
                self._log_preview,
                f"[bold green]✓ Login Successful:[/bold green] Authenticated as [white]{email}[/white] (UUID: {user_uuid})\n"
                f"Session cookies saved to media/vault/.itms_web_session.json (Expires in ~30 days).",
            )
            self.app.call_from_thread(self._refresh_status_card)
            self.app.call_from_thread(self.app.reload_data)
            self.app.call_from_thread(
                self.app.log_message,
                f"ITMS WebApp authenticated successfully: {email}",
                level="ITMS",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"Connected to ITMS as {email}",
                severity="information",
            )
            if self.current_subview in ("ORDERS", "ARCHIVE"):
                self.action_fetch_orders(archive=(self.current_subview == "ARCHIVE"))
        else:
            err = msg or "Authentication failed."
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(self._log_preview, f"[bold red]✗ Login Failed:[/bold red] {err}")
            self.app.call_from_thread(
                self.app.log_message,
                f"ITMS login error: {err}",
                level="ERROR",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"ITMS login failed: {err}",
                severity="error",
            )

    @work(thread=True)
    def action_itms_logout(self) -> None:
        """Logs out from the active ITMS session, deletes cached cookies, and clears views."""
        old_user = self.client.session_store.session.user_email or "Active Account"
        self.app.call_from_thread(self._set_feedback, f"Signing out {old_user}...", "yellow")

        self.client.logout()

        self._last_fetched_orders = []
        self._last_active_orders = []
        self._last_archive_orders = []
        self._active_orders_map.clear()
        self._archive_orders_map.clear()
        self._highlighted_order = None

        def on_logout_done():
            try:
                table_orders = self.query_one("#table-itms-orders", DataTable)
                table_orders.clear()
            except Exception:
                pass
            try:
                table_archive = self.query_one("#table-itms-archive", DataTable)
                table_archive.clear()
            except Exception:
                pass
            try:
                self.query_one("#inspector-itms-orders", InspectorPane).show_itms_order(None, is_archive=False)
                self.query_one("#inspector-itms-archive", InspectorPane).show_itms_order(None, is_archive=True)
            except Exception:
                pass
            try:
                pw_input = self.query_one("#input-itms-password", Input)
                pw_input.value = ""
            except Exception:
                pass

            self._set_feedback(f"✓ Successfully signed out from {old_user}", "bold yellow")
            self._refresh_status_card()
            self.app.reload_data()
            self.app.notify(f"Signed out from {old_user}", severity="information")
            self.app.log_message(f"Signed out from ITMS account: {old_user}", level="ITMS")

        self.app.call_from_thread(on_logout_done)

    @work(thread=True)
    def action_verify(self) -> None:
        """Verifies active session cookies with cooldown rate-limit protection."""
        self.app.call_from_thread(self._set_feedback, "Verifying session cookies...", "yellow")
        res = self.client.verify_session(force=False)

        if res.get("success"):
            cached_tag = " (Cached within cooldown)" if res.get("cached") else ""
            self.app.call_from_thread(self._set_feedback, f"✓ Session Active{cached_tag}", "bold green")
            self.app.call_from_thread(
                self._log_preview,
                f"[bold green]✓ Session Verified:[/bold green] Operator: [white]{res['user_email']}[/white] "
                f"({res.get('expires_in_days', 0)} days remaining){cached_tag}",
            )
            self.app.call_from_thread(self._refresh_status_card)
        else:
            err = res.get("error", "Session verification failed.")
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(self._log_preview, f"[bold red]✗ Session Invalid:[/bold red] {err}")
            self.app.call_from_thread(self._refresh_status_card)

    @work(thread=True)
    def action_preview_dash(self) -> None:
        """Safely fetches ITMS Dashboard (GET /) in read-only mode."""
        self.app.call_from_thread(self._set_feedback, "Fetching dashboard preview...", "yellow")
        res = self.client.fetch_read_only_dashboard()

        if res.get("success"):
            modules = res.get("accessible_modules", [])
            modules_txt = "\n".join(f"  • {m}" for m in modules) if modules else "  • (None detected)"
            self.app.call_from_thread(self._set_feedback, "✓ Dashboard reachable", "bold green")
            self.app.call_from_thread(
                self._log_preview,
                f"[bold cyan]📊 ITMS Dashboard Inspection (Safe Read-Only):[/bold cyan]\n"
                f"• Page Title: [bold white]{res['page_title']}[/bold white]\n"
                f"• Operator Account: [white]{res['user_email']}[/white]\n"
                f"• User UUID: [cyan]{res['user_uuid']}[/cyan]\n"
                f"• Server Timestamp: [dim]{res['server_time']}[/dim]\n"
                f"• Accessible Fitment Modules:\n{modules_txt}",
            )
            self.app.call_from_thread(self._refresh_status_card)
        else:
            err = res.get("error", "Failed to load dashboard.")
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(self._log_preview, f"[bold red]✗ Dashboard Inspection Failed:[/bold red] {err}")

    @work(thread=True)
    def action_fetch_orders(self, archive: Optional[bool] = None) -> None:
        """Fetches installation orders for the specified page and plate filter (active or archive)."""
        if getattr(self, "_is_fetching_orders", False):
            self.app.call_from_thread(self._set_feedback, "Fetch already in progress...", "yellow")
            return
        self._is_fetching_orders = True
        try:
            if archive is None:
                archive = (self.current_subview == "ARCHIVE")

            page_input_id = "#input-itms-archive-page" if archive else "#input-itms-page"
            filter_input_id = "#input-itms-archive-filter" if archive else "#input-itms-search-plate"
            table_id = "#table-itms-archive" if archive else "#table-itms-orders"

            try:
                page_input = self.query_one(page_input_id, Input)
                page_str = page_input.value.strip() or "1"
                page = max(1, int(page_str))
            except Exception:
                page = 1

            try:
                plate_filter = self.query_one(filter_input_id, Input).value.strip()
            except Exception:
                plate_filter = ""

            search_params = plate_filter if plate_filter else None
            mode_name = "Archive (Installed)" if archive else "Active Orders"
            endpoint = "/installation-orders/archive" if archive else "/installation-orders/index"

            self.app.call_from_thread(self._set_feedback, f"Fetching {mode_name} (Page {page})...", "yellow")
            filter_tag = f" (filter: {plate_filter})" if plate_filter else ""
            self.app.call_from_thread(
                self._log_preview,
                f"[dim]Requesting {endpoint}?page={page}{filter_tag}...[/dim]",
            )

            res = self.client.fetch_installation_orders(page=page, search_params=search_params, archive=archive)

            if res.get("success"):
                orders = res.get("orders", [])
                self._last_fetched_orders = orders
                if archive:
                    self._last_archive_orders = orders
                    self._archive_orders_map = {str(i): o for i, o in enumerate(orders)}
                else:
                    self._last_active_orders = orders
                    self._active_orders_map = {str(i): o for i, o in enumerate(orders)}

                def update_table():
                    table = self.query_one(table_id, DataTable)
                    table.clear()
                    if archive:
                        for i, o in enumerate(orders):
                            st_text = "[bold green]Installed[/bold green]" if o.get("order_status", "").lower() == "installed" else o.get("order_status", "")
                            table.add_row(
                                o.get("order_number", ""),
                                o.get("registration_number", ""),
                                o.get("vin", ""),
                                st_text,
                                o.get("registration_status", ""),
                                o.get("officer", ""),
                                o.get("installation_date", ""),
                                key=str(i),
                            )
                    else:
                        for i, o in enumerate(orders):
                            table.add_row(
                                o.get("order_number", ""),
                                o.get("registration_number", ""),
                                o.get("vin", ""),
                                o.get("status", ""),
                                o.get("sales_order", ""),
                                o.get("warehouse", "")[:30],
                                key=str(i),
                            )
                    if orders:
                        self._highlighted_order = orders[0]
                        try:
                            insp_id = "#inspector-itms-archive" if archive else "#inspector-itms-orders"
                            self.query_one(insp_id, InspectorPane).show_itms_order(orders[0], is_archive=archive)
                        except Exception:
                            pass
                    else:
                        try:
                            insp_id = "#inspector-itms-archive" if archive else "#inspector-itms-orders"
                            self.query_one(insp_id, InspectorPane).show_itms_order(None, is_archive=archive)
                        except Exception:
                            pass

                self.app.call_from_thread(update_table)

                # Synchronize fetched orders into local database and update dashboard counts
                sync_res = self.client.sync_orders_to_local_db(orders)
                self.app.call_from_thread(self.app.reload_data)

                sync_info = f" [dim]({sync_res.get('created', 0)} new, {sync_res.get('updated', 0)} updated)[/dim]"
                self.app.call_from_thread(self._set_feedback, f"✓ Retrieved {len(orders)} {mode_name} order(s) on Page {page}{sync_info}", "bold green")
                self.app.call_from_thread(
                    self.app.log_message,
                    f"Retrieved {len(orders)} ITMS {mode_name} orders on page {page}{filter_tag} (Synced {sync_res.get('created', 0)} new, {sync_res.get('updated', 0)} updated)",
                    level="ITMS",
                )
            else:
                err = res.get("error", "Failed to fetch orders.")
                self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
                self.app.call_from_thread(self._log_preview, f"[bold red]✗ Fetch Orders Failed:[/bold red] {err}")
        finally:
            self._is_fetching_orders = False

    @work(thread=True)
    def action_fetch_order_info(self, download_photos: bool = False) -> None:
        """Fetches full order details, hardware serials, beacons, and photos from ITMS info endpoint."""
        target = self._get_target_order_identifier()

        if not target:
            self.app.call_from_thread(
                self._set_feedback,
                "Please select an order from the table or enter plate/order # in the filter box.",
                "bold yellow",
            )
            return

        action_desc = "Fetching details & downloading photos..." if download_photos else "Fetching order details & photos..."
        self.app.call_from_thread(self._set_feedback, action_desc, "yellow")
        self.app.call_from_thread(
            self._log_preview,
            f"[dim]Querying /installation-orders/info for '{target}' (download_photos={download_photos})...[/dim]",
        )

        res = self.client.fetch_order_info(target, download_photos=download_photos)

        if res.get("success"):
            order_num = res.get("order_number", "Unknown")
            front_p = res.get("front_plate", {})
            rear_p = res.get("rear_plate", {})
            gps_t = res.get("gps_tracker", {})
            front_b = res.get("front_beacon", {})
            rear_b = res.get("rear_beacon", {})
            photos = res.get("photos", [])

            # Rich Table formatting
            table = Table(title=f"📋 ITMS Order Details: #{order_num}", show_header=True, header_style="bold cyan")
            table.add_column("Category", style="bold white", width=22)
            table.add_column("Details / Inventory", style="green")

            table.add_row("Order Number", f"[bold white]#{order_num}[/bold white]")
            table.add_row("Registration Plate", f"[bold green]{res.get('registration_number', 'N/A')}[/bold green]")
            table.add_row("Chassis / VIN", res.get("vin", "N/A"))
            table.add_row("Installed By", res.get("installed_by", "N/A"))
            table.add_row("Warehouse", res.get("warehouse", "N/A"))
            table.add_row("Front Plate", f"Type: {front_p.get('type', 'N/A')} │ Barcode/Serial: [bold yellow]{front_p.get('serial', 'N/A')}[/bold yellow]")
            table.add_row("Rear Plate", f"Type: {rear_p.get('type', 'N/A')} │ Barcode/Serial: [bold yellow]{rear_p.get('serial', 'N/A')}[/bold yellow]")
            table.add_row("GPS Tracker", f"Type: {gps_t.get('type', 'GPS')} │ ID: [cyan]{gps_t.get('device_id', 'N/A')}[/cyan]")
            table.add_row("Front Beacon", f"Type: {front_b.get('type', 'BLE')} │ ID: [cyan]{front_b.get('device_id', 'N/A')}[/cyan]")
            table.add_row("Rear Beacon", f"Type: {rear_b.get('type', 'BLE')} │ ID: [cyan]{rear_b.get('device_id', 'N/A')}[/cyan]")

            if photos:
                for idx, p in enumerate(photos, 1):
                    loc = f"\n  ↳ [bold green]Saved locally:[/bold green] {p['local_path']} ({p.get('bytes', 0)} bytes, SHA-256: {p.get('sha256', '')[:12]}...)" if p.get("local_path") else ""
                    table.add_row(f"Photo [{idx}] {p.get('label')}", f"{p.get('url')}{loc}")
            else:
                table.add_row("Photos", "[dim]No photos attached to this order[/dim]")

            self.app.call_from_thread(self._set_feedback, f"✓ Order #{order_num} details loaded ({len(photos)} photos)", "bold green")
            self.app.call_from_thread(self._log_preview, table)

            # Auto sync to local DB
            sync_res = self.client.sync_order_info_to_local_db(res, order_uuid=res.get("order_uuid", ""))
            if sync_res.get("success"):
                self.app.call_from_thread(self.app.reload_data)
                self.app.call_from_thread(
                    self.app.log_message,
                    f"Order #{order_num} details and {len(photos)} photos synced into database registry",
                    level="SUCCESS",
                )
        else:
            err = res.get("error", "Failed to fetch order details.")
            self.app.call_from_thread(self._set_feedback, f"✗ {err}", "bold red")
            self.app.call_from_thread(self._log_preview, f"[bold red]✗ Order Info Failed:[/bold red] {err}")

    @work(thread=True)
    def action_sync_orders(self) -> None:
        """Syncs the last fetched orders into the local database registry."""
        is_archive = (self.current_subview == "ARCHIVE")
        orders_to_sync = self._last_archive_orders if is_archive else self._last_active_orders

        if not orders_to_sync:
            self.app.call_from_thread(self._set_feedback, "Fetch orders first before syncing.", "yellow")
            return

        self.app.call_from_thread(self._set_feedback, "Syncing orders to local registry...", "yellow")
        sync_res = self.client.sync_orders_to_local_db(orders_to_sync)

        verified_msg = f", {sync_res.get('installed_verified', 0)} verified installed" if is_archive else ""
        msg = f"Synced {sync_res['total']} orders: {sync_res['created']} created, {sync_res['updated']} updated{verified_msg}."
        self.app.call_from_thread(self._set_feedback, f"✓ {msg}", "bold green")
        self.app.call_from_thread(self._log_preview, f"[bold green]✓ Database Registry Updated:[/bold green] {msg}")
        self.app.call_from_thread(self.app.reload_data)
        self.app.call_from_thread(
            self.app.log_message,
            f"Synced {sync_res['total']} ITMS orders to local database ({sync_res.get('installed_verified', 0)} installed verified)",
            level="SUCCESS",
        )

    def action_prev_page(self) -> None:
        is_archive = (self.current_subview == "ARCHIVE")
        page_input_id = "#input-itms-archive-page" if is_archive else "#input-itms-page"
        page_input = self.query_one(page_input_id, Input)
        try:
            curr = int(page_input.value.strip() or "1")
        except ValueError:
            curr = 1
        new_page = max(1, curr - 1)
        page_input.value = str(new_page)
        self.action_fetch_orders(archive=is_archive)

    def action_next_page(self) -> None:
        is_archive = (self.current_subview == "ARCHIVE")
        page_input_id = "#input-itms-archive-page" if is_archive else "#input-itms-page"
        page_input = self.query_one(page_input_id, Input)
        try:
            curr = int(page_input.value.strip() or "1")
        except ValueError:
            curr = 1
        page_input.value = str(curr + 1)
        self.action_fetch_orders(archive=is_archive)

    @work(thread=True)
    def action_itms_logout(self) -> None:
        """Terminates active ITMS WebApp session and clears cookies from vault (local system session preserved)."""
        self.app.call_from_thread(self._set_feedback, "Terminating ITMS session...", "yellow")
        ok, msg = self.client.logout()
        self.app.call_from_thread(self._set_feedback, "✓ ITMS session cleared.", "white")
        self.app.call_from_thread(
            self._log_preview,
            f"[bold yellow]ITMS Session Terminated:[/bold yellow] Stored cookies deleted from media/vault/.itms_web_session.json. Local system operator session remains active.",
        )
        self.app.call_from_thread(self._refresh_status_card)
        self.app.call_from_thread(self.app.reload_data)
        self.app.call_from_thread(
            self.app.notify,
            "ITMS WebApp session signed out. System operator session is preserved.",
            severity="information",
        )
        self.app.call_from_thread(
            self.app.log_message,
            "ITMS WebApp session signed out and cleared from vault. System operator session remains active.",
            level="ITMS",
        )
