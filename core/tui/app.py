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

from django.conf import settings
from django.core.management import call_command
from textual import work
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

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import file_dialog, vault_service, viewer
from core.services.submission_worker import submit_pair

STATUS_STYLE = {
    VehicleInstallationPair.VerificationStatus.PENDING_REVIEW: "yellow",
    VehicleInstallationPair.VerificationStatus.APPROVED: "bold green",
    VehicleInstallationPair.VerificationStatus.INCOMPLETE: "red",
    VehicleInstallationPair.VerificationStatus.CONFLICT: "bold red",
    VehicleInstallationPair.VerificationStatus.UNREGISTERED: "magenta",
    VehicleInstallationPair.VerificationStatus.SUBMITTED: "bold cyan",
    VehicleInstallationPair.VerificationStatus.FAILED: "bold red",
}

HISTORY_FILTERS = ["ALL", "SUBMITTED", "FAILED", "APPROVED", "AUDIT_LOGS"]


class TextualLogStream:
    """Redirects stdout/stderr writes into a Textual callback function."""

    def __init__(self, callback, tag="INFO"):
        self.callback = callback
        self.tag = tag
        self._buf = ""

    def write(self, s: str):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self.callback(line, self.tag)

    def flush(self):
        if self._buf.strip():
            self.callback(self._buf.strip(), self.tag)
            self._buf = ""


class MetricsBar(Static):
    """Displays top-level system statistics and queue counts."""

    def refresh_metrics(self):
        orders = InstallationOrder.objects.count()
        vault_images = EvidenceImage.objects.count()
        queue = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        ).count()
        approved = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).count()
        submitted = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
        ).count()
        issues = VehicleInstallationPair.objects.filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.FAILED,
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
            ]
        ).count()
        batches = IngestionBatch.objects.count()

        self.update(
            f"[b]Orders:[/b] {orders}  │  "
            f"[b]Vault Photos:[/b] {vault_images}  │  "
            f"[b]Queue:[/b] [yellow]{queue}[/yellow]  │  "
            f"[b]Approved:[/b] [green]{approved}[/green]  │  "
            f"[b]Submitted:[/b] [cyan]{submitted}[/cyan]  │  "
            f"[b]Issues:[/b] [red]{issues}[/red]  │  "
            f"[b]Batches:[/b] {batches}"
        )


