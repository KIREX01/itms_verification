from django.core.management.base import BaseCommand

from core.matcher import association, order_matcher


class Command(BaseCommand):
    help = (
        "Groups vaulted evidence images by detected plate into front/rear pairs, "
        "then runs the fuzzy order matcher to link each pair to an InstallationOrder."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-matching", action="store_true",
            help="Only run pairwise association; skip fuzzy order matching.",
        )

    def handle(self, *args, **options):
        summary = association.run_association()

        self.stdout.write(self.style.SUCCESS("--- Association Summary ---"))
        self.stdout.write(f"Groups processed : {summary.groups_processed}")
        self.stdout.write(f"Complete pairs   : {summary.complete_pairs}")
        self.stdout.write(f"Incomplete       : {summary.incomplete}")
        self.stdout.write(f"Conflicts        : {summary.conflicts}")
        for line in summary.details:
            self.stdout.write(f"  {line}")

        if options["skip_matching"]:
            return

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- Fuzzy Order Matching ---"))
        matched_count = order_matcher.match_all_pending_pairs()
        self.stdout.write(f"Pairs run through matcher: {matched_count}")
