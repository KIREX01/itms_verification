"""
Operator Terminal User Interface (TUI).

Built with Textual. Provides:
  - A live metrics header (Orders / Vault Images / Ready / Approved / Incomplete)
  - A DataTable of VehicleInstallationPair rows with status-colored cells
  - An inspector pane showing full detail of the selected pair
  - Single-key actions: [V]iew evidence, [A]pprove, [S]wap front/rear,
    [R]efresh, [Q]uit

Run via: python manage.py run_tui

Note on Django + async: Textual's event loop is asyncio-based, and Django's ORM
refuses synchronous DB calls made from inside a running event loop by default
(SynchronousOnlyOperation) as a guard against accidental concurrency bugs. This
app is a single-user, single-threaded local operator tool with no concurrent
async DB access, so we deliberately opt out of that guard the way Django's own
docs recommend for exactly this kind of interactive tool (see
DJANGO_ALLOW_ASYNC_UNSAFE in the Django async-safety docs). This must be set
before any ORM call happens, so it's set at import time, here.
"""
import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

import django

django.setup() if not django.apps.apps.ready else None  # safe if already configured by manage.py

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from core.models import EvidenceImage, InstallationOrder, VehicleInstallationPair
from core.services import viewer
from core.services.submission_worker import submit_pair

STATUS_STYLE = {
    VehicleInstallationPair.VerificationStatus.PENDING_REVIEW: "yellow",
    VehicleInstallationPair.VerificationStatus.APPROVED: "green",
    VehicleInstallationPair.VerificationStatus.INCOMPLETE: "red",
    VehicleInstallationPair.VerificationStatus.CONFLICT: "bold red",
    VehicleInstallationPair.VerificationStatus.UNREGISTERED: "magenta",
    VehicleInstallationPair.VerificationStatus.SUBMITTED: "bold green",
    VehicleInstallationPair.VerificationStatus.FAILED: "bold red",
}


class MetricsBar(Static):
    def refresh_metrics(self):
        orders = InstallationOrder.objects.count()
        vault_images = EvidenceImage.objects.count()
        ready = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW, is_complete=True
        ).count()
        approved = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).count()
        incomplete = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE
        ).count()
        self.update(
            f"[b]Orders[/b]: {orders}   "
            f"[b]Vault Images[/b]: {vault_images}   "
            f"[b]Ready to Review[/b]: {ready}   "
            f"[b]Approved[/b]: {approved}   "
            f"[b]Incomplete[/b]: {incomplete}"
        )


class InspectorPane(Static):
    def show_pair(self, pair: VehicleInstallationPair):
        if pair is None:
            self.update("No pair selected.")
            return
        order_line = f"{pair.order.order_number} (expects {pair.order.registration_number})" if pair.order else "— unmatched —"
        front = pair.front_image
        rear = pair.rear_image
        lines = [
            f"[b]Detected Plate:[/b] {pair.registration_number_detected}",
            f"[b]Matched Order:[/b] {order_line}",
            f"[b]Match Type/Score:[/b] {pair.match_type} / {pair.match_score}",
            f"[b]Status:[/b] {pair.verification_status}",
            "",
            f"[b]Front image:[/b] {front.vault_file if front else '—'} "
            f"(ocr_conf={front.ocr_confidence if front else 'N/A'}, "
            f"orient_conf={front.orientation_confidence if front else 'N/A'})",
            f"[b]Rear image:[/b] {rear.vault_file if rear else '—'} "
            f"(ocr_conf={rear.ocr_confidence if rear else 'N/A'}, "
            f"orient_conf={rear.orientation_confidence if rear else 'N/A'})",
        ]
        self.update("\n".join(lines))


class ITMSOperatorApp(App):
    CSS = """
    #metrics { height: 1; background: $panel; padding: 0 1; }
    #body { height: 1fr; }
    #table { width: 65%; }
    #inspector { width: 35%; border-left: solid $primary; padding: 1; }
    """

    BINDINGS = [
        ("v", "view_evidence", "View evidence"),
        ("a", "approve", "Approve"),
        ("s", "swap", "Swap Front/Rear"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield MetricsBar(id="metrics")
        with Horizontal(id="body"):
            yield DataTable(id="table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("ID", "Plate", "Order", "Match", "Status", "Complete")
        table.cursor_type = "row"
        self.reload_data()

    def reload_data(self):
        self.query_one(MetricsBar).refresh_metrics()
        table = self.query_one("#table", DataTable)
        table.clear()
        self._pair_ids = []
        for pair in VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image").all():
            style = STATUS_STYLE.get(pair.verification_status, "white")
            table.add_row(
                str(pair.id),
                pair.registration_number_detected,
                pair.order.order_number if pair.order else "—",
                pair.match_type,
                f"[{style}]{pair.verification_status}[/{style}]",
                "✓" if pair.is_complete else "✗",
            )
            self._pair_ids.append(pair.id)

    def _selected_pair(self):
        table = self.query_one("#table", DataTable)
        if table.cursor_row is None or not self._pair_ids:
            return None
        try:
            pair_id = self._pair_ids[table.cursor_row]
        except IndexError:
            return None
        return VehicleInstallationPair.objects.filter(id=pair_id).select_related(
            "order", "front_image", "rear_image"
        ).first()

    def action_refresh(self):
        self.reload_data()
        self.notify("Dashboard refreshed from database.")

    def action_view_evidence(self):
        pair = self._selected_pair()
        if not pair or not (pair.front_image and pair.rear_image):
            self.notify("Select a complete pair (front + rear) to view.", severity="warning")
            return
        import os

        from django.conf import settings

        front_abs = os.path.join(settings.MEDIA_ROOT, pair.front_image.vault_file)
        rear_abs = os.path.join(settings.MEDIA_ROOT, pair.rear_image.vault_file)
        path = viewer.show_side_by_side(front_abs, rear_abs)
        self.notify(f"Comparison image ready: {path}")

    def action_approve(self):
        pair = self._selected_pair()
        if not pair:
            return
        if not pair.is_complete or not pair.order:
            self.notify("Cannot approve: pair is incomplete or has no matched order.", severity="error")
            return
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])
        self.notify(f"Approved {pair.registration_number_detected} for submission.")
        self.reload_data()

    def action_swap(self):
        pair = self._selected_pair()
        if not pair or not (pair.front_image and pair.rear_image):
            self.notify("Need both images present to swap.", severity="warning")
            return
        pair.front_image, pair.rear_image = pair.rear_image, pair.front_image
        pair.save(update_fields=["front_image", "rear_image"])
        self.notify("Front/Rear assignment swapped.")
        self.reload_data()


def run():
    ITMSOperatorApp().run()


if __name__ == "__main__":
    run()
