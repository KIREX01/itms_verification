from django.db.models import Q
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
        from core.services.itms_web_client import get_current_itms_account, get_web_client
        active_acc = get_current_itms_account()

        orders_qs = InstallationOrder.objects.all()
        pairs_qs = VehicleInstallationPair.objects.all()
        if active_acc:
            orders_qs = orders_qs.filter(
                Q(account_email__iexact=active_acc) | Q(account_email="") | Q(account_email__isnull=True)
            )
            pairs_qs = pairs_qs.filter(
                Q(order__account_email__iexact=active_acc) |
                Q(account_email__iexact=active_acc) |
                (
                    (Q(order__isnull=True) | Q(order__account_email="") | Q(order__account_email__isnull=True)) &
                    (Q(account_email="") | Q(account_email__isnull=True))
                )
            )

        total_orders = orders_qs.count()
        active_orders = orders_qs.filter(
            is_active_on_itms=True, is_archived=False
        ).count()
        completed_orders = orders_qs.filter(
            Q(is_archived=True) |
            Q(status=InstallationOrder.Status.INSTALLED) |
            Q(order_status__iexact="installed")
        ).count()

        if total_orders > 0:
            orders_tag = f"{total_orders} [dim]([yellow]{active_orders} Act[/yellow] │ [green]{completed_orders} Done[/green])[/dim]"
        else:
            orders_tag = "0 [dim](0 Act │ 0 Done)[/dim]"

        from django.utils import timezone
        today = timezone.localdate()

        today_pairs = pairs_qs.filter(
            Q(created_at__date=today) |
            Q(front_image__batch__created_at__date=today) |
            Q(rear_image__batch__created_at__date=today) |
            Q(front_image__ingested_at__date=today) |
            Q(rear_image__ingested_at__date=today)
        )
        today_queue = today_pairs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        ).count()
        today_approved = today_pairs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).count()
        today_submitted = today_pairs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
        ).count()
        today_issues = today_pairs.filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.FAILED,
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
            ]
        ).count()
        today_batches = IngestionBatch.objects.filter(created_at__date=today).count()

        vault_images = EvidenceImage.objects.count()
        queue = pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        ).count()
        approved = pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).count()
        submitted = pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
        ).count()
        issues = pairs_qs.filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.FAILED,
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
            ]
        ).count()
        batches = IngestionBatch.objects.count()
        try:
            user = getattr(self.app, "current_user", None)
        except Exception:
            user = None
        op_str = f"[bold green]● {user.username}[/bold green]" if user else "[dim]Guest[/dim]"

        # Detect ITMS Web Session Role & Status
        from core.services.itms_web_client import get_web_client
        itms_status = get_web_client().get_status()
        if itms_status.get("authenticated"):
            itms_user = itms_status.get("user_email") or "Active"
            itms_badge = f"[bold green]● ITMS ({itms_user.split('@')[0]})[/bold green]"
        else:
            itms_badge = "[dim]○ ITMS (Offline)[/dim]"

        from core.services import config_service
        db_info = config_service.get_active_database_info()
        db_badge = db_info.get("badge", "[dim]DB[/dim]")

        self.update(
            f"[b]Op:[/b] {op_str} │ "
            f"[b]DB:[/b] {db_badge} │ "
            f"[b]ITMS:[/b] {itms_badge} │ "
            f"[b]Orders:[/b] {orders_tag} │ "
            f"[bold yellow]Shift (Today):[/bold yellow] "
            f"Queue:[yellow]{today_queue}[/yellow] "
            f"Apprv:[green]{today_approved}[/green] "
            f"Sub:[cyan]{today_submitted}[/cyan] "
            f"Issues:[red]{today_issues}[/red] "
            f"Batches:[bold white]{today_batches}[/bold white]  │  "
            f"[dim]All-Time: {pairs_qs.count()} pairs ({submitted} sub) │ {batches} batches[/dim]"
        )
