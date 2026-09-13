"""
Operator Terminal User Interface (TUI).

Built with Textual. Provides:
  - Live metrics bar (Orders / Vault / Review Queue / Approved / Submitted / Issues / Batches)
  - Navigation tabs:
      [1] Review Queue    - Pending and approved pairs with side-by-side inspector
      [2] History & Audit - Historical records filterable by status labels (All, Submitted, Failed, Audit Logs)
      [3] Upload Batches  - Ingestion batches with upload channels and file counts
  - Real-time bottom Activity Log & Console streaming vision pipeline, matching, and ITMS submissions
  - Keyboard-driven command execution:
      [P] Run Vision Pipeline (threaded, live streamed to bottom log)
      [M] Run Pair Association & Fuzzy Matching
      [U] Submit to ITMS (mock or live)
      [A] Approve pair for submission
      [S] Swap Front/Rear photo assignments
      [V] View Side-by-Side comparison in image viewer
      [F] Cycle History label filter
      [C] Clean crops & Prune Vault
      [R] Refresh data
      [Q] Quit

Run via: python manage.py run_tui
"""
import os
import sys

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

import django
from django.apps import apps

if not apps.ready:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itms_project.settings")
    django.setup()

from datetime import datetime
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from core.tui.dashboard_pane import DashboardPane
from core.tui.inspectors import InspectorPane
from core.tui.itms_pane import ITMSConnectionPane
from core.tui.settings_pane import SettingsPane
from core.tui.tables import TableLoaderMixin, HISTORY_FILTERS
from core.tui.handlers import NavigationHandlersMixin
from core.tui.actions import OperatorActionsMixin
from core.tui.auth_screens import LandingAuthScreen
from core.tui.commands import ITMSCommandProvider, ITMSCommandPalette
from core.services import auth_service

