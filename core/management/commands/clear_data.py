"""
Management command to clear all evidence images, batches, pairs, logs, and vaulted media.

Usage:
    python manage.py clear_data
    python manage.py clear_data --include-orders
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)


class Command(BaseCommand):
    help = "Clear all evidence images, batches, pairs, audit logs, and vaulted files on disk."

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-orders",
            action="store_true",
            help="Also delete all InstallationOrder rows.",
        )
        parser.add_argument(
            "--keep-files",
            action="store_true",
            help="Only delete database records, keep files on disk in media/vault.",
        )

    def handle(self, *args, **options):
        # 1. Audit logs
        logs_count, _ = SubmissionAuditLog.objects.all().delete()
        self.stdout.write(f"Deleted {logs_count} SubmissionAuditLog record(s).")

        # 2. Pairs
        pairs_count, _ = VehicleInstallationPair.objects.all().delete()
        self.stdout.write(f"Deleted {pairs_count} VehicleInstallationPair record(s).")

        # 3. Evidence Images
        images_count, _ = EvidenceImage.objects.all().delete()
        self.stdout.write(f"Deleted {images_count} EvidenceImage record(s).")

        # 4. Ingestion Batches
        batches_count, _ = IngestionBatch.objects.all().delete()
        self.stdout.write(f"Deleted {batches_count} IngestionBatch record(s).")

        # 5. Optional: Installation Orders
        if options.get("include_orders"):
            orders_count, _ = InstallationOrder.objects.all().delete()
            self.stdout.write(f"Deleted {orders_count} InstallationOrder record(s).")

        # 6. Disk files (vault & crops)
        if not options.get("keep_files"):
            media_root = Path(settings.MEDIA_ROOT)
            vault_root = getattr(settings, "VAULT_ROOT", media_root / "vault")
            crops_root = getattr(settings, "CROPS_ROOT", media_root / "crops")

            vault_deleted = 0
            if vault_root.exists():
                for item in vault_root.iterdir():
                    if item.is_file():
                        item.unlink()
                        vault_deleted += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        vault_deleted += 1
                vault_root.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f"Cleared vaulted media files on disk ({vault_deleted} items deleted).")

            crops_deleted = 0
            if crops_root.exists():
                for item in crops_root.iterdir():
                    if item.is_file():
                        item.unlink()
                        crops_deleted += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        crops_deleted += 1
                crops_root.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f"Cleared crop files on disk ({crops_deleted} items deleted).")

        self.stdout.write(self.style.SUCCESS("All test images and data successfully cleared! Ready for new ingestions."))
