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

        # Detect ITMS Web Session Role & Status
        from core.services.itms_web_client import get_web_client
        itms_status = get_web_client().get_status()
        if itms_status.get("authenticated"):
            itms_user = itms_status.get("user_email") or "Active"
            itms_badge = f"[bold green]● ITMS ({itms_user.split('@')[0]})[/bold green]"
        else:
            itms_badge = "[dim]○ ITMS (Offline)[/dim]"

        self.update(
            f"[b]System Op:[/b] {op_str}  │  "
            f"[b]Link:[/b] {itms_badge}  │  "
            f"[b]Orders:[/b] {orders}  │  "
            f"[b]Vault:[/b] {vault_images}  │  "
            f"[b]Queue:[/b] [yellow]{queue}[/yellow]  │  "
            f"[b]Approved:[/b] [green]{approved}[/green]  │  "
            f"[b]Submitted:[/b] [cyan]{submitted}[/cyan]  │  "
            f"[b]Issues:[/b] [red]{issues}[/red]  │  "
            f"[b]Batches:[/b] {batches}"
        )
