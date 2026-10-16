from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import (
    VehicleInstallationPair,
    SubmissionAuditLog,
    IngestionBatch,
    InstallationOrder,
    InstallationKit,
    StockBondTransfer,
)


class Command(BaseCommand):
    help = "Diagnostic command to inspect database bond records and operator details."

    def handle(self, *args, **options):
        self.stdout.write("=== USERS ===")
        for u in User.objects.all():
            self.stdout.write(f"User: id={u.id}, username='{u.username}', email='{u.email}', full_name='{u.get_full_name()}', is_superuser={u.is_superuser}")

        self.stdout.write("\n=== LATEST 10 AUDIT LOGS ===")
        for log in SubmissionAuditLog.objects.order_by("-id")[:10]:
            self.stdout.write(f"Log {log.id}: timestamp={log.timestamp}, action={log.action}, result={log.result}, operator_id={log.operator_id}, username='{log.operator_username}'")

        self.stdout.write("\n=== LATEST 5 PAIRS ===")
        for p in VehicleInstallationPair.objects.order_by("-id")[:5]:
            self.stdout.write(f"Pair {p.id}: plate='{p.registration_number_detected}', status={p.verification_status}, operator_id={p.operator_id}, email='{p.account_email}'")

        self.stdout.write("\n=== LATEST 5 BATCHES ===")
        for b in IngestionBatch.objects.order_by("-id")[:5]:
            self.stdout.write(f"Batch {b.batch_id}: operator_id={b.operator_id}, email='{b.account_email}', facility='{b.facility_code}', label='{b.source_label}'")

        self.stdout.write("\n=== DISTINCT WAREHOUSES IN ORDERS ===")
        orders_wh = set(InstallationOrder.objects.exclude(warehouse_name="").values_list("warehouse_name", flat=True).distinct())
        self.stdout.write(f"Count: {len(orders_wh)}")
        for w in sorted(orders_wh):
            self.stdout.write(f"  - '{w}'")

        self.stdout.write("\n=== DISTINCT WAREHOUSES IN KITS ===")
        kits_wh = set(InstallationKit.objects.exclude(warehouse="").values_list("warehouse", flat=True).distinct())
        self.stdout.write(f"Count: {len(kits_wh)}")
        for k in sorted(kits_wh):
            self.stdout.write(f"  - '{k}'")

        self.stdout.write("\n=== STOCK BOND TRANSFERS ===")
        self.stdout.write(f"Count: {StockBondTransfer.objects.count()}")
        for t in StockBondTransfer.objects.order_by("-id")[:5]:
            self.stdout.write(f"Transfer {t.id}: type={t.transfer_type}, bond={t.bond_code}, other='{t.other_bond_name}', operator='{t.operator_name}'")
