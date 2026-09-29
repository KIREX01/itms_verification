"""
Unit & Integration Tests for Stock Monitoring & Bond Daily Plate Reconciliation.

Tests:
1. Parsing of arbitrary plate inputs (multi-line, comma-separated, barcodes, IK- prefixes).
2. Inbound Delivery intake (+Received) and automatic InstallationKit creation.
3. Bond Transfers In (+Stock) and Bond Transfers Out (-Stock) for PSV and PMO.
4. Scheduled Target setting and Opening Balance adjustments.
5. Dispatch Scans and Return Scans.
6. Reconciliation balance formula:
   Closing = Opening + Received + Transfer In - Transfer Out - Installed
7. Differentiating between Public White (PSV) and Private Yellow (PMO).
8. Fast identification of Unallocated Plates on the floor.
9. REST APIs for stock management.
10. CSV Report generation with PSV, PMO, and Total Combined columns.
"""
from datetime import date
from django.test import Client, TestCase
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    InstallationKit,
    InstallationOrder,
    PlateCategory,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
)
from core.services import stock_monitoring_service


class StockMonitoringTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.test_suffix = "290926"
        self.test_date = date(2026, 9, 29)

    def test_parse_plate_input(self):
        sample = """
        UMA711PW
        UMA993PW, UMA764PW
        IK-UMA733PW
        #PO-UMA744PW-290926
        """
        plates = stock_monitoring_service.parse_plate_input(sample)
        self.assertEqual(plates, ["UMA711PW", "UMA993PW", "UMA764PW", "UMA733PW", "UMA744PW"])

    def test_delivery_intake_and_auto_kit_creation(self):
        sample_plates = ["UMA711PW", "UMA993PW", "UMA764PW"]
        res = stock_monitoring_service.record_delivery(
            delivery_number="DEL-TEST-001",
            plates=sample_plates,
            supplier="Uganda Factory Central",
            plate_category=PlateCategory.PSV,
            target_date_suffix=self.test_suffix,
            auto_create_kits=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["plates_count"], 3)
        self.assertEqual(res["created_kits_count"], 3)

        # Check delivery items
        deliv = StockDelivery.objects.get(delivery_number="DEL-TEST-001")
        self.assertEqual(deliv.items.count(), 3)
        self.assertEqual(deliv.plate_category, PlateCategory.PSV)

        # Check auto-created installation kits
        kits = InstallationKit.objects.filter(registration_number__in=sample_plates)
        self.assertEqual(kits.count(), 3)
        for k in kits:
            self.assertEqual(k.status, "New")
            self.assertEqual(k.warehouse, "Warehouse Stock")

    def test_bond_transfers_in_and_out(self):
        # Transfer in: 50 PSV plates from Kampala Bond
        res_in = stock_monitoring_service.record_bond_transfer(
            transfer_type="TRANSFER_IN",
            plate_category="PSV",
            plates_count=50,
            other_bond_name="Kampala Bond",
            target_date_suffix=self.test_suffix,
        )
        self.assertTrue(res_in["success"])

        # Transfer out: 10 PMO plates to Jinja Bond
        res_out = stock_monitoring_service.record_bond_transfer(
            transfer_type="TRANSFER_OUT",
            plate_category="PMO",
            plates_count=10,
            other_bond_name="Jinja Bond",
            target_date_suffix=self.test_suffix,
        )
        self.assertTrue(res_out["success"])

        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        self.assertEqual(recon["report_table"]["rows"][2]["psv"], 50)  # Bond Transfer In PSV
        self.assertEqual(recon["report_table"]["rows"][3]["pmo"], 10)  # Bond Transfer Out PMO

    def test_full_reconciliation_equation_psv_and_pmo(self):
        """
        Verify exact user equation:
        Closing = Opening + Received + Transfer In - Transfer Out - Installed
        """
        # 1. Set Opening balances: 1000 PSV, 500 PMO
        stock_monitoring_service.set_opening_balances(
            opening_psv=1000,
            opening_pmo=500,
            target_date_suffix=self.test_suffix,
        )

        # 2. Record deliveries: 200 PSV, 50 PMO
        psv_plates = [f"UMA{i:03d}PW" for i in range(1, 201)]
        pmo_plates = [f"UMA{i:03d}PX" for i in range(1, 51)]
        stock_monitoring_service.record_delivery("DEL-PSV-1", psv_plates, plate_category="PSV", target_date_suffix=self.test_suffix)
        stock_monitoring_service.record_delivery("DEL-PMO-1", pmo_plates, plate_category="PMO", target_date_suffix=self.test_suffix)

        # 3. Bond transfers: In: 50 PSV; Out: 20 PSV, 10 PMO
        stock_monitoring_service.record_bond_transfer("TRANSFER_IN", "PSV", 50, "Bond B", target_date_suffix=self.test_suffix)
        stock_monitoring_service.record_bond_transfer("TRANSFER_OUT", "PSV", 20, "Bond C", target_date_suffix=self.test_suffix)
        stock_monitoring_service.record_bond_transfer("TRANSFER_OUT", "PMO", 10, "Bond D", target_date_suffix=self.test_suffix)

        # 4. Scheduled target: 120 PSV, 30 PMO
        stock_monitoring_service.set_scheduled_target(
            scheduled_psv=120,
            scheduled_pmo=30,
            target_date_suffix=self.test_suffix,
        )

        # 5. Create Installed Orders: 115 PSV installed, 28 PMO installed
        for i in range(1, 116):
            p = f"UMA{i:03d}PW"
            InstallationOrder.objects.create(
                order_number=f"PO-{p}-{self.test_suffix}",
                registration_number=p,
                order_status="Installed",
                is_archived=True,
            )

        for i in range(1, 29):
            p = f"UMA{i:03d}PX"
            InstallationOrder.objects.create(
                order_number=f"PO-{p}-{self.test_suffix}",
                registration_number=p,
                order_status="Installed",
                is_archived=True,
            )

        # 6. Reconcile
        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        rows_by_metric = {r["metric"]: r for r in recon["report_table"]["rows"]}

        # Check PSV:
        # Opening: 1000
        # Received: 200
        # Transfer In: 50
        # Transfer Out: 20
        # Installed: 115
        # Expected Closing: 1000 + 200 + 50 - 20 - 115 = 1115
        self.assertEqual(rows_by_metric["Opening Balance"]["psv"], 1000)
        self.assertEqual(rows_by_metric["Kits Received"]["psv"], 200)
        self.assertEqual(rows_by_metric["Bond Transfer In"]["psv"], 50)
        self.assertEqual(rows_by_metric["Bond Transfer Out"]["psv"], 20)
        self.assertEqual(rows_by_metric["Scheduled (Target)"]["psv"], 120)
        self.assertEqual(rows_by_metric["Kits Installed (Actual)"]["psv"], 115)
        self.assertEqual(rows_by_metric["Closing Balance"]["psv"], 1115)
        self.assertEqual(rows_by_metric["Scheduled Target Variance"]["psv"], -5)

        # Check PMO:
        # Opening: 500
        # Received: 50
        # Transfer In: 0
        # Transfer Out: 10
        # Installed: 28
        # Expected Closing: 500 + 50 + 0 - 10 - 28 = 512
        self.assertEqual(rows_by_metric["Opening Balance"]["pmo"], 500)
        self.assertEqual(rows_by_metric["Kits Received"]["pmo"], 50)
        self.assertEqual(rows_by_metric["Bond Transfer In"]["pmo"], 0)
        self.assertEqual(rows_by_metric["Bond Transfer Out"]["pmo"], 10)
        self.assertEqual(rows_by_metric["Scheduled (Target)"]["pmo"], 30)
        self.assertEqual(rows_by_metric["Kits Installed (Actual)"]["pmo"], 28)
        self.assertEqual(rows_by_metric["Closing Balance"]["pmo"], 512)
        self.assertEqual(rows_by_metric["Scheduled Target Variance"]["pmo"], -2)

        # Check Combined Totals:
        # Total Closing: 1115 + 512 = 1627
        self.assertEqual(rows_by_metric["Closing Balance"]["total"], 1627)
        self.assertEqual(rows_by_metric["Scheduled Target Variance"]["total"], -7)

    def test_floor_dispatch_and_unallocated_detection(self):
        """
        Plates taken out for shift: if they don't appear in orders or archive,
        and were not returned -> flagged as unallocated!
        """
        # Dispatch 5 plates
        plates = ["UMA711PW", "UMA993PW", "UMA764PW", "UMA733PW", "UMA744PW"]
        stock_monitoring_service.record_dispatch_scans(
            plates=plates,
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )

        # 1 installed in ITMS
        InstallationOrder.objects.create(
            order_number=f"PO-UMA711PW-{self.test_suffix}",
            registration_number="UMA711PW",
            order_status="Installed",
            is_archived=True,
        )

        # 1 pending in ITMS
        InstallationOrder.objects.create(
            order_number=f"PO-UMA993PW-{self.test_suffix}",
            registration_number="UMA993PW",
            order_status="Under installation",
            is_archived=False,
        )

        # 1 returned uninstalled
        stock_monitoring_service.record_return_scans(
            plates=["UMA764PW"],
            reason=StockReturnScan.Reason.BIKE_NO_SHOW,
            target_date_suffix=self.test_suffix,
        )

        # Remaining 2 (UMA733PW, UMA744PW) should be UNALLOCATED
        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        floor = recon["floor_operations"]
        self.assertEqual(floor["dispatched_count"], 5)
        self.assertEqual(floor["returned_count"], 1)
        self.assertEqual(floor["net_dispatched"], 4)
        self.assertEqual(floor["unallocated_discrepancy"], 2)
        self.assertIn("UMA733PW", floor["unallocated_plates"])
        self.assertIn("UMA744PW", floor["unallocated_plates"])

    def test_stock_rest_apis(self):
        # 1. API: Opening
        r1 = self.client.post("/api/stock/opening/", {"opening_psv": 500, "opening_pmo": 250, "date": self.test_suffix})
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["success"])

        # 2. API: Scheduled
        r2 = self.client.post("/api/stock/scheduled/", {"scheduled_psv": 80, "scheduled_pmo": 20, "date": self.test_suffix})
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["success"])

        # 3. API: Delivery
        r3 = self.client.post("/api/stock/delivery/", {
            "delivery_number": "DEL-API-01",
            "plates": "UMA101PW\nUMA102PW",
            "plate_category": "PSV",
            "date": self.test_suffix,
        })
        self.assertEqual(r3.status_code, 200)
        self.assertTrue(r3.json()["success"])

        # 4. API: Bond Transfer
        r4 = self.client.post("/api/stock/transfer/", {
            "transfer_type": "TRANSFER_IN",
            "plate_category": "PSV",
            "plates_count": 25,
            "other_bond_name": "Kampala East",
            "date": self.test_suffix,
        })
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.json()["success"])

        # 5. API: Reconciliation GET
        r5 = self.client.get(f"/api/stock/reconciliation/?date={self.test_suffix}")
        self.assertEqual(r5.status_code, 200)
        recon_data = r5.json()["reconciliation"]
        self.assertIn("report_table", recon_data)

        # 6. API: CSV Export GET
        r6 = self.client.get(f"/api/stock/export/?date={self.test_suffix}")
        self.assertEqual(r6.status_code, 200)
        self.assertEqual(r6["Content-Type"], "text/csv")
        csv_text = r6.content.decode("utf-8")
        self.assertIn("Public White (PSV)", csv_text)
        self.assertIn("Private Yellow (PMO)", csv_text)
        self.assertIn("Closing Balance", csv_text)

    def test_scanner_input_formats_and_deduplication(self):
        # Test various USB and QR barcode scanner payloads
        scanner_payloads = [
            "https://itms.go.ug/verify?plate=UMA711PW",
            '{"plate": "UMA993PW"}',
            "IK-UMA764PW",
            "#PO-UMA733PW-290926",
            "  uma 744 pw  \r\n",
            "UMA711PW",  # Duplicate of first
            "IK:UMA993PW",  # Duplicate of second
            "UMA074PX",
        ]
        unique_plates, dup_count, dup_plates = stock_monitoring_service.parse_plate_input_with_stats(scanner_payloads)
        self.assertEqual(dup_count, 2)
        self.assertEqual(dup_plates, ["UMA711PW", "UMA993PW"])
        self.assertEqual(
            unique_plates,
            ["UMA711PW", "UMA993PW", "UMA764PW", "UMA733PW", "UMA744PW", "UMA074PX"]
        )

    def test_dispatch_duplicate_rejection(self):
        # 1. First batch dispatch
        res1 = stock_monitoring_service.record_dispatch_scans(
            plates=["UMA711PW", "UMA993PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(res1["newly_dispatched"], 2)
        self.assertEqual(res1["already_dispatched"], 0)

        # 2. Second batch dispatch with 1 duplicate and 1 new
        res2 = stock_monitoring_service.record_dispatch_scans(
            plates=["UMA711PW", "UMA764PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(res2["newly_dispatched"], 1)
        self.assertEqual(res2["already_dispatched"], 1)
        self.assertIn("UMA711PW", res2["already_dispatched_plates"])

        # Total in DB should be exactly 3 unique plates
        total_scans = StockDispatchScan.objects.filter(work_date_suffix=self.test_suffix).count()
        self.assertEqual(total_scans, 3)

    def test_delivery_duplicate_deduplication(self):
        res = stock_monitoring_service.record_delivery(
            delivery_number="DEL-DUP-TEST",
            plates=["UMA711PW", "UMA711PW", "UMA993PW", "IK-UMA993PW"],
            supplier="Factory",
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
            auto_create_kits=True,
        )
        self.assertEqual(res["plates_count"], 2)
        self.assertEqual(res["duplicate_scans_skipped"], 2)
        self.assertEqual(res["created_kits_count"], 2)

    def test_delivery_notes_reference_and_query(self):
        # 1. Test auto reference generation
        ref1 = stock_monitoring_service.generate_delivery_note_reference(self.test_date)
        self.assertTrue(ref1.startswith("DN-20260929-"))

        # 2. Record delivery with AUTO delivery number and paper reference
        res1 = stock_monitoring_service.record_delivery(
            delivery_number="AUTO",
            paper_note_reference="PAPER-DN-8891",
            plates=["UMA801PW", "UMA802PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(res1["delivery_number"], ref1)
        self.assertEqual(res1["paper_note_reference"], "PAPER-DN-8891")

        # 3. Query delivery notes for date
        notes = stock_monitoring_service.get_delivery_notes_for_date(self.test_suffix)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["delivery_number"], ref1)
        self.assertEqual(notes[0]["paper_note_reference"], "PAPER-DN-8891")
        self.assertEqual(notes[0]["total_plates_count"], 2)
        self.assertIn("UMA801PW", notes[0]["plates"])

    def test_mvr_unallocated_docket_generation(self):
        # Dispatch 3 plates
        stock_monitoring_service.record_dispatch_scans(
            plates=["UMA901PW", "UMA902PW", "UMA903PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )
        # 1 installed in ITMS
        InstallationOrder.objects.create(
            order_number=f"PO-UMA901PW-{self.test_suffix}",
            registration_number="UMA901PW",
            order_status="Installed",
            is_archived=True,
        )
        # 1 returned to safe room
        stock_monitoring_service.record_return_scans(
            plates=["UMA902PW"],
            target_date_suffix=self.test_suffix,
        )

        # 1 plate (UMA903PW) physically fitted but skipped by MVR
        docket = stock_monitoring_service.get_mvr_unallocated_docket(self.test_suffix)
        self.assertTrue(docket["success"])
        self.assertEqual(docket["count"], 1)
        self.assertEqual(docket["unallocated_plates"], ["UMA903PW"])
        self.assertEqual(docket["raw_plates"], "UMA903PW")
        self.assertIn("AGM BONDED WAREHOUSE — MVR ALLOCATION EXCEPTION DOCKET", docket["formatted_docket"])
        self.assertIn("UMA903PW", docket["formatted_docket"])

    def test_delivery_notes_and_mvr_docket_rest_apis(self):
        # Record a delivery and dispatch a plate
        stock_monitoring_service.record_delivery(
            delivery_number="DN-API-TEST-01",
            paper_note_reference="DN/TEST/991",
            plates=["UMA950PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )
        stock_monitoring_service.record_dispatch_scans(
            plates=["UMA950PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )

        # 1. API GET delivery notes
        r_deliv = self.client.get(f"/api/stock/delivery-notes/?date={self.test_suffix}")
        self.assertEqual(r_deliv.status_code, 200)
        deliv_json = r_deliv.json()
        self.assertTrue(deliv_json["success"])
        self.assertGreaterEqual(deliv_json["count"], 1)
        self.assertEqual(deliv_json["delivery_notes"][0]["paper_note_reference"], "DN/TEST/991")

        # 2. API GET MVR docket
        r_docket = self.client.get(f"/api/stock/mvr-docket/?date={self.test_suffix}")
        self.assertEqual(r_docket.status_code, 200)
        docket_json = r_docket.json()
        self.assertTrue(docket_json["success"])
        self.assertIn("raw_plates", docket_json["docket"])
        self.assertIn("formatted_docket", docket_json["docket"])
        self.assertIn("UMA950PW", docket_json["docket"]["unallocated_plates"])


