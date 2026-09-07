import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import IngestionBatch
from core.services import vault_service

VALID_EXTENSIONS = vault_service.VALID_EXTENSIONS


class Command(BaseCommand):
    help = (
        "Evidence Vault ingestion: scans a source directory for photos, groups them into an "
        "IngestionBatch, computes SHA-256 hashes, cryptographically skips duplicates, and copies "
        "new files into media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/<uuid>.<ext> for permanent, "
        "forensic-grade storage."
    )

    def add_arguments(self, parser):
        parser.add_argument("source_dir", type=str, help="Directory containing raw photos to ingest.")
        parser.add_argument(
            "--recursive", action="store_true", help="Recurse into subdirectories of source_dir.",
        )
        parser.add_argument(
            "--batch-label", type=str, default="",
            help="Descriptive label or note for this upload batch (e.g. 'Morning Shift Entebbe').",
        )

    def handle(self, *args, **options):
        source = Path(options["source_dir"]).expanduser().resolve()
        if not source.is_dir():
            raise CommandError(f"Source directory does not exist: {source}")

        pattern = "**/*" if options["recursive"] else "*"
        candidates = [
            p for p in source.glob(pattern)
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS
        ]

        if not candidates:
            self.stdout.write(self.style.WARNING("No valid image files found in source directory."))
            return

        label = options["batch_label"] or source.name
        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label=label,
        )

        self.stdout.write(f"Initialized upload batch: {batch.batch_id} (label: '{label}')")
        self.stdout.write(f"Scanning {len(candidates)} candidate file(s)...\n")

        for path in candidates:
            img, status = vault_service.ingest_from_disk(path, batch=batch)
            if status == "INGESTED":
                self.stdout.write(self.style.SUCCESS(f"INGESTED           {path.name} -> {img.vault_file}"))
            elif status == "DUPLICATE_SKIPPED":
                self.stdout.write(f"DUPLICATE_SKIPPED  {path.name} (already in vault as {img.id})")
            elif status == "READ_ERROR":
                self.stderr.write(self.style.ERROR(f"READ_ERROR         {path.name}: file unreadable"))
            else:
                self.stderr.write(self.style.ERROR(f"FAILED             {path.name} ({status})"))

        batch.refresh_from_db()
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Batch {batch.batch_id} complete: {batch.ingested_count} ingested, "
                f"{batch.duplicate_count} duplicates skipped, {batch.failed_count} failed."
            )
        )
        self.stdout.write(
            f"To process vision on this batch: python manage.py process_vision --batch {batch.batch_id}"
        )
