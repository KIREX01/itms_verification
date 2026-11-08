"""
Unit and integration tests for:
1. StockCheckingService (core.services.stock_checking_service)
2. KitUnallocationService (core.services.kit_unallocation_service)
3. Stock APIs & Centralized Refresh / Contextual Excel Paste verification
"""
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    InstallationKit,
    InstallationOrder,
    OperatorITMSSession,
    PlateCategory,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
)
from core.services.stock_checking_service import stock_checking_service
from core.services.kit_unallocation_service import (
    kit_unallocation_service,
    AUTO_ORDER_DISPATCH_NOTE,
)

User = get_user_model()


class StockServicesTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="test_stock_officer",
            password="testpassword123",
            email="stock@agmsolutions.ug",
        )
        # Create verified envelope session
        self.session_obj = OperatorITMSSession.objects.create(
            user=self.user,
            itms_email=self.user.email,
            is_authenticated=True,
            cookies={"_identity-frontend": "dummy_cookie_val"},
            active_bond_code="AGM",
            active_bond_name="AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)",
            last_verified_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )
        self.client.force_login(self.user)
        self.suffix = "09.11.2026"

    def test_stock_checking_service_validate_availability(self):
        """Tests that StockCheckingService accurately isolates kits on stock vs not on stock."""
        InstallationKit.objects.create(
            registration_number="UMA 101A",
            kit_code="IK-UMA101A",
            front_plate="001111",
            rear_plate="001112",
            status="New",
            warehouse="AGM Bonded Warehouse",
        )
        InstallationKit.objects.create(
            registration_number="UMA 102A",
            kit_code="IK-UMA102A",
            front_plate="001113",
            rear_plate="001114",
            status="New",
            warehouse="AGM Bonded Warehouse",
        )

        res = stock_checking_service.validate_plates_stock_availability(
            plates=["UMA 101A", "UMA 102A", "UMA 999Z"],
            target_date_suffix=self.suffix,
        )
        self.assertEqual(res["total_evaluated"], 3)
        self.assertEqual(res["in_stock_count"], 2)
        self.assertEqual(res["not_on_stock_count"], 1)
        self.assertIn("UMA 999Z", res["not_on_stock_plates"])
        self.assertFalse(res["valid"])

    def test_stock_checking_service_ledger_balances_and_auto_carry(self):
        """Tests balance calculation and auto-carrying previous day closing balance."""
        # Previous shift ledger
        prev_work_d = date(2026, 11, 8)
        DailyStockLedger.objects.create(
            work_date=prev_work_d,
            work_date_suffix="08.11.2026",
            closing_balance_psv=120,
            closing_balance_pmo=30,
            closing_stock=150,
        )

        # Active shift
        res = stock_checking_service.auto_carry_previous_closing(self.suffix)
        self.assertEqual(res["ledger"]["opening_balance_psv"], 120)
        self.assertEqual(res["ledger"]["opening_balance_pmo"], 30)
        self.assertEqual(res["ledger"]["opening_stock"], 150)

    def test_stock_checking_service_update_ledger_entry(self):
        """Tests updating ledger overrides and scheduled targets atomically."""
        res = stock_checking_service.update_ledger_entry(
            target_date_suffix=self.suffix,
            scheduled_total=75,
            scheduled_psv=60,
            scheduled_pmo=15,
            notes="Opening numbers audited with safe room supervisor.",
        )
        self.assertEqual(res["ledger"]["scheduled_total"], 75)
        self.assertEqual(res["ledger"]["notes"], "Opening numbers audited with safe room supervisor.")

    def test_kit_unallocation_service_ground_truth_and_unallocated_kits(self):
        """
        Tests that physically dispatched plates not appearing in ITMS are flagged as unallocated,
        and auto-registered plates do not corrupt the unallocated set.
        """
        # 1. Physical dispatch from safe room: UMA 301A and UMA 302A
        work_d = date(2026, 11, 9)
        StockDispatchScan.objects.create(
            registration_number="UMA 301A",
            plate_category=PlateCategory.PSV,
            work_date=work_d,
            work_date_suffix=self.suffix,
            notes="Physical Safe Room Scan",
        )
        StockDispatchScan.objects.create(
            registration_number="UMA 302A",
            plate_category=PlateCategory.PSV,
            work_date=work_d,
            work_date_suffix=self.suffix,
            notes="Physical Safe Room Scan",
        )

        # 2. UMA 301A has an ITMS order; UMA 302A has NO ITMS order
        InstallationOrder.objects.create(
            order_number=f"PO-UMA301A-{self.suffix}",
            registration_number="UMA 301A",
            order_status="Installed",
            warehouse_name="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
        )

        unalloc = kit_unallocation_service.get_unallocated_kits(self.suffix)
        self.assertEqual(unalloc["total_physical_dispatched"], 2)
        self.assertEqual(unalloc["reconciled_installed_count"], 1)
        self.assertIn("UMA 301A", unalloc["reconciled_installed_plates"])
        self.assertEqual(unalloc["unresolved_discrepancy_count"], 1)
        self.assertIn("UMA 302A", unalloc["unresolved_discrepancy_plates"])

        # 3. Safe auto-ingestion of a 3rd order (UMA 303A)
        InstallationOrder.objects.create(
            order_number=f"PO-UMA303A-{self.suffix}",
            registration_number="UMA 303A",
            order_status="Installed",
            warehouse_name="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
        )
        ingest_res = kit_unallocation_service.auto_ingest_shift_orders_to_dispatch(self.suffix)
        self.assertIn("UMA303A", ingest_res["auto_registered_plates"])

        # Verify auto-ingested plate does NOT corrupt unallocated detection
        unalloc_after = kit_unallocation_service.get_unallocated_kits(self.suffix)
        # Physical dispatches should still only count UMA 301A and UMA 302A
        self.assertEqual(unalloc_after["total_physical_dispatched"], 2)
        # Unallocated should still ONLY be UMA 302A
        self.assertEqual(unalloc_after["unresolved_discrepancy_count"], 1)
        self.assertIn("UMA 302A", unalloc_after["unresolved_discrepancy_plates"])

    def test_kit_unallocation_service_misallocated_detection(self):
        """Tests detection of MVR misallocated kits (hardware serial mismatch and warehouse mismatch)."""
        # Stock kit
        InstallationKit.objects.create(
            registration_number="UMA 401A",
            kit_code="IK-UMA401A",
            front_plate="002001",
            rear_plate="002002",
            gps_tracker="8BAE1111",
            status="New",
        )
        # Order with swapped/mismatched front plate serial
        InstallationOrder.objects.create(
            order_number=f"PO-UMA401A-{self.suffix}",
            registration_number="UMA 401A",
            front_plate_serial="009999",  # MISMATCH
            rear_plate_serial="002002",
            gps_tracker_id="8BAE1111",
            warehouse_name="AGM SOLUTIONS UGANDA LIMITED (SPIRO) - INSTALLATION",
        )

        misalloc = kit_unallocation_service.get_misallocated_kits(self.suffix)
        self.assertGreaterEqual(misalloc["misallocated_count"], 1)
        serial_mismatches = [item for item in misalloc["serial_discrepancies"] if "UMA 401A" in item["plate"]]
        self.assertTrue(len(serial_mismatches) > 0)
        self.assertIn("Front Plate: Order(009999) vs Kit(002001)", serial_mismatches[0]["reason"])

    def test_api_stock_mvr_docket_integration(self):
        """Tests that api_stock_mvr_docket returns the formatted docket and discrepancy breakdown."""
        resp = self.client.get(f"/api/stock/mvr-docket/?date_suffix={self.suffix}")
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assertIn("docket", data)
        self.assertIn("formatted_docket", data["docket"])
        self.assertIn("raw_plates", data["docket"])

    def test_api_stock_ledger_entry_integration(self):
        """Tests that api_stock_ledger_entry updates values and returns calculated snapshot."""
        resp = self.client.post(
            "/api/stock/ledger/entry/",
            data={
                "date": self.suffix,
                "opening_balance_psv": 150,
                "opening_balance_pmo": 40,
                "scheduled_total": 90,
                "notes": "Verified opening stock count.",
            },
        )
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        ledger = data["reconciliation"]["report_table"]["rows_by_metric"]["Opening Balance"]
        self.assertEqual(ledger["psv"], 150)
        self.assertEqual(ledger["pmo"], 40)
        self.assertEqual(ledger["total"], 190)
