"""
Management command to clear evidence images, batches, pairs, logs, stock, kits, and media.

Usage:
    python manage.py clear_data
    python manage.py clear_data --include-orders
    python manage.py clear_data --factory-reset
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    FacilitySyncMetadata,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    OperatorITMSSession,
    OperatorProfile,
    SafeAuditScan,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
    VehicleInstallationPair,
)


class Command(BaseCommand):
    help = "Clear all evidence images, batches, pairs, audit logs, stock, kits, and media files on disk."

    def add_arguments(self, parser):
        parser.add_argument(
            "target",
            nargs="?",
            default="all",
            help="Optional target or action parameter (e.g. 'clear', 'all', 'orders', 'factory-reset').",
        )
        parser.add_argument(
            "--factory-reset",
            action="store_true",
            help="Complete factory reset: wipes all orders, kits, stock ledger, dispatches, deliveries, media, and test users, keeping only the primary superuser admin.",
        )
        parser.add_argument(
            "--include-orders",
            action="store_true",
            help="Also delete all InstallationOrder rows.",
        )
        parser.add_argument(
            "--clear-kits",
            action="store_true",
            help="Also delete all InstallationKit inventory rows.",
        )
        parser.add_argument(
            "--clear-stock",
            action="store_true",
            help="Also delete all stock deliveries, dispatches, returns, safe scans, and daily ledgers.",
        )
        parser.add_argument(
            "--clear-exports",
            action="store_true",
            help="Also delete generated shift export files in exports/ directory.",
        )
        parser.add_argument(
            "--keep-admin-only",
            action="store_true",
            help="Delete non-admin user accounts, keeping only the primary superuser admin.",
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
        factory_reset = options.get("factory_reset", False) or target in ("factory-reset", "factory_reset", "reset")
        include_orders = options.get("include_orders", False) or factory_reset or target == "orders"
        clear_kits = options.get("clear_kits", False) or factory_reset
        clear_stock = options.get("clear_stock", False) or factory_reset
        clear_exports = options.get("clear_exports", False) or factory_reset
        clear_itms_photos = options.get("clear_itms_photos", False) or factory_reset
        clear_itms_session = options.get("clear_itms_session", False) or factory_reset
        keep_admin_only = options.get("keep_admin_only", False) or factory_reset

        self.stdout.write(self.style.WARNING("=== CLOSE COPILOT DATA CLEANUP & RESET ==="))

        # 1. Audit logs
        logs_count, _ = SubmissionAuditLog.objects.all().delete()
        self.stdout.write(f"✓ Deleted {logs_count} SubmissionAuditLog record(s).")

        # 2. Pairs
        pairs_count, _ = VehicleInstallationPair.objects.all().delete()
        self.stdout.write(f"✓ Deleted {pairs_count} VehicleInstallationPair record(s).")

        # 3. Evidence Images
        images_count, _ = EvidenceImage.objects.all().delete()
        self.stdout.write(f"✓ Deleted {images_count} EvidenceImage record(s).")

        # 4. Ingestion Batches
        batches_count, _ = IngestionBatch.objects.all().delete()
        self.stdout.write(f"✓ Deleted {batches_count} IngestionBatch record(s).")

        # 5. Orders
        if include_orders:
            orders_count, _ = InstallationOrder.objects.all().delete()
            self.stdout.write(f"✓ Deleted {orders_count} InstallationOrder record(s).")
        else:
            orders_count = InstallationOrder.objects.count()
            self.stdout.write(f"• Preserved {orders_count} InstallationOrder record(s) (use --include-orders to delete).")

        # 6. Installation Kits
        if clear_kits:
            kits_count, _ = InstallationKit.objects.all().delete()
            self.stdout.write(f"✓ Deleted {kits_count} InstallationKit record(s).")

        # 7. Stock & Inventory Records
        if clear_stock:
            item_del, _ = StockDeliveryItem.objects.all().delete()
            deliv_del, _ = StockDelivery.objects.all().delete()
            trans_del, _ = StockBondTransfer.objects.all().delete()
            disp_del, _ = StockDispatchScan.objects.all().delete()
            ret_del, _ = StockReturnScan.objects.all().delete()
            safe_del, _ = SafeAuditScan.objects.all().delete()
            ledg_del, _ = DailyStockLedger.objects.all().delete()
            meta_del, _ = FacilitySyncMetadata.objects.all().delete()
            self.stdout.write(
                f"✓ Cleared stock ledger ({deliv_del} deliveries, {disp_del} dispatches, {ret_del} returns, {ledg_del} daily ledgers)."
            )

        # 8. User Accounts & Sessions
        if keep_admin_only:
            User = get_user_model()
            non_admins = User.objects.filter(is_superuser=False)
            non_admin_count = non_admins.count()
            non_admins.delete()
            OperatorProfile.objects.exclude(user__is_superuser=True).delete()
            if clear_itms_session:
                OperatorITMSSession.objects.all().delete()
            else:
                OperatorITMSSession.objects.exclude(user__is_superuser=True).delete()
            Session.objects.all().delete()
            admin_users = list(User.objects.filter(is_superuser=True).values_list("username", flat=True))
            self.stdout.write(
                f"✓ Deleted {non_admin_count} non-admin user account(s); preserved superuser admin: {', '.join(admin_users) or 'admin'}."
            )

        # 9. Disk files (vault & crops)
        if not options.get("keep_files"):
            media_root = Path(settings.MEDIA_ROOT)
            vault_root = getattr(settings, "VAULT_ROOT", media_root / "vault")
            crops_root = getattr(settings, "CROPS_ROOT", media_root / "crops")

            vault_deleted = 0
            itms_photos_preserved = 0
            itms_session_preserved = False

            if clear_itms_session:
                try:
                    from core.services.secure_storage import get_secure_auth_path
                    web_sess = get_secure_auth_path("itms_web_session.json")
                    if web_sess.is_file():
                        web_sess.unlink()
                    tokens_file = get_secure_auth_path("itms_tokens.json")
                    if tokens_file.is_file():
                        tokens_file.unlink()
                except Exception:
                    pass

            if vault_root.exists():
                for item in vault_root.iterdir():
                    if item.name.startswith(".itms"):
                        if clear_itms_session:
                            item.unlink()
                            vault_deleted += 1
                        else:
                            itms_session_preserved = True
                        continue

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

            self.stdout.write(f"✓ Cleared vaulted media files on disk ({vault_deleted} items deleted{itms_msg}).")

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
            self.stdout.write(f"✓ Cleared crop files on disk ({crops_deleted} items deleted).")

        # 10. Clear shift exports
        if clear_exports:
            exports_dir = Path("exports")
            exports_deleted = 0
            if exports_dir.exists():
                for item in exports_dir.iterdir():
                    if item.is_file() and not item.name.startswith(".git"):
                        item.unlink()
                        exports_deleted += 1
            self.stdout.write(f"✓ Cleared shift export files on disk ({exports_deleted} files deleted).")

        self.stdout.write(self.style.SUCCESS("✅ All requested data successfully cleared! Application reset to pristine factory state."))
