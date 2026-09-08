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
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from core.tui.widgets import MetricsBar
from core.tui.inspectors import InspectorPane
from core.tui.tables import TableLoaderMixin, HISTORY_FILTERS
from core.tui.handlers import NavigationHandlersMixin
from core.tui.actions import OperatorActionsMixin
from core.tui.auth_screens import LandingAuthScreen
from core.services import auth_service

class ITMSOperatorApp(TableLoaderMixin, NavigationHandlersMixin, OperatorActionsMixin, App):
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("1", "tab_queue", "Queue"),
        ("2", "tab_history", "History"),
        ("3", "tab_batches", "Batches"),
        ("i", "native_ingest", "Add Photos (Dialog)"),
        ("w", "open_upload_ui", "Web Upload"),
        ("p", "process_vision", "Run Vision"),
        ("m", "match_pairs", "Match Pairs"),
        ("u", "submit_pair", "Submit ITMS"),
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield MetricsBar(id="metrics")

        with TabbedContent(id="tabs-content"):
            with TabPane("📋 Review Queue (1)", id="tab-queue"):
                with Horizontal(classes="tab-horizontal"):
                    yield DataTable(id="table-queue", classes="table-panel")
                    yield InspectorPane(id="inspector-queue", classes="inspector-panel")

            with TabPane("📜 History & Audit (2)", id="tab-history"):
                yield Static(id="history-filter-bar")
                with Horizontal(classes="tab-horizontal"):
                    yield DataTable(id="table-history", classes="table-panel")
                    yield InspectorPane(id="inspector-history", classes="inspector-panel")

            with TabPane("📦 Ingestion Batches (3)", id="tab-batches"):
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

        with Vertical(id="activity-container"):
            yield Static(
                "[b]Console & Activity Log[/b] (Real-time Vision, Automation & Execution)",
                id="activity-header",
            )
            yield RichLog(id="activity-log", wrap=True, highlight=True, markup=True)

        yield Footer()

    def on_mount(self) -> None:
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

        # Session authentication check
        saved_user = auth_service.get_remembered_session()
        if saved_user:
            self.current_user = saved_user
            self.log_message(
                f"[bold green]ITMS Verification Copilot initialized.[/bold green] Operator: [bold cyan]{saved_user.get_full_name() or saved_user.username}[/bold cyan] (Session Restored)",
                level="SUCCESS",
            )
            try:
                metrics = self.query_one("#metrics", MetricsBar)
                metrics.refresh_metrics()
            except Exception:
                pass
        else:
            self.push_screen(LandingAuthScreen(), self._on_auth_completed)

        self.log_message("[dim]Press [1-3] tabs │ [P] Vision │ [M] Match │ [U] Submit │ [A] Approve │ [S] Swap │ [L] Link │ [X] Sign Out[/dim]")

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
            metrics = self.query_one("#metrics", MetricsBar)
            metrics.refresh_metrics()
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

    def reload_data(self):
        """Reloads metrics and all table datasets from PostgreSQL."""
        self.query_one(MetricsBar).refresh_metrics()
        self._reload_queue_table()
        self._reload_history_table()
        self._reload_batches_table()


def run():
    ITMSOperatorApp().run()


if __name__ == "__main__":
    run()
