from django.core.management.base import BaseCommand

from core.services.submission_worker import submit_approved_pairs


class Command(BaseCommand):
    help = (
        "Drives all APPROVED VehicleInstallationPair rows through the simulated ITMS "
        "4-step submission workflow, with full audit logging and manual-fallback "
        "on any failure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--auto-approve", action="store_true",
            help=(
                "Also auto-approve any complete, high-confidence (EXACT/FUZZY matched) "
                "PENDING_REVIEW pairs before submitting -- for unattended batch runs."
            ),
        )
        parser.add_argument(
            "--retry-failed", action="store_true",
            help="Reset complete FAILED pairs to APPROVED before running submission.",
        )
        parser.add_argument(
            "--backend", choices=["mock", "live", "live_web"], default=None,
            help="Submission backend: 'mock' (simulated sandbox), 'live' (REST API), or 'live_web' (live stock.itms.ug web wizard). Default: settings.ITMS_SUBMISSION_BACKEND.",
        )

    def handle(self, *args, **options):
        backend = options.get("backend")
        outcomes = submit_approved_pairs(
            auto_approve=options["auto_approve"],
            retry_failed=options["retry_failed"],
            backend=backend,
        )

        if not outcomes:
            self.stdout.write(self.style.WARNING("No approved pairs were ready for submission."))
            return

        success = sum(1 for o in outcomes if o.success)
        failed = len(outcomes) - success

        for outcome in outcomes:
            if outcome.success:
                self.stdout.write(self.style.SUCCESS(f"SUBMITTED  pair={outcome.pair_id}  token={outcome.token}"))
            else:
                self.stdout.write(self.style.ERROR(f"FAILED     pair={outcome.pair_id}  reason={outcome.error}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Submission run complete: {success} submitted, {failed} failed."))