class InspectorPane(Static):
    """Displays rich detail for the currently selected pair or batch."""

    def show_pair(self, pair: VehicleInstallationPair):
        if pair is None:
            self.update("[dim]No pair selected. Use ↑/↓ to browse.[/dim]")
            return

        order_line = (
            f"[green]{pair.order.order_number}[/green] (expects [b]{pair.order.registration_number}[/b])"
            if pair.order
            else "[yellow]— unmatched —[/yellow]"
        )
        serial_line = pair.order.plate_serial if pair.order and pair.order.plate_serial else "—"
        tracker_line = pair.order.tracker_id if pair.order and pair.order.tracker_id else "—"
        front = pair.front_image
        rear = pair.rear_image
        status_color = STATUS_STYLE.get(pair.verification_status, "white")

        lines = [
            f"[b cyan]═══ Pair Inspector ═══[/b cyan]",
            f"[b]Detected Plate:[/b] [bold yellow]{pair.registration_number_detected}[/bold yellow]",
            f"[b]Status:[/b] [{status_color}]{pair.verification_status}[/{status_color}]",
            f"[b]Match Quality:[/b] {pair.match_type} (score: {pair.match_score if pair.match_score is not None else 'N/A'})",
            f"[b]Matched Order:[/b] {order_line}",
            f"[b]Plate Serial:[/b] {serial_line}  │  [b]Tracker ID:[/b] {tracker_line}",
            "",
            "[b underline]Photographic Evidence[/b underline]:",
            f" [b]Front:[/b] {front.vault_file if front else '[red]MISSING[/red]'}",
            f"        ocr_conf={round(front.ocr_confidence, 3) if front and front.ocr_confidence else 'N/A'} "
            f"orient={front.orientation if front else '—'} ({round(front.orientation_confidence or 0.0, 2)})",
            f" [b]Rear:[/b]  {rear.vault_file if rear else '[red]MISSING[/red]'}",
            f"        ocr_conf={round(rear.ocr_confidence, 3) if rear and rear.ocr_confidence else 'N/A'} "
            f"orient={rear.orientation if rear else '—'} ({round(rear.orientation_confidence or 0.0, 2)})",
            "",
            "[b]Quick Actions:[/b]",
            " [b green]A[/b green]: Approve Pair     [b yellow]S[/b yellow]: Swap Front/Rear",
            " [b cyan]V[/b cyan]: View Side-by-Side [b blue]U[/b blue]: Submit to ITMS",
        ]
        self.update("\n".join(lines))

    def show_audit_history(self, pair: VehicleInstallationPair):
        if pair is None:
            self.update("[dim]No historical pair selected.[/dim]")
            return

        logs = list(pair.audit_logs.all().order_by("-timestamp")[:10])
        lines = [
            f"[b cyan]═══ Audit Trail: {pair.registration_number_detected} ═══[/b cyan]",
            f"[b]Status:[/b] {pair.verification_status}  │  [b]Order:[/b] {pair.order.order_number if pair.order else '—'}",
            f"[b]Submitted At:[/b] {pair.submitted_at.strftime('%Y-%m-%d %H:%M:%S') if pair.submitted_at else 'Not Submitted'}",
            "",
            "[b underline]Recent Audit Log Entries:[/b underline]",
        ]

        if not logs:
            lines.append("[dim]No audit log entries recorded for this pair.[/dim]")
        else:
            for log in logs:
                result_color = "green" if log.result == "SUCCESS" else "red" if log.result == "FAILURE" else "cyan"
                time_str = log.timestamp.strftime("%H:%M:%S")
                token_str = f" (token: {log.simulated_token[:10]}...)" if log.simulated_token else ""
                lines.append(f"• [{time_str}] [b]{log.action}[/b] -> [{result_color}]{log.result}[/{result_color}]{token_str}")
                if log.message:
                    lines.append(f"  [dim]{log.message}[/dim]")

        self.update("\n".join(lines))

    def show_batch_info(self, batch: IngestionBatch):
        if batch is None:
            self.update("[dim]No batch selected.[/dim]")
            return

        images = list(batch.images.all()[:15])
        lines = [
            f"[b cyan]═══ Batch Details ═══[/b cyan]",
            f"[b]Batch ID:[/b] [bold yellow]{batch.batch_id}[/bold yellow]",
            f"[b]Source Channel:[/b] {batch.source_type} ({batch.source_label or 'None'})",
            f"[b]Created At:[/b] {batch.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"[b]Files:[/b] {batch.total_files} total │ [green]{batch.ingested_count} ingested[/green] │ [yellow]{batch.duplicate_count} skipped[/yellow] │ [red]{batch.failed_count} failed[/red]",
            "",
            "[b underline]Images in this batch[/b underline]:",
        ]
        if not images:
            lines.append("[dim]No images registered under this batch.[/dim]")
        else:
            for img in images:
                pruned_badge = " [red](PRUNED)[/red]" if img.is_file_pruned else ""
                plate = img.detected_plate or "—"
                lines.append(f"• {img.original_source_path[:22]} -> {plate} ({img.status}){pruned_badge}")

        lines.extend([
            "",
            "[b]Shortcut:[/b] Press [b yellow]P[/b yellow] to run vision pipeline on this batch",
        ])
        self.update("\n".join(lines))