class ITMSOperatorApp(TableLoaderMixin, NavigationHandlersMixin, OperatorActionsMixin, App):
    TITLE = "ITMS CLOSING SYSTEM"
    SUB_TITLE = "Verification Copilot"
    CSS_PATH = "styles.tcss"
    COMMANDS = {ITMSCommandProvider}

    BINDINGS = [
        ("ctrl+p", "command_palette", "Commands (^P)"),
        ("1", "tab_dashboard", "Dashboard (1)"),
        ("2", "tab_itms", "ITMS (2)"),
        ("3", "tab_batches", "Batches (3)"),
        ("4", "tab_queue", "Queue (4)"),
        ("5", "tab_history", "History (5)"),
        ("6", "tab_settings", "Settings (6)"),
        ("f1", "tab_dashboard", "Dashboard"),
        ("f2", "tab_itms", "ITMS"),
        ("f3", "tab_batches", "Batches"),
        ("f4", "tab_queue", "Queue"),
        ("f5", "tab_history", "History"),
        ("f6", "tab_settings", "Settings"),
        ("ctrl+1", "tab_dashboard", "Dashboard"),
        ("ctrl+2", "tab_itms", "ITMS"),
        ("ctrl+3", "tab_batches", "Batches"),
        ("ctrl+4", "tab_queue", "Queue"),
        ("ctrl+5", "tab_history", "History"),
        ("ctrl+6", "tab_settings", "Settings"),
        ("i", "native_ingest", "Add Photos (Dialog)"),
        ("w", "open_upload_ui", "Web Upload"),
        ("p", "process_vision", "Run Vision"),
        ("j", "joint_rescan", "Joint Re-Scan"),
        ("m", "match_pairs", "Match Pairs"),
        ("t", "quick_type_plate", "Type Plate"),
        ("y", "sync_itms_orders", "Sync Orders"),
        ("u", "submit_pair", "Submit Order"),
        ("b", "batch_submit", "Batch Submit"),
        ("o", "drain_outbox", "Drain Outbox"),
        ("ctrl+r", "retry_failed", "Retry Failed (^R)"),
        ("e", "export_shift_report", "Export Report"),
        ("a", "approve", "Approve"),
        ("s", "swap", "Swap Front/Rear"),
        ("l", "link_pair", "Link / Pick Photo"),
        ("v", "view_evidence", "View Photo / Pair"),
        ("f", "cycle_filter", "Cycle Filter"),
        ("c", "clean_storage", "Clean / Prune"),
        ("r", "refresh", "Refresh Data"),
        ("x", "logout", "Sign Out"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_user = None
        self.history_filter_index = 0
        self.current_history_filter = HISTORY_FILTERS[0]
        self._selected_batch_id = None
        self._selected_image_id = None
        self._vision_running = False
        self._matcher_running = False
        self._submission_running = False
        self._clean_running = False
        self._native_ingest_running = False
        self._order_sync_running = False
        self._scanner_buffer = ""
        self._scanner_last_time = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(id="tabs-content"):
            with TabPane("📊 Dashboard (1)", id="tab-dashboard"):
                yield DashboardPane(id="dashboard-pane")

            with TabPane("🌐 ITMS Connect (2)", id="tab-itms"):
                yield ITMSConnectionPane(id="itms-connection-pane")

            with TabPane("📦 Ingestion & Batches (3)", id="tab-batches"):
                yield Static(
                    "[b]Add Photos:[/b] Press [bold green]I[/bold green] for Native Dialog  │  Press [bold cyan]W[/bold cyan] for Web Upload  │  Press [bold yellow]P[/bold yellow] to process batch  │  Press [bold magenta]V[/bold magenta] to view photo with BBox",
                    id="batches-upload-bar",
                )
                with Horizontal(classes="tab-horizontal"):
                    with Vertical(classes="table-panel"):
                        yield Static(
                            "[b]📦 Batches[/b] [dim](↑/↓ select batch  │  [P] run vision on batch)[/dim]",
                            id="batches-table-title",
                        )
                        yield DataTable(id="table-batches")
                        yield Static(
                            "[b]🖼 Batch Evidence Photos & Vision Breakdown[/b] [dim](Tab to focus table  │  ↑/↓ select  │  [V]/Enter view with BBox)[/dim]",
                            id="batch-images-table-title",
                        )
                        yield DataTable(id="table-batch-images")
                    yield InspectorPane(id="inspector-batches", classes="inspector-panel")

            with TabPane("📋 Review Queue (4)", id="tab-queue"):
                yield Static(
                    "[b]Fast-Path Actions:[/b] Press [bold green]T[/bold green] Type/Match  │  "
                    "Press [bold cyan]V[/bold cyan] View Side-by-Side  │  "
                    "Press [bold yellow]A[/bold yellow] Approve/Retry  │  "
                    "Press [bold blue]U[/bold blue] Submit Single  │  "
                    "Press [bold green]B[/bold green] Batch Submit  │  "
                    "Press [bold red]^R[/bold red] Retry Failed  │  "
                    "Press [bold magenta]Y[/bold magenta] Sync Orders",
                    id="queue-action-bar",
                )
                with Horizontal(classes="tab-horizontal"):
                    yield DataTable(id="table-queue", classes="table-panel")
                    yield InspectorPane(id="inspector-queue", classes="inspector-panel")

            with TabPane("📜 History & Audit (5)", id="tab-history"):
                yield Static(id="history-filter-bar")
                with Horizontal(classes="tab-horizontal"):
                    yield DataTable(id="table-history", classes="table-panel")
                    yield InspectorPane(id="inspector-history", classes="inspector-panel")

            with TabPane("⚙️ Settings & Safety (6)", id="tab-settings"):
                yield SettingsPane(id="settings-pane")

        with Vertical(id="activity-container"):
            yield Static(
                "[b]Console & Activity Log[/b] (Real-time Vision, Automation & Execution)",
                id="activity-header",
            )
            yield RichLog(id="activity-log", wrap=True, highlight=True, markup=True)

        yield Footer()

    def on_mount(self) -> None:
        # Pre-flight: ensure database schema and operator accounts are ready
        from core.services import config_service
        config_service.ensure_migrations_applied()
        config_service.ensure_operator_accounts_synced()

        # Setup Review Queue table
        queue_table = self.query_one("#table-queue", DataTable)
        queue_table.add_columns("ID", "Plate", "Order", "Match", "Score", "Status", "Comp", "Front", "Rear")
        queue_table.cursor_type = "row"

        # Setup History table
        history_table = self.query_one("#table-history", DataTable)
        history_table.cursor_type = "row"

        # Setup Batches table
        batches_table = self.query_one("#table-batches", DataTable)
        batches_table.add_columns("Batch ID", "Source", "Label", "Total", "Ingested", "Skipped", "Failed", "Created")
        batches_table.cursor_type = "row"

        # Setup Batch Images table
        batch_images_table = self.query_one("#table-batch-images", DataTable)
        batch_images_table.add_columns("#", "ID", "Source File", "Status", "Plate", "OCR Conf", "Det Conf", "Orient", "O-Conf", "BBox")
        batch_images_table.cursor_type = "row"

        self._update_history_filter_bar()
        self.reload_data()

        # Database engine & fallback check
        from core.services import config_service
        db_info = config_service.get_active_database_info()
        self.log_message(f"Database Engine: {db_info['display']}", level="INFO")
        fb_warn = os.environ.get("ITMS_DB_FALLBACK_WARNING")
        if fb_warn:
            self.log_message(f"[bold yellow]⚠️ Fallback Active:[/bold yellow] {fb_warn}", level="WARNING")

        # Session authentication check
        saved_user = auth_service.get_remembered_session()
        if saved_user:
            self.current_user = saved_user
            self.log_message(
                f"[bold green]ITMS Closing System initialized.[/bold green] Operator: [bold cyan]{saved_user.get_full_name() or saved_user.username}[/bold cyan] (Session Restored)",
                level="SUCCESS",
            )
            try:
                self.query_one("#dashboard-pane", DashboardPane).refresh_dashboard()
            except Exception:
                pass
        else:
            self.push_screen(LandingAuthScreen(), self._on_auth_completed)

        self.log_message("[dim]Press [1-6] workflow tabs │ [I] Photos │ [P] Vision │ [M] Match │ [B] Batch Submit │ [O] Drain Outbox │ [X] Sign Out[/dim]")

        # Periodically refresh dashboard and outbox monitor every 15s
        self.set_interval(15.0, self._auto_refresh_dashboard_and_outbox)

    def _auto_refresh_dashboard_and_outbox(self) -> None:
        """Periodically updates dashboard telemetry and checks offline outbox queue."""
        try:
            self.query_one("#dashboard-pane", DashboardPane).refresh_dashboard()
        except Exception:
            pass
        self._check_offline_outbox()

    def _check_offline_outbox(self) -> None:
        """Heartbeat daemon: pings ITMS and auto-drains queued outbox orders when online."""
        try:
            from core.services import config_service
            from core.models import VehicleInstallationPair
            if not config_service.get_setting("outbox.enabled", True):
                return
            if getattr(self, "_submission_running", False):
                return
            has_outbox = VehicleInstallationPair.objects.filter(
                verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX
            ).exists()
            if not has_outbox:
                return

            from core.services.itms_web_client import get_web_client
            client = get_web_client()
            probe = client.test_connection()
            if probe.get("success"):
                self.log_message(
                    "Heartbeat: ITMS connectivity online. Auto-draining Offline Outbox queue...",
                    level="ITMS",
                )
                self.action_drain_outbox()
        except Exception:
            pass

    def _on_auth_completed(self, user):
        if not user:
            self.exit()
            return
        self.current_user = user
        self.log_message(
            f"Welcome, [bold green]{user.get_full_name() or user.username}[/bold green]! Terminal session active.",
            level="SUCCESS",
        )
        try:
            self.query_one("#dashboard-pane", DashboardPane).refresh_dashboard()
        except Exception:
            pass
        self.reload_data()

    def log_message(self, message: str, level: str = "INFO"):
        """Writes a formatted, timestamped message to the bottom activity log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        colors = {
            "INFO": "cyan",
            "VISION": "yellow",
            "MATCHER": "magenta",
            "ITMS": "bold blue",
            "SUCCESS": "bold green",
            "WARNING": "bold yellow",
            "ERROR": "bold red",
        }
        color = colors.get(level.upper(), "white")
        log_widget = self.query_one("#activity-log", RichLog)
        log_widget.write(f"[dim]{timestamp}[/dim] [{color}][{level.upper()}][/{color}] {message}")

    def action_command_palette(self) -> None:
        """Show the organized ITMS command palette with dedicated close mechanisms."""
        if not ITMSCommandPalette.is_open(self):
            self.push_screen(ITMSCommandPalette(id="--command-palette"))

    def on_key(self, event: events.Key) -> None:
        """Global key event interceptor for hardware barcode scanners and tab shortcuts."""
        # Unfocus input on Escape so digits 1-6 navigate tabs immediately without edit mode
        if event.key == "escape" and isinstance(self.focused, Input):
            self.set_focus(None)
            event.prevent_default()
            event.stop()
            return

        # Dedicated function keys & Ctrl+number shortcuts that work unconditionally (even inside inputs)
        if event.key in ("f1", "ctrl+1"):
            self.action_tab_dashboard()
            event.prevent_default()
            event.stop()
            return
        elif event.key in ("f2", "ctrl+2"):
            self.action_tab_itms()
            event.prevent_default()
            event.stop()
            return
        elif event.key in ("f3", "ctrl+3"):
            self.action_tab_batches()
            event.prevent_default()
            event.stop()
            return
        elif event.key in ("f4", "ctrl+4"):
            self.action_tab_queue()
            event.prevent_default()
            event.stop()
            return
        elif event.key in ("f5", "ctrl+5"):
            self.action_tab_history()
            event.prevent_default()
            event.stop()
            return
        elif event.key in ("f6", "ctrl+6"):
            self.action_tab_settings()
            event.prevent_default()
            event.stop()
            return

        if isinstance(self.focused, Input):
            return

        import time
        now = time.time()
        interval = now - self._scanner_last_time
        self._scanner_last_time = now

        # USB / Bluetooth 2D barcode scanners emit characters in rapid burst (<45ms) ending with Enter
        if event.key == "enter" and len(self._scanner_buffer) >= 3 and interval < 0.15:
            scanned_val = self._scanner_buffer.strip()
            self._scanner_buffer = ""
            event.prevent_default()
            event.stop()
            self._handle_barcode_scan(scanned_val)
            return

        if interval < 0.045 and event.character and event.character.isprintable():
            self._scanner_buffer += event.character
            event.prevent_default()
            event.stop()
            return
        elif interval > 0.08:
            if event.character and event.character.isprintable():
                self._scanner_buffer = event.character
            else:
                self._scanner_buffer = ""

    def _handle_barcode_scan(self, raw_code: str) -> None:
        """Processes scanned barcode/QR code from hardware scanner, auto-selecting matching pair or order."""
        from core.vision.normalizer import canonicalize
        from core.models import VehicleInstallationPair, InstallationOrder

        cleaned = canonicalize(raw_code)
        raw_upper = raw_code.strip().upper()

        # 1. Check if a pair exists in the database
        queue_table = self.query_one("#table-queue", DataTable)

        target_pair = (
            VehicleInstallationPair.objects.filter(
                registration_number_detected__iexact=cleaned
            ).select_related("order").first()
            or VehicleInstallationPair.objects.filter(
                order__order_number__iexact=raw_upper
            ).select_related("order").first()
            or VehicleInstallationPair.objects.filter(
                order__vin__iexact=raw_upper
            ).select_related("order").first()
            or VehicleInstallationPair.objects.filter(
                order__tracker_id__iexact=raw_upper
            ).select_related("order").first()
            or VehicleInstallationPair.objects.filter(
                order__gps_tracker_id__iexact=raw_upper
            ).select_related("order").first()
            or VehicleInstallationPair.objects.filter(
                order__plate_serial__iexact=raw_upper
            ).select_related("order").first()
        )

        if target_pair:
            self.action_tab_queue()
            try:
                row_idx = queue_table.get_row_index(str(target_pair.id))
                queue_table.move_cursor(row=row_idx, column=0)
                self._update_queue_inspector(str(target_pair.id))
                self.notify(
                    f"📷 [SCANNER] Matched: {target_pair.registration_number_detected} (Order #{target_pair.order.order_number if target_pair.order else '—'})",
                    severity="information",
                )
                self.log_message(
                    f"[bold green]✓ Hardware Scanner Match:[/bold green] '{raw_code}' → Selected Pair #{target_pair.id} ({target_pair.registration_number_detected})",
                    level="SUCCESS",
                )
            except Exception:
                self.notify(
                    f"📷 [SCANNER] Matched Pair #{target_pair.id} ({target_pair.registration_number_detected})",
                    severity="information",
                )
            return

        # 2. If no pair matched, check if an InstallationOrder exists
        order = (
            InstallationOrder.objects.filter(registration_number__iexact=cleaned).first()
            or InstallationOrder.objects.filter(order_number__iexact=raw_upper).first()
            or InstallationOrder.objects.filter(vin__iexact=raw_upper).first()
            or InstallationOrder.objects.filter(tracker_id__iexact=raw_upper).first()
            or InstallationOrder.objects.filter(gps_tracker_id__iexact=raw_upper).first()
        )

        if order:
            self.notify(
                f"📷 [SCANNER] Found Order #{order.order_number} ({order.registration_number})",
                severity="information",
            )
            self.log_message(
                f"[bold cyan]✓ Hardware Scanner Order Match:[/bold cyan] '{raw_code}' corresponds to Order #{order.order_number} ({order.registration_number}, VIN: {order.vin}).",
                level="INFO",
            )
            return

        # 3. No match found
        self.notify(f"Scanner: No match for '{raw_code}'", severity="warning")
        self.log_message(f"[yellow]Scanner Input:[/yellow] Read '{raw_code}', no matching pair or order in system.", level="WARNING")

    def reload_data(self):
        """Reloads dashboard telemetry and all table datasets from active database."""
        try:
            from core.tui.dashboard_pane import DashboardPane
            self.query_one("#dashboard-pane", DashboardPane).refresh_dashboard()
        except Exception:
            pass
        self._reload_queue_table()
        self._reload_history_table()
        self._reload_batches_table()


def run():
    from core.services.config_service import ensure_migrations_applied, ensure_operator_accounts_synced
    ensure_migrations_applied()
    ensure_operator_accounts_synced()
    ITMSOperatorApp().run()


if __name__ == "__main__":
    run()
