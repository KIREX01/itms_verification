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
            "target",
            nargs="?",
            default="all",
            help="Optional target or action parameter (e.g. 'clear', 'all', 'orders').",
        )
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
        parser.add_argument(
            "--clear-itms-photos",
            action="store_true",
            help="Also delete downloaded ITMS evidence photos in media/vault/itms_photos/.",
        )
        parser.add_argument(
            "--clear-itms-session",
            action="store_true",
            help="Also delete cached ITMS session cookies (.itms_web_session.json).",
        )

    def handle(self, *args, **options):
        target = options.get("target", "all")

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
        if options.get("include_orders") or target == "orders":
            orders_count, _ = InstallationOrder.objects.all().delete()
            self.stdout.write(f"Deleted {orders_count} InstallationOrder record(s).")
        else:
            orders_count = InstallationOrder.objects.count()
            self.stdout.write(f"Preserved {orders_count} InstallationOrder registry records (use --include-orders to delete).")

        # 6. Disk files (vault & crops)
        if not options.get("keep_files"):
            media_root = Path(settings.MEDIA_ROOT)
            vault_root = getattr(settings, "VAULT_ROOT", media_root / "vault")
            crops_root = getattr(settings, "CROPS_ROOT", media_root / "crops")
            clear_itms_photos = options.get("clear_itms_photos", False)
            clear_itms_session = options.get("clear_itms_session", False)

            vault_deleted = 0
            itms_photos_preserved = 0
            itms_session_preserved = False

            if vault_root.exists():
                for item in vault_root.iterdir():
                    # Check ITMS session files
                    if item.name.startswith(".itms"):
                        if clear_itms_session:
                            item.unlink()
                            vault_deleted += 1
                        else:
                            itms_session_preserved = True
                        continue

                    # Check ITMS evidence photos vault
                    if item.name in ("itms_photos", "itms_downloads", "itms_vault"):
                        if clear_itms_photos:
                            shutil.rmtree(item)
                            vault_deleted += 1
                        else:
                            itms_photos_preserved = sum(1 for _ in item.rglob("*") if _.is_file())
                        continue

                    if item.is_file():
                        item.unlink()
                        vault_deleted += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        vault_deleted += 1

                vault_root.mkdir(parents=True, exist_ok=True)

            itms_status = []
            if itms_session_preserved:
                itms_status.append("active session preserved")
            if itms_photos_preserved > 0:
                itms_status.append(f"{itms_photos_preserved} ITMS evidence photos preserved")
            itms_msg = f" ({', '.join(itms_status)})" if itms_status else ""

            self.stdout.write(f"Cleared vaulted media files on disk ({vault_deleted} items deleted{itms_msg}).")

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

        self.stdout.write(self.style.SUCCESS("All temporary test data successfully cleared! Ready for new ingestions."))
