import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.models import Q
from core.models import (
    VehicleInstallationPair,
    SubmissionAuditLog,
    IngestionBatch,
    InstallationOrder,
    InstallationKit,
)
from core.services import config_service, bond_service

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Backfills operator attribution and ensures bond records match canonical facilities."

    def handle(self, *args, **options):
        self.stdout.write("--- Synchronizing Operator Details and Bond Facilities ---")

        # 1. Update superuser email if available in User model or .env
        admin_user = User.objects.filter(is_superuser=True).first()
        if admin_user and not admin_user.email:
            import os
            env_email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
            if env_email:
                admin_user.email = env_email
                admin_user.save(update_fields=["email"])
                self.stdout.write(f"Updated admin user email to {env_email}")

        # 2. Backfill IngestionBatch operator attribution
        batch_count = 0
        for b in IngestionBatch.objects.all():
            changed = False
            if not b.operator and admin_user:
                # If uploaded via web console or admin upload label, associate with admin
                if "admin" in (b.source_label or "").lower() or b.source_type == IngestionBatch.SourceType.WEB:
                    b.operator = admin_user
                    changed = True
            if b.operator and not b.account_email and getattr(b.operator, "email", ""):
                b.account_email = b.operator.email.strip()
                changed = True
            if not b.facility_code or b.facility_code == "KLA_CENTRAL":
                b.facility_code = "AGM"
                changed = True
            if changed:
                b.save(update_fields=["operator", "account_email", "facility_code"])
                batch_count += 1
        self.stdout.write(f"Updated {batch_count} IngestionBatch records.")

        # 3. Backfill VehicleInstallationPair operator and email
        pairs_updated = 0
        for p in VehicleInstallationPair.objects.all():
            changed = False
            batch = (p.front_image.batch if p.front_image and p.front_image.batch else None) or (
                p.rear_image.batch if p.rear_image and p.rear_image.batch else None
            )

            # Check if an approval log exists with an operator
            if not p.operator:
                approval_log = p.audit_logs.filter(
                    action__in=[
                        SubmissionAuditLog.Action.OPERATOR_APPROVE,
                        SubmissionAuditLog.Action.SUBMIT,
                        SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
                        SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
                    ],
                    operator__isnull=False,
                ).first()
                if approval_log and approval_log.operator:
                    p.operator = approval_log.operator
                    changed = True
                elif batch and batch.operator:
                    p.operator = batch.operator
                    changed = True

            if not p.account_email:
                if p.operator and getattr(p.operator, "email", ""):
                    p.account_email = p.operator.email.strip()
                    changed = True
                elif batch and batch.account_email:
                    p.account_email = batch.account_email.strip()
                    changed = True

            if changed:
                p.save(update_fields=["operator", "account_email"])
                pairs_updated += 1

        self.stdout.write(f"Updated {pairs_updated} VehicleInstallationPair records.")

        # 4. Backfill SubmissionAuditLog operator and operator_email
        logs_updated = 0
        for log in SubmissionAuditLog.objects.all():
            changed = False
            if not log.operator and log.pair and log.pair.operator:
                log.operator = log.pair.operator
                changed = True
            elif not log.operator and log.operator_username and log.operator_username != "System":
                matched_user = User.objects.filter(username__iexact=log.operator_username).first()
                if matched_user:
                    log.operator = matched_user
                    changed = True

            if log.operator:
                full_name = log.operator.get_full_name().strip()
                uname = log.operator.username
                if not log.operator_username or log.operator_username == "System":
                    log.operator_username = full_name or uname
                    changed = True
                if not log.operator_email and getattr(log.operator, "email", ""):
                    log.operator_email = log.operator.email.strip()
                    changed = True

            if not log.operator_email and log.pair:
                email = log.pair.account_email
                if not email and log.pair.front_image and log.pair.front_image.batch:
                    email = log.pair.front_image.batch.account_email
                if email:
                    log.operator_email = email.strip()
                    changed = True

            if changed:
                log.save(update_fields=["operator", "operator_username", "operator_email"])
                logs_updated += 1

        self.stdout.write(f"Updated {logs_updated} SubmissionAuditLog records.")

        # 5. Verify and update bond facilities in config.json
        config_service.set_setting("bond.available_bonds", config_service.DEFAULT_CONFIG["bond"]["available_bonds"])
        config_service.set_setting("bond.active_bond_name", config_service.DEFAULT_CONFIG["bond"]["active_bond_name"])

        bonds = config_service.get_available_bonds()
        self.stdout.write(f"Configured available bonds: {len(bonds)} facilities.")
        for b in bonds:
            self.stdout.write(f"  [{b.get('code')}]: {b.get('name')} (ID: {b.get('warehouse_id')})")

        self.stdout.write(self.style.SUCCESS("✓ All operator details and bond records synchronized successfully."))