class ITMSOperatorApp(App):
    CSS = """
    Screen {
        background: $background;
    }
    #metrics {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #tabs-content {
        height: 1fr;
    }
    .tab-horizontal {
        height: 1fr;
    }
    .table-panel {
        width: 65%;
        height: 100%;
    }
    .inspector-panel {
        width: 35%;
        height: 100%;
        border-left: solid $primary;
        padding: 1;
        overflow-y: auto;
    }
    #history-filter-bar {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    #batches-upload-bar {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    #activity-container {
        height: 11;
        border-top: solid $primary;
        background: $surface;
    }
    #activity-header {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #activity-log {
        height: 1fr;
        background: #111111;
        color: #e2e8f0;
        padding: 0 1;
    }
    """

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
        ("v", "view_evidence", "View Side-by-Side"),
        ("f", "cycle_filter", "Cycle Filter"),
        ("c", "clean_storage", "Clean / Prune"),
        ("r", "refresh", "Refresh Data"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.history_filter_index = 0
        self.current_history_filter = HISTORY_FILTERS[0]
        self._selected_batch_id = None

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
                    "[b]Add Photos:[/b] Press [bold green]I[/bold green] for Native Dialog (Files/Folder)  │  Press [bold cyan]W[/bold cyan] for Web Upload (http://127.0.0.1:8000/upload/)  │  Press [bold yellow]P[/bold yellow] to process batch",
                    id="batches-upload-bar",
                )
                with Horizontal(classes="tab-horizontal"):
                    yield DataTable(id="table-batches", classes="table-panel")
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

        self._update_history_filter_bar()
        self.reload_data()

        self.log_message("[bold green]ITMS Verification Copilot initialized.[/bold green] Ready for operations.")
        self.log_message("[dim]Press [1-3] to switch tabs │ [P] Run Vision │ [M] Match Pairs │ [U] Submit │ [A] Approve │ [S] Swap[/dim]")

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

    def _update_history_filter_bar(self):
        filter_str = "  ".join([
            f"[bold green]▶ {f}[/bold green]" if f == self.current_history_filter else f"[dim]{f}[/dim]"
            for f in HISTORY_FILTERS
        ])
        bar = self.query_one("#history-filter-bar", Static)
        bar.update(f"[b]History Label Filter:[/b] {filter_str}   [dim](Press [b]F[/b] to cycle)[/dim]")

    def reload_data(self):
        """Reloads metrics and all table datasets from PostgreSQL."""
        self.query_one(MetricsBar).refresh_metrics()
        self._reload_queue_table()
        self._reload_history_table()
        self._reload_batches_table()

    def _reload_queue_table(self):
        table = self.query_one("#table-queue", DataTable)
        table.clear()
        qs = VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image").filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
                VehicleInstallationPair.VerificationStatus.APPROVED,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            ]
        )

        for pair in qs:
            style = STATUS_STYLE.get(pair.verification_status, "white")
            score_str = str(round(pair.match_score, 2)) if pair.match_score is not None else "—"
            front_conf = str(round(pair.front_image.ocr_confidence, 2)) if pair.front_image and pair.front_image.ocr_confidence else "—"
            rear_conf = str(round(pair.rear_image.ocr_confidence, 2)) if pair.rear_image and pair.rear_image.ocr_confidence else "—"

            table.add_row(
                str(pair.id)[:8],
                pair.registration_number_detected,
                pair.order.order_number if pair.order else "—",
                pair.match_type,
                score_str,
                f"[{style}]{pair.verification_status}[/{style}]",
                "✓" if pair.is_complete else "✗",
                front_conf,
                rear_conf,
                key=str(pair.id),
            )

        if table.row_count > 0 and table.cursor_row is not None:
            self._update_queue_inspector()

    def _reload_history_table(self):
        table = self.query_one("#table-history", DataTable)
        table.clear(columns=True)

        if self.current_history_filter == "AUDIT_LOGS":
            table.add_columns("Time", "Pair / Plate", "Action", "Result", "Message", "Token")
            logs = SubmissionAuditLog.objects.select_related("pair").order_by("-timestamp")[:100]
            for log in logs:
                plate = log.pair.registration_number_detected if log.pair else "—"
                res_color = "green" if log.result == "SUCCESS" else "red" if log.result == "FAILURE" else "yellow"
                table.add_row(
                    log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    plate,
                    log.action,
                    f"[{res_color}]{log.result}[/{res_color}]",
                    (log.message[:45] + "...") if len(log.message) > 45 else log.message,
                    (log.simulated_token[:12] + "...") if log.simulated_token else "—",
                    key=str(log.id),
                )
        else:
            table.add_columns("ID", "Plate", "Order", "Status", "Submitted At", "Updated At")
            qs = VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image")
            if self.current_history_filter == "SUBMITTED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
            elif self.current_history_filter == "FAILED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.FAILED)
            elif self.current_history_filter == "APPROVED":
                qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)

            for pair in qs.order_by("-updated_at")[:100]:
                style = STATUS_STYLE.get(pair.verification_status, "white")
                sub_at = pair.submitted_at.strftime("%Y-%m-%d %H:%M") if pair.submitted_at else "—"
                upd_at = pair.updated_at.strftime("%Y-%m-%d %H:%M")

                table.add_row(
                    str(pair.id)[:8],
                    pair.registration_number_detected,
                    pair.order.order_number if pair.order else "—",
                    f"[{style}]{pair.verification_status}[/{style}]",
                    sub_at,
                    upd_at,
                    key=str(pair.id),
                )

        if table.row_count > 0 and table.cursor_row is not None:
            self._update_history_inspector()

    def _reload_batches_table(self):
        table = self.query_one("#table-batches", DataTable)
        table.clear()
        for b in IngestionBatch.objects.all().order_by("-created_at")[:50]:
            table.add_row(
                b.batch_id,
                b.source_type,
                b.source_label or "—",
                str(b.total_files),
                f"[green]{b.ingested_count}[/green]",
                f"[yellow]{b.duplicate_count}[/yellow]",
                f"[red]{b.failed_count}[/red]" if b.failed_count > 0 else "0",
                b.created_at.strftime("%Y-%m-%d %H:%M"),
                key=str(b.batch_id),
            )

        if table.row_count > 0 and table.cursor_row is not None:
            self._update_batch_inspector()

    # ── Table Navigation & Row Selection Handlers ─────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "table-queue":
            self._update_queue_inspector(event.row_key)
        elif event.data_table.id == "table-history":
            self._update_history_inspector(event.row_key)
        elif event.data_table.id == "table-batches":
            self._update_batch_inspector(event.row_key)

    def _get_active_pair(self, table_id: str) -> VehicleInstallationPair:
        table = self.query_one(f"#{table_id}", DataTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            pair_id = row_key.value
            return VehicleInstallationPair.objects.filter(id=pair_id).select_related(
                "order", "front_image", "rear_image"
            ).first()
        except Exception:
            return None

    def _update_queue_inspector(self, row_key=None):
        inspector = self.query_one("#inspector-queue", InspectorPane)
        pair = None
        if row_key and row_key.value:
            pair = VehicleInstallationPair.objects.filter(id=row_key.value).select_related(
                "order", "front_image", "rear_image"
            ).first()
        else:
            pair = self._get_active_pair("table-queue")
        inspector.show_pair(pair)

    def _update_history_inspector(self, row_key=None):
        inspector = self.query_one("#inspector-history", InspectorPane)
        if self.current_history_filter == "AUDIT_LOGS":
            inspector.update("[dim]Direct audit logs view. Select pair filter to inspect individual trails.[/dim]")
            return

        pair = None
        if row_key and row_key.value:
            pair = VehicleInstallationPair.objects.filter(id=row_key.value).select_related(
                "order", "front_image", "rear_image"
            ).first()
        else:
            pair = self._get_active_pair("table-history")
        inspector.show_audit_history(pair)

    def _update_batch_inspector(self, row_key=None):
        inspector = self.query_one("#inspector-batches", InspectorPane)
        batch = None
        batch_id = row_key.value if row_key and row_key.value else None
        if not batch_id:
            table = self.query_one("#table-batches", DataTable)
            if table.cursor_row is not None and table.row_count > 0:
                try:
                    rk = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                    batch_id = rk.value
                except Exception:
                    pass

        if batch_id:
            self._selected_batch_id = batch_id
            batch = IngestionBatch.objects.filter(batch_id=batch_id).first()
        inspector.show_batch_info(batch)

    # ── Tab Navigation Actions ──────────────────────────────────────────

    def action_tab_queue(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-queue"

    def action_tab_history(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-history"

    def action_tab_batches(self):
        tabs = self.query_one("#tabs-content", TabbedContent)
        tabs.active = "tab-batches"

    @work(thread=True)
    def action_native_ingest(self) -> None:
        """Opens native desktop dialog to pick files or folders, and ingests them into a new batch."""
        self.call_from_thread(
            self.log_message,
            "Opening native photo picker dialog (select files or folder)...",
            level="INFO",
        )
        selected_paths = file_dialog.prompt_native_photo_selection()
        if not selected_paths:
            self.call_from_thread(self.log_message, "Photo selection cancelled by operator.", level="INFO")
            return

        total = len(selected_paths)
        self.call_from_thread(
            self.log_message,
            f"Selected {total} photo(s). Initializing ingestion batch...",
            level="INFO",
        )

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label=f"Native Dialog ({total} photos)",
        )

        ingested = 0
        skipped = 0
        failed = 0

        for p in selected_paths:
            img, status = vault_service.ingest_from_disk(p, batch=batch)
            base_name = os.path.basename(p)
            if status == "INGESTED":
                ingested += 1
                self.call_from_thread(
                    self.log_message,
                    f"Ingested {base_name} -> {img.vault_file}",
                    level="INFO",
                )
            elif status == "DUPLICATE_SKIPPED":
                skipped += 1
                self.call_from_thread(
                    self.log_message,
                    f"Duplicate skipped: {base_name} (already in vault)",
                    level="WARNING",
                )
            else:
                failed += 1
                self.call_from_thread(
                    self.log_message,
                    f"Failed to ingest {base_name} ({status})",
                    level="ERROR",
                )

        batch.refresh_from_db()
        self.call_from_thread(
            self.log_message,
            f"Batch {batch.batch_id} complete: {ingested} ingested, {skipped} duplicates skipped, {failed} failed.",
            level="SUCCESS",
        )
        self.call_from_thread(
            self.log_message,
            "Tip: Press [b yellow]P[/b yellow] to run vision recognition on newly added photos.",
            level="INFO",
        )
        self.call_from_thread(self.notify, f"Batch {batch.batch_id}: {ingested} photos ingested!")
        self.call_from_thread(self.reload_data)

    def action_open_upload_ui(self):
        import socket
        import webbrowser
        url = "http://127.0.0.1:8000/upload/"
        server_running = False
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.3):
                server_running = True
        except OSError:
            server_running = False

        if server_running:
            try:
                webbrowser.open(url)
                self.notify(f"Opened Web Upload: {url}")
                self.log_message(f"Opened Web Upload in browser: [bold underline cyan]{url}[/bold underline cyan]", level="INFO")
            except Exception as exc:
                self.notify(f"Could not open browser: {exc}", severity="warning")
        else:
            self.notify("Web server not running on port 8000. Launching Native Dialog...", severity="warning")
            self.log_message(
                "Web server is not running on 127.0.0.1:8000. To use browser upload, run in another terminal: [bold cyan]python manage.py runserver[/bold cyan]",
                level="WARNING",
            )
            self.log_message("Opening native desktop file picker dialog instead...", level="INFO")
            self.action_native_ingest()

    def action_cycle_filter(self):
        self.history_filter_index = (self.history_filter_index + 1) % len(HISTORY_FILTERS)
        self.current_history_filter = HISTORY_FILTERS[self.history_filter_index]
        self._update_history_filter_bar()
        self._reload_history_table()
        self.log_message(f"History filter switched to: [b]{self.current_history_filter}[/b]")

    def action_refresh(self):
        self.reload_data()
        self.log_message("Dashboard refreshed from database.", level="INFO")

    # ── Pair Operator Actions ───────────────────────────────────────────

    def action_approve(self):
        pair = self._get_active_pair("table-queue")
        if not pair:
            self.notify("No pair selected to approve.", severity="warning")
            return
        if not pair.is_complete or not pair.order:
            self.notify("Cannot approve: pair is incomplete or has no matched order.", severity="error")
            self.log_message(f"Cannot approve {pair.registration_number_detected}: missing order or incomplete evidence.", level="WARNING")
            return

        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])
        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Pair approved by operator in TUI.",
        )
        self.notify(f"Approved {pair.registration_number_detected} for submission.")
        self.log_message(f"Approved {pair.registration_number_detected} (Order: {pair.order.order_number})", level="SUCCESS")
        self.reload_data()

    def action_swap(self):
        pair = self._get_active_pair("table-queue")
        if not pair or not (pair.front_image and pair.rear_image):
            self.notify("Need both front and rear images to swap assignments.", severity="warning")
            return

        pair.front_image, pair.rear_image = pair.rear_image, pair.front_image
        pair.save(update_fields=["front_image", "rear_image"])
        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_SWAP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator swapped front and rear image assignments in TUI.",
        )
        self.notify(f"Swapped front/rear for {pair.registration_number_detected}.")
        self.log_message(f"Swapped front/rear assignments for {pair.registration_number_detected}", level="INFO")
        self.reload_data()

    def action_view_evidence(self):
        pair = self._get_active_pair("table-queue") or self._get_active_pair("table-history")
        if not pair or not (pair.front_image and pair.rear_image):
            self.notify("Select a complete pair with both photos to view comparison.", severity="warning")
            return

        front_abs = os.path.join(settings.MEDIA_ROOT, pair.front_image.vault_file)
        rear_abs = os.path.join(settings.MEDIA_ROOT, pair.rear_image.vault_file)
        try:
            path = viewer.show_side_by_side(front_abs, rear_abs)
            self.notify(f"Comparison opened: {path}")
            self.log_message(f"Side-by-side evidence viewer opened for {pair.registration_number_detected}", level="INFO")
        except Exception as exc:
            self.notify(f"Error launching viewer: {exc}", severity="error")
            self.log_message(f"Failed to launch viewer: {exc}", level="ERROR")

    # ── Background Worker Actions (Live Output Streaming) ───────────────

    @work(thread=True)
    def action_process_vision(self) -> None:
        """Runs the vision pipeline in a background thread and streams progress to the bottom log."""
        batch_filter = self._selected_batch_id

        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="VISION")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        if batch_filter:
            self.call_from_thread(
                self.log_message,
                f"Starting vision pipeline for batch: {batch_filter}...",
                level="VISION",
            )
            cmd_args = ["process_vision", f"--batch={batch_filter}"]
        else:
            self.call_from_thread(
                self.log_message,
                "Starting vision pipeline over pending unsubmitted images...",
                level="VISION",
            )
            cmd_args = ["process_vision", "--include-needs-review"]

        try:
            call_command(*cmd_args, stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(
                self.log_message,
                "Vision pipeline batch execution complete.",
                level="SUCCESS",
            )
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Vision pipeline execution failed: {exc}", level="ERROR")
        finally:
            self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_match_pairs(self) -> None:
        """Runs associate_pairs in background and logs results."""
        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="MATCHER")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        self.call_from_thread(self.log_message, "Running pair grouping and order matching...", level="MATCHER")
        try:
            call_command("associate_pairs", stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(self.log_message, "Pair matching completed.", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Pair matching failed: {exc}", level="ERROR")
        finally:
            self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_submit_pair(self) -> None:
        """Submits the currently selected pair (or all approved) to ITMS with live logging."""
        pair = self._get_active_pair("table-queue")
        if not pair:
            self.call_from_thread(self.notify, "No pair selected for submission.", severity="warning")
            return

        if pair.verification_status != VehicleInstallationPair.VerificationStatus.APPROVED:
            self.call_from_thread(
                self.log_message,
                f"Pair {pair.registration_number_detected} is {pair.verification_status}. Approve it with [A] first.",
                level="WARNING",
            )
            return

        self.call_from_thread(
            self.log_message,
            f"Initiating ITMS submission for {pair.registration_number_detected} (Order: {pair.order.order_number})...",
            level="ITMS",
        )

        outcome = submit_pair(pair)
        if outcome.success:
            self.call_from_thread(
                self.log_message,
                f"Successfully submitted {pair.registration_number_detected} to ITMS! Token: {outcome.token}",
                level="SUCCESS",
            )
        else:
            self.call_from_thread(
                self.log_message,
                f"Submission failed for {pair.registration_number_detected}: {outcome.error}",
                level="ERROR",
            )
        self.call_from_thread(self.reload_data)

    @work(thread=True)
    def action_clean_storage(self) -> None:
        """Runs crop cleanup and vault lifecycle pruning in background."""
        def stream_cb(msg, tag):
            self.call_from_thread(self.log_message, msg, level=tag)

        out_stream = TextualLogStream(stream_cb, tag="STORAGE")
        err_stream = TextualLogStream(stream_cb, tag="ERROR")

        self.call_from_thread(self.log_message, "Executing temporary crop cleanup...", level="INFO")
        try:
            call_command("clean_crops", stdout=out_stream, stderr=err_stream)
            self.call_from_thread(self.log_message, "Enforcing 7-day vault retention lifecycle...", level="INFO")
            call_command("prune_vault", stdout=out_stream, stderr=err_stream)
            out_stream.flush()
            err_stream.flush()
            self.call_from_thread(self.log_message, "Storage maintenance completed.", level="SUCCESS")
        except Exception as exc:
            self.call_from_thread(self.log_message, f"Storage maintenance error: {exc}", level="ERROR")
        finally:
            self.call_from_thread(self.reload_data)


def run():
    ITMSOperatorApp().run()


if __name__ == "__main__":
    run()
