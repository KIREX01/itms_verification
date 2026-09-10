from django.core.management.base import BaseCommand

from core.matcher import association, order_matcher


class Command(BaseCommand):
    help = (
        "Groups vaulted evidence images into front/rear pairs via sequence and plate matching, "
        "identifies front/rear count discrepancies and photos missing partners, and links complete "
        "pairs to an InstallationOrder."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch",
            type=str,
            default=None,
            help="Only associate photos belonging to a specific IngestionBatch (batch_id).",
        )
        parser.add_argument(
            "--skip-matching",
            action="store_true",
            help="Only run pairwise association; skip fuzzy order matching.",
        )

    def handle(self, *args, **options):
        batch_id = options.get("batch")
        if batch_id:
            self.stdout.write(f"Scoping pairwise association to batch: {batch_id}\n")

        summary = association.run_association(batch_id=batch_id)

        self.stdout.write(self.style.SUCCESS("--- Pairwise Association Summary ---"))
        self.stdout.write(f"Complete pairs formed : {summary.complete_pairs}")
        self.stdout.write(f"Incomplete / Missing  : {summary.incomplete}")
        self.stdout.write(f"Conflicts             : {summary.conflicts}")

        if summary.discrepancies:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("--- Batch Count Discrepancies ---"))
            for disc in summary.discrepancies:
                self.stdout.write(self.style.WARNING(f"  {disc}"))

        if summary.missing_photos:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(f"--- Photos Missing Partner ({len(summary.missing_photos)}) ---"))
            for mp in summary.missing_photos:
                orient = mp.get("orientation", "UNKNOWN")
                missing = mp.get("missing", "OPPOSITE")
                fname = mp.get("filename", "unknown")
                b_tag = f" [Batch: {mp.get('batch')}]" if mp.get("batch") else ""
                self.stdout.write(self.style.ERROR(f"  • {orient:5} photo '{fname}'{b_tag} -> MISSING {missing} photo!"))

        if summary.details:
            self.stdout.write("")
            self.stdout.write("--- Detailed Pairing Breakdown ---")
            for line in summary.details:
                self.stdout.write(f"  {line}")

        if options["skip_matching"]:
            return

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- Fuzzy Order Matching ---"))
        matched_count = order_matcher.match_all_pending_pairs()
        self.stdout.write(f"Pairs evaluated against registry: {matched_count}")
