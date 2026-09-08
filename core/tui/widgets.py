from textual.widgets import Static
from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    VehicleInstallationPair,
)

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
        user = getattr(self.app, "current_user", None)
        op_str = f"[bold green]● {user.username}[/bold green]" if user else "[dim]Guest[/dim]"

        self.update(
            f"[b]Operator:[/b] {op_str}  │  "
            f"[b]Orders:[/b] {orders}  │  "
            f"[b]Vault Photos:[/b] {vault_images}  │  "
            f"[b]Queue:[/b] [yellow]{queue}[/yellow]  │  "
            f"[b]Approved:[/b] [green]{approved}[/green]  │  "
            f"[b]Submitted:[/b] [cyan]{submitted}[/cyan]  │  "
            f"[b]Issues:[/b] [red]{issues}[/red]  │  "
            f"[b]Batches:[/b] {batches}"
        )
