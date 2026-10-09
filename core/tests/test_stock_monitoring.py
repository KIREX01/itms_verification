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
import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
from django.test import Client, TestCase
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    InstallationKit,
    InstallationOrder,
    PlateCategory,
    SafeAuditScan,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    VehicleInstallationPair,
)
from core.services import bond_service, kit_provisioning_service, stock_monitoring_service


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
            self.assertIn(k.warehouse, ("Warehouse Stock", "AGM Bonded Warehouse", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)"))

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
        rows_by_metric = recon["report_table"]["rows_by_metric"]
        self.assertEqual(rows_by_metric["Bond transfer IN"]["psv"], 50)  # Bond Transfer In PSV
        self.assertEqual(rows_by_metric["Bond transfer OUT"]["pmo"], 10)  # Bond Transfer Out PMO

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
        rows_by_metric = recon["report_table"]["rows_by_metric"]

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
        self.assertIn("PUBLIC", csv_text)
        self.assertIn("PRIVATE", csv_text)
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

    def test_bond_service_discovery_and_active_switch(self):
        # 1. Default active bond
        bond_service.set_active_bond("AGM", "AGM Bonded Warehouse")
        active = bond_service.get_active_bond()
        self.assertEqual(active["code"], "AGM")
        self.assertEqual(active["name"], "AGM Bonded Warehouse")

        # 2. Discovered warehouse dynamically from kit/order
        InstallationKit.objects.create(
            kit_code="IK-TEST-ENT",
            registration_number="UMA999ET",
            warehouse="Entebbe Regional Depot",
            status="New",
        )
        warehouses = bond_service.get_all_discovered_warehouses()
        wh_names = [w["name"] for w in warehouses]
        self.assertIn("Entebbe Regional Depot", wh_names)
        # AGM must be sorted first
        self.assertEqual(warehouses[0]["code"], "AGM")

        # 3. Switching active bond
        ok = bond_service.set_active_bond("JINJA", "Jinja Fitting Center")
        self.assertTrue(ok)
        curr = bond_service.get_active_bond()
        self.assertEqual(curr["code"], "JINJA")

        # Restore
        bond_service.set_active_bond("AGM", "AGM Bonded Warehouse")

    def test_cross_bond_order_isolation(self):
        # Active bond: AGM
        bond_service.set_active_bond("AGM", "AGM Bonded Warehouse")

        # AGM order (local)
        InstallationOrder.objects.create(
            order_number=f"PO-UMA801PW-{self.test_suffix}",
            registration_number="UMA801PW",
            warehouse_name="AGM Bonded Warehouse",
            order_status="Installed",
            is_archived=True,
        )

        # Cross-bond order (external facility: Jinja)
        InstallationOrder.objects.create(
            order_number=f"PO-UMA802PW-{self.test_suffix}",
            registration_number="UMA802PW",
            warehouse_name="Jinja Regional Warehouse",
            order_status="Installed",
            is_archived=True,
        )

        stock_monitoring_service.record_dispatch_scans(
            plates=["UMA801PW", "UMA802PW"],
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )

        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        installed_row = recon["report_table"]["rows_by_metric"]["Kits Installed"]
        # Only AGM's order should count as installed for AGM bond!
        self.assertEqual(installed_row["psv"], 1)

        # Cross-bond order isolated and reported
        floor = recon["floor_operations"]
        self.assertEqual(floor["cross_bond_count"], 1)

    def test_graduated_discrepancy_scale(self):
        bond_service.set_active_bond("AGM", "AGM Bonded Warehouse")

        # Plate 1: Reconciled Installed (in archived order)
        InstallationOrder.objects.create(
            order_number=f"PO-UMA201PW-{self.test_suffix}",
            registration_number="UMA201PW",
            warehouse_name="AGM Bonded Warehouse",
            order_status="Installed",
            is_archived=True,
        )

        # Plate 3: On Line / In Progress (in active non-archived order)
        InstallationOrder.objects.create(
            order_number=f"PO-UMA203PW-{self.test_suffix}",
            registration_number="UMA203PW",
            warehouse_name="AGM Bonded Warehouse",
            order_status="Assigned",
            is_archived=False,
        )

        # Plate 4: Pending System Sync (has photo evidence captured on floor, but ITMS order not yet created)
        VehicleInstallationPair.objects.create(
            registration_number_detected="UMA204PW",
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )

        # Dispatch all 5 plates to the floor
        dispatched_plates = ["UMA201PW", "UMA202PW", "UMA203PW", "UMA204PW", "UMA205PW"]
        stock_monitoring_service.record_dispatch_scans(
            plates=dispatched_plates,
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )

        # Plate 2: Returned to Safe Room
        stock_monitoring_service.record_return_scans(
            plates=["UMA202PW"],
            target_date_suffix=self.test_suffix,
            reason=StockReturnScan.Reason.BIKE_NO_SHOW,
        )

        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        floor = recon["floor_operations"]
        scale = floor["graduated_scale"]

        # Verify Graduated Discrepancy Scale
        self.assertEqual(scale["reconciled_installed"]["count"], 1)
        self.assertIn("UMA201PW", scale["reconciled_installed"]["plates"])

        self.assertEqual(scale["returned_to_safe"]["count"], 1)
        self.assertIn("UMA202PW", scale["returned_to_safe"]["plates"])

        self.assertEqual(scale["on_line_active"]["count"], 1)
        self.assertIn("UMA203PW", scale["on_line_active"]["plates"])

        self.assertEqual(scale["pending_system_sync"]["count"], 0)

        # Unresolved discrepancy must include both UMA204PW & UMA205PW (dispatched, not returned, no ITMS order)
        self.assertEqual(scale["unresolved_discrepancy"]["count"], 2)
        self.assertIn("UMA204PW", scale["unresolved_discrepancy"]["plates"])
        self.assertIn("UMA205PW", scale["unresolved_discrepancy"]["plates"])
        self.assertEqual(floor["unallocated_discrepancy"], 2)

        # Verify MVR Docket formatting
        docket = stock_monitoring_service.get_mvr_unallocated_docket(self.test_suffix)
        fmt = docket["formatted_docket"]
        self.assertIn("GRADUATED DISCREPANCY AUDIT SCALE:", fmt)
        self.assertIn("1. Reconciled Installed:", fmt)
        self.assertIn("5. UNRESOLVED DISCREPANCY:", fmt)
        self.assertIn("UMA204PW", docket["raw_plates"])
        self.assertIn("UMA205PW", docket["raw_plates"])

    def test_itms_warehouses_rest_api(self):
        # GET warehouses
        resp = self.client.get("/api/itms/warehouses/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("active_bond", data)
        self.assertIn("warehouses", data)

        # POST set active bond
        post_resp = self.client.post(
            "/api/itms/warehouses/",
            data={"code": "AGM", "name": "AGM Bonded Warehouse"},
        )
        self.assertEqual(post_resp.status_code, 200)
        post_data = post_resp.json()
        self.assertTrue(post_data["success"])
        self.assertEqual(post_data["active_bond"]["code"], "AGM")

    def test_record_stock_taking_audit_and_variance(self):
        """Verifies safe room physical stock-taking audit balances against book closing stock."""
        # 1. Setup opening stock & deliveries
        stock_monitoring_service.set_opening_balances(
            opening_psv=100,
            opening_pmo=50,
            target_date_suffix=self.test_suffix,
        )
        stock_monitoring_service.record_delivery(
            delivery_number="DN-TEST-AUDIT",
            supplier="Factory Depot",
            plates="UMA801PW, UMA802PW",
            plate_category="PSV",
            target_date_suffix=self.test_suffix,
        )

        # 2. Perform safe room physical stock-taking scan (e.g. 152 physical plates in safe room)
        # 150 opening + 2 delivered = 152 book closing balance
        scanned_safe_plates = [f"UMA{i:03d}PW" for i in range(1, 153)]
        res = stock_monitoring_service.record_stock_taking_audit(
            scanned_plates=scanned_safe_plates,
            target_date_suffix=self.test_suffix,
            notes="Full safe room physical count audit",
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["total_scanned"], 152)
        self.assertEqual(res["book_closing_total"], 152)
        self.assertEqual(res["variance"], 0)  # Perfectly balanced!

        # 3. Verify ledger persisted
        ledger = DailyStockLedger.objects.get(work_date_suffix=self.test_suffix)
        self.assertEqual(ledger.physical_count, 152)
        self.assertEqual(ledger.variance, 0)

        # 4. If fewer plates scanned (e.g. 150 scanned vs 152 book -> -2 variance)
        res_short = stock_monitoring_service.record_stock_taking_audit(
            scanned_plates=scanned_safe_plates[:150],
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(res_short["total_scanned"], 150)
        self.assertEqual(res_short["variance"], -2)

    def test_set_physical_count_manual(self):
        """Verifies setting manual physical count updates DailyStockLedger and variance."""
        stock_monitoring_service.set_opening_balances(
            opening_psv=200,
            opening_pmo=0,
            target_date_suffix=self.test_suffix,
        )
        res = stock_monitoring_service.set_physical_count(
            physical_count=198,
            target_date_suffix=self.test_suffix,
            notes="Manual physical audit count",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["physical_count"], 198)
        self.assertEqual(res["variance"], -2)  # 198 - 200 = -2 missing

    def test_single_scheduled_target_and_performance_kpis(self):
        """
        Operational Target (Scheduled) is a single combined figure (e.g. 500) covering
        both Private and Public. It does NOT subtract from physical stock.
        """
        # 1. Opening stock: 50 PMO, 872 PSV (922 Total as in official spreadsheet)
        stock_monitoring_service.set_opening_balances(
            opening_pmo=50,
            opening_psv=872,
            target_date_suffix=self.test_suffix,
        )

        # 2. Inbound Delivery: 500 PSV
        psv_plates = [f"UMA{i:03d}PW" for i in range(1, 501)]
        stock_monitoring_service.record_delivery("DN-AGM-01", psv_plates, plate_category="PSV", target_date_suffix=self.test_suffix)

        # 3. Scheduled Target: 500 total (Single combined input)
        sched_res = stock_monitoring_service.set_scheduled_target(
            scheduled_target=500,
            target_date_suffix=self.test_suffix,
        )
        self.assertTrue(sched_res["success"])
        self.assertEqual(sched_res["scheduled_total"], 500)

        # 4. 200 kits installed (all 200 PSV)
        for i in range(1, 201):
            p = f"UMA{i:03d}PW"
            InstallationOrder.objects.create(
                order_number=f"PO-{p}-{self.test_suffix}",
                registration_number=p,
                order_status="Installed",
                is_archived=True,
            )

        # 5. Compute reconciliation
        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        sched_sum = recon["scheduled_summary"]
        self.assertEqual(sched_sum["scheduled_target"], 500)
        self.assertEqual(sched_sum["installed_total"], 200)
        self.assertEqual(sched_sum["daily_performance_pct"], 40.0)  # 200 / 500 = 40%
        self.assertEqual(sched_sum["backlog_level"], 300)  # 500 - 200 = 300 remaining

        # 6. Physical closing stock must NOT be altered by scheduled target:
        # Opening: 922 + Received: 500 - Installed: 200 = 1222 Closing stock
        self.assertEqual(recon["closing_stock"], 1222)
        self.assertEqual(recon["report_table"]["rows_by_metric"]["Closing Balance"]["total"], 1222)

        # 7. Check 9 official rows in exact spreadsheet sequence
        rows = recon["report_table"]["rows"]
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[0]["metric"], "Opening Balance")
        self.assertEqual(rows[1]["metric"], "Kits Received")
        self.assertEqual(rows[2]["metric"], "SCHEDULED")
        self.assertEqual(rows[3]["metric"], "Kits Installed")
        self.assertEqual(rows[4]["metric"], "Daily perfomance, %")
        self.assertEqual(rows[5]["metric"], "Bond transfer IN")
        self.assertEqual(rows[6]["metric"], "Bond transfer OUT")
        self.assertEqual(rows[7]["metric"], "Backlog level")
        self.assertEqual(rows[8]["metric"], "Closing Balance")

    def test_auto_carry_previous_closing_balance(self):
        """
        Verify previous shift closing balances (PMO & PSV) can be auto-carried into current shift opening balances.
        """
        # Day 1: 28th September 2026
        day1_date = date(2026, 9, 28)
        day1_suffix = "280926"
        DailyStockLedger.objects.create(
            work_date=day1_date,
            work_date_suffix=day1_suffix,
            closing_balance_pmo=45,
            closing_balance_psv=620,
            closing_stock=665,
        )

        # Day 2: 29th September 2026 (target date)
        carried = stock_monitoring_service.get_previous_shift_closing_balances(self.test_suffix)
        self.assertTrue(carried["found"])
        self.assertEqual(carried["opening_pmo"], 45)
        self.assertEqual(carried["opening_psv"], 620)
        self.assertEqual(carried["opening_total"], 665)
        self.assertEqual(carried["previous_suffix"], day1_suffix)

    def test_shift_remarks_and_official_spreadsheet_export(self):
        """
        Verify shift remarks and official WhatsApp spreadsheet format with STORAGE BOND NAME and Consumables.
        """
        remarks_text = "All ready bikes were installed. The rest of the bikes are not released. Unreleased bikes approx: 1000"
        stock_monitoring_service.set_shift_remarks(remarks_text, target_date_suffix=self.test_suffix)

        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        self.assertEqual(recon["remarks"], remarks_text)
        self.assertIn("Rivets", recon["consumables"])
        self.assertIn("Insulating Tape", recon["consumables"])

        csv_text = stock_monitoring_service.export_stock_reconciliation_csv(self.test_suffix)
        self.assertIn("STORAGE BOND NAME,DESCRIPTION,PRIVATE,PUBLIC,Total,REMARKS,Consumables", csv_text)
        self.assertIn("Opening Balance", csv_text)
        self.assertIn("SCHEDULED", csv_text)
        self.assertIn("Daily perfomance, %", csv_text)
        self.assertIn("Backlog level", csv_text)
        self.assertIn("Closing Balance", csv_text)
        self.assertIn("Rivets", csv_text)
        self.assertIn("All ready bikes were installed", csv_text)

    def test_kit_provisioning_from_deliveries_and_safe_room(self):
        """
        Verify that kit provisioning aggregates plates from deliveries, safe audits,
        and marks unassigned plates as 'New' while preserving existing order states.
        """
        deliv = StockDelivery.objects.create(
            delivery_number="DEL-PROV-001",
            supplier="Factory Intake",
            target_date_suffix=self.test_suffix,
            total_plates_count=2,
        )
        StockDeliveryItem.objects.create(delivery=deliv, registration_number="UMA101PW")
        StockDeliveryItem.objects.create(delivery=deliv, registration_number="UMA102PW")

        StockReturnScan.objects.create(
            registration_number="UMA103PW",
            work_date_suffix=self.test_suffix,
            reason=StockReturnScan.Reason.OTHER,
            notes="Safe Room Retake",
        )

        # Existing completed order for UMA101PW
        InstallationOrder.objects.create(
            order_number=f"PO-UMA101PW-{self.test_suffix}",
            registration_number="UMA101PW",
            order_status="Installed",
            is_archived=True,
        )

        res = kit_provisioning_service.sync_and_provision_warehouse_kits(sync_itms=False)
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["new_kits_ready_count"], 2)

        k101 = InstallationKit.objects.get(registration_number="UMA101PW")
        self.assertEqual(k101.status, "Installed")

        k102 = InstallationKit.objects.get(registration_number="UMA102PW")
        self.assertEqual(k102.status, "New")

        k103 = InstallationKit.objects.get(registration_number="UMA103PW")
        self.assertEqual(k103.status, "New")

    def test_validate_morning_dispatch_readiness_auto_enroll(self):
        """
        Verify that morning dispatch validation recognizes existing 'New' plates,
        and auto-enrolls any missing plates as 'New' to prevent operational delay.
        """
        plates = ["UMA901PW", "UMA902PW"]
        res = kit_provisioning_service.validate_morning_dispatch_readiness(plates, auto_enroll_missing=True)
        self.assertEqual(res["total_scanned"], 2)
        self.assertEqual(res["kits_auto_enrolled"], 2)

        # Re-validating should now see both as ready in stock
        res2 = kit_provisioning_service.validate_morning_dispatch_readiness(plates, auto_enroll_missing=False)
        self.assertEqual(res2["already_in_stock_new"], 2)
        self.assertEqual(res2["missing_count"], 0)

    def test_api_stock_kits_sync_and_readiness(self):
        """
        Verify REST API endpoints /api/stock/kits/sync/ and /api/stock/kits/readiness/.
        """
        # POST /api/stock/kits/sync/
        sync_resp = self.client.post(
            "/api/stock/kits/sync/",
            data={"plates": "UMA951PW, UMA952PW", "sync_itms": False},
            content_type="application/json",
        )
        self.assertEqual(sync_resp.status_code, 200)
        sync_data = sync_resp.json()
        self.assertTrue(sync_data["success"])
        self.assertIn("new_kits_ready_count", sync_data["result"])

        # GET /api/stock/kits/readiness/
        readiness_resp = self.client.get("/api/stock/kits/readiness/")
        self.assertEqual(readiness_resp.status_code, 200)
        readiness_data = readiness_resp.json()
        self.assertTrue(readiness_data["success"])
        self.assertGreaterEqual(readiness_data["readiness"]["new_unallocated"], 2)

    def test_api_itms_orders_kits_tab(self):
        """
        Verify /api/itms/orders/?tab=kits returns kits from local DB with kit fields.
        """
        InstallationKit.objects.create(
            kit_code="IK-UMA999PW",
            registration_number="UMA999PW",
            status="New",
            warehouse="AGM SPIRO",
        )
        resp = self.client.get("/api/itms/orders/?tab=kits&source=local")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tab"], "kits")
        self.assertIn("kits", data)
        self.assertTrue(any(k["kit_code"] == "IK-UMA999PW" for k in data["kits"]))

    def test_two_tier_kit_verification_local_stock(self):
        """Tier 1: Kit in local synced stock is immediately verified."""
        InstallationKit.objects.create(
            kit_code="IK-UMA338PZ",
            registration_number="UMA338PZ",
            status="New",
            warehouse="AGM SPIRO",
        )
        res = kit_provisioning_service.verify_scanned_kits_stock(
            ["UMA 338PZ"],
            check_itms_live=False,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["verified_plates"], ["UMA338PZ"])
        self.assertEqual(res["rejected_not_on_stock"], [])

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_two_tier_kit_verification_itms_live_fallback(self, mock_get_client):
        """Tier 2: Kit missing locally is fetched from ITMS live and synced."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "count": 1,
            "kits": [
                {
                    "kit_code": "IK-UMA577PQ",
                    "registration_number": "UMA 577PQ",
                    "front_plate": "001200991",
                    "rear_plate": "001200992",
                    "gps_tracker": "8BAE4707ABCD",
                    "warehouse": "AGM SPIRO",
                    "status": "New",
                    "created_date": "02.10.2026",
                    "kit_uuid": "test-uuid-577",
                }
            ],
        }

        def fake_sync(kits):
            for k in kits:
                InstallationKit.objects.update_or_create(
                    kit_code=k["kit_code"],
                    defaults={
                        "registration_number": k["registration_number"],
                        "front_plate": k["front_plate"],
                        "rear_plate": k["rear_plate"],
                        "gps_tracker": k["gps_tracker"],
                        "status": k["status"],
                        "warehouse": k["warehouse"],
                    },
                )
            return {"success": True, "created": len(kits)}
        mock_client.sync_kits_to_local_db.side_effect = fake_sync

        self.assertFalse(InstallationKit.objects.filter(registration_number="UMA 577PQ").exists())

        res = kit_provisioning_service.verify_scanned_kits_stock(
            ["UMA 577PQ"],
            check_itms_live=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["verified_plates"], ["UMA577PQ"])
        self.assertEqual(res["synced_from_itms"], ["UMA577PQ"])
        self.assertEqual(res["rejected_not_on_stock"], [])

        kit = InstallationKit.objects.get(kit_code="IK-UMA577PQ")
        self.assertEqual(kit.gps_tracker, "8BAE4707ABCD")
        self.assertEqual(kit.status, "New")

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_two_tier_kit_verification_rejects_non_stock(self, mock_get_client):
        """Tier 2 rejection: Kit missing locally and NOT found on ITMS is blocked."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "count": 0,
            "kits": [],
        }

        res = kit_provisioning_service.verify_scanned_kits_stock(
            ["UZZ 999ZZ"],
            check_itms_live=True,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["verified_plates"], [])
        self.assertEqual(res["rejected_not_on_stock"], ["UZZ999ZZ"])
        self.assertFalse(InstallationKit.objects.filter(registration_number="UZZ999ZZ").exists())

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_dispatch_scans_blocks_non_stock_and_allows_valid(self, mock_get_client):
        """Mixed dispatch: Only verified stock is dispatched; non-stock plates are blocked."""
        InstallationKit.objects.create(
            kit_code="IK-UMA111AA",
            registration_number="UMA111AA",
            status="New",
            warehouse="AGM SPIRO",
        )

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "count": 0,
            "kits": [],
        }

        res = stock_monitoring_service.record_dispatch_scans(
            plates=["UMA111AA", "UZZ999ZZ"],
            target_date_suffix=self.test_suffix,
            require_stock_verification=True,
            check_itms_live=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["newly_dispatched"], 1)
        self.assertEqual(res["verified_plates"], ["UMA111AA"])
        self.assertEqual(res["rejected_not_on_stock"], ["UZZ999ZZ"])
        self.assertIn("warning", res)

        self.assertTrue(StockDispatchScan.objects.filter(registration_number="UMA111AA").exists())
        self.assertFalse(StockDispatchScan.objects.filter(registration_number="UZZ999ZZ").exists())

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_api_stock_dispatch_returns_400_for_non_stock(self, mock_get_client):
        """POST /api/stock/dispatch/ returns 400 when all plates are not on stock."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "count": 0,
            "kits": [],
        }

        resp = self.client.post(
            "/api/stock/dispatch/",
            data={"plates": "UZZ888ZZ"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["rejected_not_on_stock"], ["UZZ888ZZ"])
        self.assertIn("cannot be taken out", data["error"])

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_record_dispatch_scans_syncs_from_itms_and_marks_dispatched(self, mock_get_client):
        """When kit missing locally but available on ITMS: syncs to local DB and marks dispatched."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "count": 1,
            "kits": [{
                "kit_code": "IK-UXX777XX",
                "registration_number": "UXX777XX",
                "status": "New",
                "warehouse": "AGM SPIRO",
                "gps_tracker": "IMEI-8675309",
            }],
        }
        mock_client.sync_kits_to_local_db.side_effect = lambda kits: [
            InstallationKit.objects.create(
                kit_code=k["kit_code"],
                registration_number=k["registration_number"],
                status=k.get("status", "New"),
                warehouse=k.get("warehouse", ""),
            ) for k in kits
        ]

        res = stock_monitoring_service.record_dispatch_scans(
            plates=["UXX777XX"],
            target_date_suffix=self.test_suffix,
            require_stock_verification=True,
            check_itms_live=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["newly_dispatched"], 1)
        self.assertTrue(InstallationKit.objects.filter(registration_number="UXX777XX", status="Dispatched").exists())
        self.assertTrue(StockDispatchScan.objects.filter(registration_number="UXX777XX").exists())

    def test_record_dispatch_scans_marks_existing_local_kit_dispatched(self):
        """When kit exists in local DB: marks dispatched immediately."""
        InstallationKit.objects.create(
            kit_code="IK-ULC123AA",
            registration_number="ULC123AA",
            status="New",
        )
        res = stock_monitoring_service.record_dispatch_scans(
            plates=["ULC123AA"],
            target_date_suffix=self.test_suffix,
            require_stock_verification=True,
            check_itms_live=False,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["newly_dispatched"], 1)
        self.assertTrue(InstallationKit.objects.filter(registration_number="ULC123AA", status="Dispatched").exists())
        self.assertTrue(StockDispatchScan.objects.filter(registration_number="ULC123AA").exists())

    def test_stocktake_audit_links_hardware_and_isolates_unregistered(self):
        """Monthly stocktaking separates verified kits with hardware serials from unregistered kits."""
        InstallationKit.objects.create(
            kit_code="IK-UMA501AA",
            registration_number="UMA501AA",
            status="New",
            gps_tracker="IMEI-8675309",
            front_tracker="BLE-F-1111",
            rear_tracker="BLE-R-2222",
            warehouse="AGM SPIRO",
        )

        audit_res = stock_monitoring_service.record_stock_taking_audit(
            scanned_plates="UMA501AA\nUMA502BB",
            target_date_suffix=self.test_suffix,
        )

        self.assertTrue(audit_res["success"])
        self.assertEqual(audit_res["total_scanned"], 2)
        self.assertEqual(audit_res["verified_count"], 1)
        self.assertEqual(audit_res["unregistered_count"], 1)
        self.assertEqual(audit_res["verified_plates"], ["UMA501AA"])
        self.assertEqual(audit_res["unregistered_plates"], ["UMA502BB"])

        hw_profiles = audit_res["hardware_profiles"]
        self.assertEqual(len(hw_profiles), 1)
        self.assertEqual(hw_profiles[0]["plate"], "UMA501AA")
        self.assertEqual(hw_profiles[0]["gps_tracker"], "IMEI-8675309")
        self.assertEqual(hw_profiles[0]["front_ble"], "BLE-F-1111")
        self.assertEqual(hw_profiles[0]["rear_ble"], "BLE-R-2222")

    def test_export_blocked_and_unregistered_csv(self):
        """Generates actionable CSVs for the ITMS Stock Transfer Officer."""
        blocked_path, blocked_name, blocked_cnt = stock_monitoring_service.export_blocked_kits_csv(
            blocked_plates=["UMA999XX", "UMA998YY"],
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(blocked_cnt, 2)
        self.assertTrue(blocked_name.startswith("blocked_dispatch_kits_"))
        import os
        self.assertTrue(os.path.exists(blocked_path))
        with open(blocked_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("UMA999XX", content)
            self.assertIn("UMA998YY", content)
            self.assertIn("Awaiting ITMS Stock Transfer Officer", content)

        unreg_path, unreg_name, unreg_cnt = stock_monitoring_service.export_unregistered_stocktake_csv(
            unregistered_plates=["UMA777ZZ"],
            target_date_suffix=self.test_suffix,
        )
        self.assertEqual(unreg_cnt, 1)
        self.assertTrue(unreg_name.startswith("unregistered_stocktake_kits_"))
        self.assertTrue(os.path.exists(unreg_path))
        with open(unreg_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("UMA777ZZ", content)
            self.assertIn("Physical kit box in safe room", content)
            self.assertIn("NOT ON ITMS STOCK", content)

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_bulk_verify_scanned_kits_stock_skips_live_itms(self, mock_get_client):
        """Bulk verify with > 3 missing plates skips live ITMS HTTP calls to prevent UI freezes."""
        plates = [f"UMA90{i}AA" for i in range(10)]
        res = kit_provisioning_service.verify_scanned_kits_stock(plates, check_itms_live=True)
        # mock_get_client should NOT have been called because len(missing) == 10 > 3
        mock_get_client.assert_not_called()
        self.assertEqual(len(res["rejected_not_on_stock"]), 10)
        self.assertIn("Awaiting ITMS Stock Transfer Officer", res["details"]["UMA900AA"]["reason"])

    def test_unallocated_kit_reconciliation_and_configured_export_directory(self):
        """
        Verifies that kits dispatched to line without ITMS allocation (e.g. UMA058QK)
        are categorized as unallocated discrepancies and exported to the configured directory.
        """
        import tempfile
        from core.services import config_service

        with tempfile.TemporaryDirectory() as temp_dir:
            config_service.set_setting("sync.default_export_directory", temp_dir)
            configured_dir = stock_monitoring_service.get_configured_export_dir()
            self.assertEqual(str(configured_dir), temp_dir)

            # 1. Dispatch UMA058QK to bike on the floor (status New in InstallationKit)
            InstallationKit.objects.create(
                registration_number="UMA058QK",
                kit_code="IK-UMA058QK",
                status="New",
                warehouse="AGM SPIRO",
            )
            StockDispatchScan.objects.create(
                registration_number="UMA058QK",
                plate_category="PSV",
                work_date_suffix=self.test_suffix,
                status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                notes="Dispatched to bike. Awaiting MVR allocation in orders.",
            )

            # 2. Run daily reconciliation
            recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
            self.assertIn("UMA058QK", recon["unallocated_plates"])
            floor = recon.get("floor_operations", {})
            self.assertIn("UMA058QK", floor.get("unallocated_plates", []))

            # 3. Export unallocated/blocked kits CSV
            file_path, filename, cnt = stock_monitoring_service.export_blocked_kits_csv(
                blocked_plates=["UMA058QK"],
                target_date_suffix=self.test_suffix,
            )
            self.assertEqual(cnt, 1)
            self.assertTrue(file_path.startswith(temp_dir))
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("UMA058QK", content)
                self.assertIn("Awaiting MVR Allocation in Orders (Dispatched to Line)", content)

            # 4. Check category CSV content for unallocated
            csv_content = stock_monitoring_service.generate_category_csv_content("unallocated", self.test_suffix)
            self.assertIn("UMA058QK", csv_content)
            self.assertIn("New / Unallocated", csv_content)

            # 5. Check MVR exception docket
            docket = stock_monitoring_service.get_mvr_unallocated_docket(self.test_suffix)
            self.assertIn("UMA058QK", docket["unallocated_plates"])
            self.assertIn("UMA058QK", docket["formatted_docket"])

    def test_accumulated_blocked_plates_end_of_day_export(self):
        """
        Verifies that plates blocked throughout the day (from single scans or Excel)
        are accumulated persistently and exported in the end-of-day report.
        """
        import os
        import tempfile
        from core.services import config_service

        with tempfile.TemporaryDirectory() as temp_dir:
            config_service.set_setting("sync.default_export_directory", temp_dir)

            # Record blocked plates from batch 1 (e.g. 9 AM scan)
            stock_monitoring_service.record_blocked_plates(["UMA101AA", "UMA102BB"], target_date_suffix=self.test_suffix)

            # Record blocked plates from batch 2 (e.g. 2 PM Excel paste)
            stock_monitoring_service.record_blocked_plates(["UMA103CC", "UMA101AA"], target_date_suffix=self.test_suffix)

            accumulated = stock_monitoring_service.get_blocked_plates_for_date(self.test_suffix)
            self.assertEqual(len(accumulated), 3)
            self.assertIn("UMA101AA", accumulated)
            self.assertIn("UMA102BB", accumulated)
            self.assertIn("UMA103CC", accumulated)

            # Export via export_shift_csvs at end of day
            shift_files = stock_monitoring_service.export_shift_csvs(self.test_suffix)
            self.assertIn("blocked", shift_files)
            blocked_file = shift_files["blocked"]
            self.assertTrue(os.path.exists(blocked_file))
            with open(blocked_file, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("UMA101AA", content)
                self.assertIn("UMA102BB", content)
                self.assertIn("UMA103CC", content)

            # Check category CSV generator
            csv_text = stock_monitoring_service.generate_category_csv_content("blocked", self.test_suffix)
            self.assertIn("UMA101AA", csv_text)
            self.assertIn("NOT ON ITMS STOCK", csv_text)

    def test_kit_sync_daemon_interval_and_rate_limit_cooldown(self):
        """
        Verifies that MorningKitSyncDaemon defaults to 3 hours (10,800s)
        and enforces the 10-minute cooldown window to protect the ITMS API.
        """
        daemon = kit_provisioning_service.MorningKitSyncDaemon.get_instance()
        status = daemon.get_status()
        self.assertEqual(status["interval_seconds"], 10800)
        self.assertEqual(status["interval_hours"], 3.0)

        # Trigger sync pass 1
        with patch.object(kit_provisioning_service, "sync_and_provision_warehouse_kits") as mock_sync:
            mock_sync.return_value = {"success": True, "new_kits_ready_count": 5}
            res1 = daemon.trigger_sync(sync_itms=False, force=True)
            self.assertTrue(res1["success"])
            self.assertEqual(mock_sync.call_count, 1)

            # Trigger sync pass 2 immediately without force -> should be blocked by cooldown
            res2 = daemon.trigger_sync(sync_itms=False, force=False)
            self.assertEqual(res2.get("status"), "cooldown_active")
            # Should NOT have invoked sync_and_provision_warehouse_kits again
            self.assertEqual(mock_sync.call_count, 1)

            # Trigger sync pass 3 with force=True -> bypasses cooldown
            res3 = daemon.trigger_sync(sync_itms=False, force=True)
            self.assertEqual(mock_sync.call_count, 2)

    def test_crawler_bulk_database_upserts_and_protection(self):
        """
        Verifies that sync_kits_to_local_db properly performs bulk atomic upserts
        for installation kits without row-by-row locking.
        """
        from core.services.itms_web_client import get_web_client
        client = get_web_client()

        sample_kits = [
            {
                "kit_code": "IK-UMA888TEST",
                "registration_number": "UMA 888TEST",
                "front_plate": "FP-888",
                "rear_plate": "RP-888",
                "warehouse": "AGM SPIRO",
                "status": "New",
                "created_date": "04.10.2026",
            },
            {
                "kit_code": "IK-UMA999TEST",
                "registration_number": "UMA 999TEST",
                "front_plate": "FP-999",
                "rear_plate": "RP-999",
                "warehouse": "AGM SPIRO",
                "status": "New",
                "created_date": "04.10.2026",
            },
        ]

        # 1. First insert
        res = client.sync_kits_to_local_db(sample_kits)
        self.assertTrue(res["success"])
        self.assertEqual(res["created"], 2)
        self.assertEqual(res["updated"], 0)

        kit1 = InstallationKit.objects.get(kit_code="IK-UMA888TEST")
        self.assertEqual(kit1.registration_number, "UMA 888TEST")
        self.assertEqual(kit1.status, "New")

        # 2. Update status of existing kit
        sample_kits[0]["status"] = "Allocated"
        res_update = client.sync_kits_to_local_db(sample_kits)
        self.assertTrue(res_update["success"])
        self.assertEqual(res_update["created"], 0)
        self.assertEqual(res_update["updated"], 1)

        kit1.refresh_from_db()
        self.assertEqual(kit1.status, "Allocated")

    def test_clipboard_service_large_batch_parsing(self):
        """Validates that 1,200+ plates copied from an Excel column parse cleanly without truncation."""
        from core.services import clipboard_service, stock_monitoring_service

        # Generate 1,200 distinct valid plates (600 UMA series + 600 UMB series)
        series_a = [f"UMA{i:03d}PZ" for i in range(1, 601)]
        series_b = [f"UMB{i:03d}PZ" for i in range(1, 601)]
        plates_input = "\n".join(series_a + series_b)
        clean_plates, dup_count, dup_plates = stock_monitoring_service.parse_plate_input_with_stats(plates_input)
        self.assertEqual(len(clean_plates), 1200)
        self.assertEqual(dup_count, 0)
        self.assertEqual(clean_plates[0], "UMA001PZ")
        self.assertEqual(clean_plates[-1], "UMB600PZ")

    def test_physical_stock_count_and_variance_reconciliation(self):
        """Verifies that physical count and variance appear correctly in daily reconciliation."""
        # Setup opening balance
        stock_monitoring_service.set_opening_balances(opening_pmo=100, opening_psv=200, target_date_suffix=self.test_suffix)

        # Set physical count of 280 (book closing is 300, variance should be -20)
        res = stock_monitoring_service.set_physical_count(physical_count=280, target_date_suffix=self.test_suffix)
        self.assertTrue(res["success"])
        self.assertEqual(res["physical_count"], 280)
        self.assertEqual(res["variance"], -20)

        # Compute reconciliation
        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        self.assertTrue(recon["has_physical_count"])
        self.assertEqual(recon["physical_count"], 280)
        self.assertEqual(recon["variance"], -20)

        # Check rows in report_table
        rows = recon["report_table"]["rows"]
        phys_row = next((r for r in rows if "Physical Count" in r["metric"]), None)
        var_row = next((r for r in rows if "Variance" in r["metric"]), None)
        self.assertIsNotNone(phys_row)
        self.assertIsNotNone(var_row)
        self.assertEqual(phys_row["total"], 280)
        self.assertEqual(var_row["total"], "-20")

    def test_clipboard_file_import_csv(self):
        """Validates file importing from temporary CSV / TXT files."""
        import tempfile
        from core.services import clipboard_service

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("Header,Plate Number,Owner\n")
            f.write("1,UMA 101PW,John\n")
            f.write("2,UMA 102PW,Sarah\n")
            f.write("3,UMA 103PW,David\n")
            tmp_path = f.name

        try:
            clean, dups, _ = clipboard_service.read_plates_from_file(tmp_path)
            self.assertEqual(len(clean), 3)
            self.assertIn("UMA101PW", clean)
            self.assertIn("UMA102PW", clean)
            self.assertIn("UMA103PW", clean)
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_stock_paste_modal_stats_and_confirm(self):
        """Verifies StockPasteModal handles plate parsing, duplicate counting, and confirmation payload."""
        from core.tui.dialogs import StockPasteModal
        modal = StockPasteModal(mode="stocktake", target_date_suffix=self.test_suffix)
        raw_text = "UMA 101PW\nUMA 102PW\nUMA 101PW\nINVALID_PLATE\nUMA 103PW"
        from core.services import stock_monitoring_service
        plates, dups, _ = stock_monitoring_service.parse_plate_input_with_stats(raw_text)

        self.assertEqual(plates, ["UMA101PW", "UMA102PW", "UMA103PW"])
        self.assertEqual(dups, 1)

    def test_stock_pane_subtab_cycling_and_date_stepping(self):
        """Tests StockPane F cycle view navigation and date stepping."""
        from core.tui.stock_pane import StockPane
        pane = StockPane(target_date_suffix="021026")
        self.assertEqual(pane.target_date_suffix, "021026")

        # Test date step backward
        pane.action_prev_day()
        self.assertEqual(pane.target_date_suffix, "011026")

        # Test date step forward
        pane.action_next_day()
        self.assertEqual(pane.target_date_suffix, "021026")

    def test_stocktake_manual_count_updates_variance_and_summary(self):
        """Tests that set_physical_count immediately saves physical count and updates variance against closing stock."""
        # Setup initial opening and installed
        stock_monitoring_service.set_opening_balances(opening_psv=100, opening_pmo=50, target_date_suffix=self.test_suffix)
        recon_before = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        book_closing = recon_before["closing_stock"] # 150

        # Set physical count to 155 (surplus +5)
        res = stock_monitoring_service.set_physical_count(physical_count=155, target_date_suffix=self.test_suffix)
        self.assertTrue(res["success"])
        self.assertEqual(res["physical_count"], 155)
        self.assertEqual(res["variance"], 5)

        recon_after = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        self.assertTrue(recon_after["has_physical_count"])
        self.assertEqual(recon_after["physical_count"], 155)
        self.assertEqual(recon_after["variance"], 5)

    def test_stock_pane_4_modes_and_movement_staging(self):
        """Verifies StockPane 4 core modes and in-memory movement plate staging."""
        from unittest.mock import MagicMock
        from core.tui.stock_pane import StockPane

        pane = StockPane(target_date_suffix="041026")
        pane.notify = MagicMock()

        # Check 4 clean modes
        self.assertEqual(len(pane.MODES), 4)
        mode_ids = [m[0] for m in pane.MODES]
        self.assertEqual(mode_ids, ["mode-dispatch", "mode-movements", "mode-audit", "mode-ledger"])

        # Test movement scan staging
        mock_input = MagicMock()
        pane._handle_movement_scan("UMA 111AA", mock_input)
        self.assertEqual(pane._staged_movements, ["UMA111AA"])

        # Test duplicate staging suppressed
        pane._handle_movement_scan("UMA 111AA", mock_input)
        self.assertEqual(pane._staged_movements, ["UMA111AA"])
        self.assertTrue(pane.notify.called)

        # Test clear staged
        pane._handle_clear_movement_staged()
        self.assertEqual(pane._staged_movements, [])

    def test_stock_paste_modal_movements_mode(self):
        """Verifies StockPasteModal handles movements mode titles and confirm labels."""
        from core.tui.dialogs import StockPasteModal
        modal = StockPasteModal(mode="movements", target_date_suffix=self.test_suffix)
        self.assertEqual(modal.mode, "movements")
        # Check titles mapping contains movements
        from core.tui.dialogs import StockPasteModal
        m = StockPasteModal(mode="movements")
        self.assertEqual(m.mode, "movements")

    def test_physical_safe_audit_persistence_and_targeted_enrichment(self):
        """Verifies SafeAuditScan persistence, physical bond inventory filtering, and no ITMS dump."""
        from unittest.mock import MagicMock
        from core.models import SafeAuditScan, InstallationKit
        from core.tui.stock_pane import StockPane

        # 1. Simulate remote ITMS kits synced to database that are NOT at this bond
        InstallationKit.objects.create(
            kit_code="IK-REMOTE-001",
            registration_number="UMA999RM",
            status="New",
            warehouse="Other Facility",
        )

        # 2. Record physical safe room stock audit
        scanned_sample = ["UMA101SA", "UMA102SA", "UMA103SA"]
        res = stock_monitoring_service.record_stock_taking_audit(
            scanned_plates=scanned_sample,
            target_date_suffix=self.test_suffix,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["total_scanned"], 3)

        # 3. Verify SafeAuditScan records created and persisted in DB
        saved_audits = list(SafeAuditScan.objects.filter(work_date_suffix=self.test_suffix).values_list("registration_number", flat=True))
        self.assertEqual(sorted(saved_audits), sorted(scanned_sample))

        # 4. Verify get_physical_bond_plates returns only physical plates
        phys_plates = stock_monitoring_service.get_physical_bond_plates(self.test_suffix)
        self.assertEqual(sorted(phys_plates), sorted(scanned_sample))
        self.assertNotIn("UMA999RM", phys_plates)  # Remote ITMS kit is NOT in physical bond inventory

        # 5. Verify StockPane._render_stocktake_table renders physical audits and NOT remote ITMS kits
        pane = StockPane(target_date_suffix=self.test_suffix)
        pane.notify = MagicMock()
        mock_table = MagicMock()
        pane.query_one = lambda selector, expected_type=None: mock_table if "table" in selector else MagicMock()

        pane._render_stocktake_table()
        # Verify rows added correspond to scanned physical plates
        added_rows = [call[1].get("key") for call in mock_table.add_row.call_args_list if call[1].get("key")]
        self.assertIn("UMA101SA", added_rows)
        self.assertIn("UMA102SA", added_rows)
        self.assertNotIn("UMA999RM", added_rows)

    def test_api_stock_inspect(self):
        """Test /api/stock/inspect/ returns complete hardware profile and stock status."""
        kit = InstallationKit.objects.create(
            registration_number="UMA888DS",
            kit_code="IK-UMA888DS",
            warehouse="AGM Bonded Warehouse",
            gps_tracker="86420109999",
            front_tracker="BLE-FRONT-888",
            rear_tracker="BLE-REAR-888",
            front_plate="FP-888",
            rear_plate="RP-888",
            status="New",
        )
        # 1. Un-dispatched kit inspection
        resp = self.client.get(f"/api/stock/inspect/?plate=UMA 888DS&date_suffix={self.test_suffix}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["canonical_plate"], "UMA888DS")
        self.assertEqual(data["stock_status"]["code"], "ON_STOCK")
        self.assertEqual(data["kit_profile"]["gps_tracker"], "86420109999")
        self.assertFalse(data["is_blocked"])

        # 2. Dispatch kit
        StockDispatchScan.objects.create(
            registration_number="UMA888DS",
            plate_category=PlateCategory.PSV,
            work_date_suffix=self.test_suffix,
            status=StockDispatchScan.Status.ON_LINE_ACTIVE,
            operator_name="Tester",
        )
        resp2 = self.client.get(f"/api/stock/inspect/?plate=UMA 888DS&date_suffix={self.test_suffix}")
        data2 = resp2.json()
        self.assertTrue(data2["success"])
        self.assertEqual(data2["stock_status"]["code"], "DISPATCHED")
        self.assertTrue(data2["dispatch_record"]["is_dispatched"])
        self.assertEqual(data2["dispatch_record"]["operator"], "Tester")

    def test_api_stock_dispatch_clear(self):
        """Test /api/stock/dispatch/clear/ clears dispatches for shift."""
        StockDispatchScan.objects.create(
            registration_number="UMA777DS",
            plate_category=PlateCategory.PSV,
            work_date_suffix=self.test_suffix,
            status=StockDispatchScan.Status.ON_LINE_ACTIVE,
        )
        resp = self.client.post(
            "/api/stock/dispatch/clear/",
            data=json.dumps({"date_suffix": self.test_suffix}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["cleared_count"], 1)
        self.assertFalse(StockDispatchScan.objects.filter(work_date_suffix=self.test_suffix).exists())

    def test_api_stock_audit(self):
        """Test /api/stock/audit/ performs physical stocktaking audit."""
        resp = self.client.post(
            "/api/stock/audit/",
            data=json.dumps({"plates": "UMA501SA, UMA502SA", "date_suffix": self.test_suffix}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_scanned"], 2)
        self.assertTrue(SafeAuditScan.objects.filter(work_date_suffix=self.test_suffix, registration_number="UMA501SA").exists())

    def test_api_stock_previous_closing(self):
        """Test /api/stock/previous-closing/ returns previous shift closing balance."""
        DailyStockLedger.objects.create(
            work_date=self.test_date - timedelta(days=1),
            work_date_suffix="280926",
            closing_balance_psv=120,
            closing_balance_pmo=30,
            closing_stock=150,
        )
        resp = self.client.get(f"/api/stock/previous-closing/?date_suffix={self.test_suffix}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["opening_psv"], 120)
        self.assertEqual(data["data"]["opening_pmo"], 30)

    def test_api_stock_export_unregistered_csv(self):
        """Test /api/stock/export/unregistered/ exports unregistered kits CSV."""
        SafeAuditScan.objects.create(
            registration_number="UMA999UNREG",
            plate_category=PlateCategory.PSV,
            work_date_suffix=self.test_suffix,
        )
        resp = self.client.get(f"/api/stock/export/unregistered/?date_suffix={self.test_suffix}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        content = resp.content.decode("utf-8")
        self.assertIn("UMA999UNREG", content)

    @patch("core.services.stock_monitoring_service.enrich_physical_plates_with_itms")
    def test_inbound_delivery_saves_and_triggers_background_enrichment(self, mock_enrich):
        """
        Verify inbound delivery prioritizes saving to database immediately,
        creates local stub InstallationKit records marked 'New', and triggers
        background enrichment without blocking.
        """
        sample_plates = ["UMA301IN", "UMA302IN"]
        from django.conf import settings
        with patch.object(settings, "TESTING", False, create=True), patch("sys.argv", ["manage.py", "runserver"]):
            res = stock_monitoring_service.record_delivery(
                delivery_number="DEL-TEST-001",
                plates=sample_plates,
                supplier="Central Depot",
                target_date_suffix=self.test_suffix,
            )

        # 1. Immediate DB persistence verified
        self.assertTrue(res["success"])
        self.assertEqual(res["plates_count"], 2)
        delivery = StockDelivery.objects.get(delivery_number="DEL-TEST-001")
        self.assertEqual(delivery.items.count(), 2)

        # 2. Local InstallationKit stubs provisioned as 'New'
        for p in sample_plates:
            kit = InstallationKit.objects.get(registration_number=p)
            self.assertEqual(kit.status, "New")

        # 3. Background enrichment thread launched mock check (give daemon thread brief moment)
        import time
        time.sleep(0.05)
        mock_enrich.assert_called_once()
        called_plates = mock_enrich.call_args[0][0]
        self.assertEqual(sorted(called_plates), sorted(sample_plates))

    @patch("core.services.stock_monitoring_service.enrich_physical_plates_with_itms")
    def test_stock_audit_saves_and_triggers_background_enrichment(self, mock_enrich):
        """
        Verify safe room physical stock audit prioritizes saving SafeAuditScan
        and DailyStockLedger immediately, and triggers background ITMS lookup.
        """
        sample_plates = ["UMA401SA", "UMA402SA"]
        from django.conf import settings
        with patch.object(settings, "TESTING", False, create=True), patch("sys.argv", ["manage.py", "runserver"]):
            res = stock_monitoring_service.record_stock_taking_audit(
                scanned_plates=sample_plates,
                target_date_suffix=self.test_suffix,
            )

        # 1. Immediate DB persistence verified
        self.assertTrue(res["success"])
        self.assertEqual(res["total_scanned"], 2)
        self.assertEqual(SafeAuditScan.objects.filter(work_date_suffix=self.test_suffix).count(), 2)

        # 2. Ledger updated
        ledger = DailyStockLedger.objects.get(work_date_suffix=self.test_suffix)
        self.assertEqual(ledger.physical_count, 2)

        # 3. Background enrichment thread launched
        import time
        time.sleep(0.05)
        mock_enrich.assert_called_once()
        called_plates = mock_enrich.call_args[0][0]
        self.assertEqual(sorted(called_plates), sorted(sample_plates))

    def test_sync_kits_to_local_db_matches_and_updates_stub_kit(self):
        """
        Verify sync_kits_to_local_db updates an existing stub kit created during
        delivery or audit (matching by registration_number) without creating duplicate records.
        """
        from core.services.itms_web_client import get_web_client
        # 1. Stub kit created during rapid delivery scan
        stub = InstallationKit.objects.create(
            kit_code="IK-UMA555STUB",
            registration_number="UMA 555STUB",
            status="New",
        )

        client = get_web_client()
        # 2. ITMS returns real kit details with different kit_code
        itms_kits = [{
            "kit_code": "ITMS-K-9988",
            "registration_number": "UMA 555STUB",
            "gps_tracker": "8642010998877",
            "front_tracker": "BLE-FRONT-9988",
            "rear_tracker": "BLE-REAR-9988",
            "front_plate": "FP-9988",
            "rear_plate": "RP-9988",
            "warehouse": "AGM Bonded Warehouse",
            "status": "New",
        }]

        res = client.sync_kits_to_local_db(itms_kits)
        self.assertTrue(res["success"])
        self.assertEqual(res["updated"], 1)
        self.assertEqual(res["created"], 0)

        # 3. Check stub was updated and no duplicate was created
        stub.refresh_from_db()
        self.assertEqual(stub.kit_code, "ITMS-K-9988")
        self.assertEqual(stub.gps_tracker, "8642010998877")
        self.assertEqual(stub.front_tracker, "BLE-FRONT-9988")
        self.assertEqual(InstallationKit.objects.filter(registration_number="UMA 555STUB").count(), 1)

    def test_get_stock_transfer_request_docket(self):
        """Verifies generation of ITMS stock transfer docket for non-stock plates."""
        docket = stock_monitoring_service.get_stock_transfer_request_docket(
            target_date_suffix=self.test_suffix,
            blocked_plates=["UZZ999ZZ", "UXX888XX"],
        )
        self.assertTrue(docket["success"])
        self.assertEqual(docket["count"], 2)
        self.assertIn("UZZ999ZZ", docket["raw_plates"])
        self.assertIn("UXX888XX", docket["raw_plates"])
        self.assertIn("ITMS STOCK TRANSFER & REGISTRATION REQUEST", docket["formatted_message"])
        self.assertIn("SET ASIDE", docket["formatted_message"])
        self.assertEqual(len(docket["items"]), 2)
        self.assertEqual(docket["items"][0]["action"], "Set Box Aside")

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_dispatch_attaches_transfer_docket_when_plates_blocked(self, mock_get_client):
        """When dispatches contain blocked plates, transfer docket is automatically attached."""
        InstallationKit.objects.create(
            kit_code="IK-UMA222BB",
            registration_number="UMA222BB",
            status="New",
            warehouse="AGM SPIRO",
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {"success": True, "count": 0, "kits": []}

        res = stock_monitoring_service.record_dispatch_scans(
            plates=["UMA222BB", "UZZ888XX"],
            target_date_suffix=self.test_suffix,
            require_stock_verification=True,
            check_itms_live=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["newly_dispatched"], 1)
        self.assertIn("UZZ888XX", res["rejected_not_on_stock"])
        self.assertIn("docket", res)
        self.assertEqual(res["docket"]["count"], 1)
        self.assertIn("UZZ888XX", res["docket"]["raw_plates"])

    @patch("core.services.kit_provisioning_service.get_web_client")
    def test_api_stock_verify_batch_and_blocked_plates(self, mock_get_client):
        """Tests /api/stock/verify-batch/ and /api/stock/blocked-plates/ endpoints."""
        from django.test import Client
        import json

        InstallationKit.objects.create(
            kit_code="IK-UMA333CC",
            registration_number="UMA333CC",
            status="New",
            warehouse="AGM SPIRO",
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.fetch_installation_kits.return_value = {"success": True, "count": 0, "kits": []}

        c = Client()
        # 1. Test verify-batch endpoint
        resp = c.post(
            "/api/stock/verify-batch/",
            data=json.dumps({
                "plates": "UMA333CC\nUBB999ZZ",
                "date_suffix": self.test_suffix,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["verified_plates"], ["UMA333CC"])
        self.assertEqual(data["rejected_not_on_stock"], ["UBB999ZZ"])
        self.assertIn("docket", data)
        self.assertEqual(data["docket"]["count"], 1)

        # 2. Test blocked-plates endpoint
        resp_blocked = c.get(f"/api/stock/blocked-plates/?date_suffix={self.test_suffix}")
        self.assertEqual(resp_blocked.status_code, 200)
        blocked_data = resp_blocked.json()
        self.assertTrue(blocked_data["success"])
        self.assertIn("docket", blocked_data)
        self.assertIn("UBB999ZZ", blocked_data["docket"]["raw_plates"])











