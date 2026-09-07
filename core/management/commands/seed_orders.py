import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import InstallationOrder

DEFAULT_FIXTURE = [
    {"order_number": "ORD-0001", "registration_number": "UMA123AA", "plate_serial": "PS-0001", "tracker_id": "TRK-0001"},
    {"order_number": "ORD-0002", "registration_number": "UBB456C", "plate_serial": "PS-0002", "tracker_id": "TRK-0002"},
    {"order_number": "ORD-0003", "registration_number": "UMA987ZZ", "plate_serial": "PS-0003", "tracker_id": "TRK-0003"},
    {"order_number": "ORD-0004", "registration_number": "UAB111K", "plate_serial": "PS-0004", "tracker_id": "TRK-0004"},
    {"order_number": "ORD-0005", "registration_number": "UCD222LM", "plate_serial": "PS-0005", "tracker_id": "TRK-0005"},
]


class Command(BaseCommand):
    help = "Seed InstallationOrder rows from a CSV file, or a small built-in demo dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv", dest="csv_path", default=None,
            help="Path to a CSV with columns: order_number,registration_number,plate_serial,tracker_id",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete all existing InstallationOrder rows before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = InstallationOrder.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing order rows."))

        rows = self._load_rows(options["csv_path"])

        created, updated = 0, 0
        for row in rows:
            obj, was_created = InstallationOrder.objects.update_or_create(
                order_number=row["order_number"],
                defaults={
                    "registration_number": row["registration_number"].upper().replace(" ", ""),
                    "plate_serial": row.get("plate_serial", ""),
                    "tracker_id": row.get("tracker_id", ""),
                },
            )
            created += int(was_created)
            updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS(f"Seed complete: {created} created, {updated} updated."))

    def _load_rows(self, csv_path):
        if not csv_path:
            self.stdout.write("No --csv provided; using built-in demo dataset.")
            return DEFAULT_FIXTURE

        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required = {"order_number", "registration_number"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise CommandError(f"CSV must contain at least columns: {required}")
            return list(reader)
