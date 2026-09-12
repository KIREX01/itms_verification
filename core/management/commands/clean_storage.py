"""
Management command to execute storage lifecycle cleanup and database optimization.

Usage:
    python manage.py clean_storage
    python manage.py clean_storage --days 7
    python manage.py clean_storage --dry-run
    python manage.py clean_storage --no-db
"""
from django.core.management.base import BaseCommand
from core.services.maintenance_service import clean_storage_lifecycle, optimize_database


class Command(BaseCommand):
    help = "Cleans up stale crops, old export files, and checkpoints/truncates SQLite WAL database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Delete temporary crops older than N days (default: 7; 0 = clean all crops).",
        )
        parser.add_argument(
            "--export-days",
            type=int,
            default=30,
            help="Delete shift exports older than N days (default: 30).",
        )
        parser.add_argument(
            "--no-db",
            action="store_true",
            help="Skip SQLite WAL checkpointing and database query optimization.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scan and calculate space to be freed without deleting files.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        export_days = options["export_days"]
        optimize_db = not options["no_db"]
        dry_run = options["dry_run"]

        self.stdout.write("Starting storage lifecycle maintenance...")
        res = clean_storage_lifecycle(
            max_crop_age_days=days,
            max_export_age_days=export_days,
            optimize_db=optimize_db,
            dry_run=dry_run,
        )

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Removed {res['deleted_count']} file(s), freed {res['mb_freed']} MB of storage."
            )
        )

        db_opt = res.get("db_optimization", {})
        if db_opt and db_opt.get("optimized"):
            self.stdout.write(self.style.SUCCESS(f"Database: {db_opt.get('message')}"))
