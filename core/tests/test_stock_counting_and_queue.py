from datetime import date, timedelta
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
import json
import uuid

from core.models import (
    DailyStockLedger,
    InstallationOrder,
    StockDelivery,
    StockDeliveryItem,
    StockBondTransfer,
    VehicleInstallationPair,
    EvidenceImage,
    IngestionBatch,
    PlateCategory,
)
from core.services.stock_monitoring_service import compute_daily_reconciliation
from core.views.pairs import api_pairs_list

User = get_user_model()


class StockCountingAndQueueTabsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin_stock_test",
            email="admin@itms.ug",
            password="adminpassword123",
        )
        self.factory = RequestFactory()

    def test_stock_counting_installed_kits_pending_plus_archive(self):
        """installed kits = pending orders + archive for that shift."""
        today = date(2026, 11, 2)
        suffix = "021126"

        # Create 1 archived installed order
        InstallationOrder.objects.create(
            order_number=f"PO-UMA001AA-{suffix}",
            registration_number="UMA001AA",
            status=InstallationOrder.Status.INSTALLED,
            is_archived=True,
            order_status="Installed",
        )
        # Create 1 active pending order
        InstallationOrder.objects.create(
            order_number=f"PO-UMA002BB-{suffix}",
            registration_number="UMA002BB",
            status=InstallationOrder.Status.PENDING,
            is_archived=False,
            order_status="Ready for installation",
        )

        recon = compute_daily_reconciliation(suffix)
        tot = recon["total"]

        # Installed kits must equal 2 (1 pending + 1 archive)
        self.assertEqual(tot["installed"], 2)

    def test_opening_stock_equals_previous_day_closing_stock(self):
        """opening stock = previous day closing stock."""
        day1 = date(2026, 11, 1)
        suffix1 = "011126"
        day2 = date(2026, 11, 2)
        suffix2 = "021126"

        # Previous day closing stock was 250 (PSV: 200, PMO: 50)
        DailyStockLedger.objects.create(
            work_date=day1,
            work_date_suffix=suffix1,
            opening_stock=300,
            opening_balance_psv=240,
            opening_balance_pmo=60,
            delivered_count=0,
            installed_count=50,
            closing_stock=250,
            closing_balance_psv=200,
            closing_balance_pmo=50,
        )

        # Compute day 2
        recon = compute_daily_reconciliation(suffix2)
        tot = recon["total"]

        # Day 2 opening stock must equal Day 1 closing stock (250)
        self.assertEqual(tot["opening"], 250)

    def test_closing_stock_formula(self):
        """closing stock = opening stock + kits received - kits installed - bond transfer out + bond transfer in."""
        work_d = date(2026, 11, 3)
        suffix = "031126"

        # Set ledger with opening stock = 100
        ledger = DailyStockLedger.objects.create(
            work_date=work_d,
            work_date_suffix=suffix,
            opening_stock=100,
            opening_balance_psv=100,
            opening_balance_pmo=0,
        )

        # Inbound delivery: 50 received
        delv = StockDelivery.objects.create(
            delivery_number="DELV-TEST-001",
            target_date_suffix=suffix,
            delivery_date=work_d,
            total_plates_count=50,
            plate_category=PlateCategory.PSV,
        )

        # Transfer in: 10
        StockBondTransfer.objects.create(
            transfer_number="TRF-IN-TEST",
            target_date_suffix=suffix,
            transfer_date=work_d,
            transfer_type=StockBondTransfer.TransferType.TRANSFER_IN,
            plate_category=PlateCategory.PSV,
            plates_count=10,
        )

        # Transfer out: 5
        StockBondTransfer.objects.create(
            transfer_number="TRF-OUT-TEST",
            target_date_suffix=suffix,
            transfer_date=work_d,
            transfer_type=StockBondTransfer.TransferType.TRANSFER_OUT,
            plate_category=PlateCategory.PSV,
            plates_count=5,
        )

        # 20 orders installed (pending + archive)
        for i in range(15):
            InstallationOrder.objects.create(
                order_number=f"PO-TEST-ARC-{i}-{suffix}",
                registration_number=f"UMA{i:03d}P",
                status=InstallationOrder.Status.INSTALLED,
                is_archived=True,
            )
        for i in range(5):
            InstallationOrder.objects.create(
                order_number=f"PO-TEST-PND-{i}-{suffix}",
                registration_number=f"UMA{i+20:03d}P",
                status=InstallationOrder.Status.PENDING,
                is_archived=False,
            )

        recon = compute_daily_reconciliation(suffix)
        tot = recon["total"]

        # Expected: 100 (open) + 50 (recv) - 20 (inst) - 5 (out) + 10 (in) = 135
        expected_closing = 100 + 50 - 20 - 5 + 10
        self.assertEqual(tot["opening"], 100)
        self.assertEqual(tot["received"], 50)
        self.assertEqual(tot["installed"], 20)
        self.assertEqual(tot["transfer_out"], 5)
        self.assertEqual(tot["transfer_in"], 10)
        self.assertEqual(tot["closing_stock"], expected_closing)

    def test_queue_tab_issues_filter_includes_conflicts_and_unlinked(self):
        """All problem pairs (conflicts, unlinked orders, etc.) must be under issues."""
        batch = IngestionBatch.objects.create(batch_id="BATCH-QUEUE-TEST", operator=self.user)
        f1 = EvidenceImage.objects.create(batch=batch, file_hash=uuid.uuid4().hex, orientation=EvidenceImage.Orientation.FRONT)
        r1 = EvidenceImage.objects.create(batch=batch, file_hash=uuid.uuid4().hex, orientation=EvidenceImage.Orientation.REAR)

        # Pair in conflict / unlinked
        pair_conflict = VehicleInstallationPair.objects.create(
            front_image=f1,
            rear_image=r1,
            registration_number_detected="UMA111ZZ",
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        )

        req = self.factory.get("/api/pairs/?status=ISSUES")
        req.user = self.user
        resp = api_pairs_list(req)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content.decode("utf-8"))
        pair_ids = [p["id"] for p in data.get("pairs", [])]
        self.assertIn(pair_conflict.id, pair_ids)
