# -*- coding: utf-8 -*-
"""
Unit and integration tests for standardized CSV report generation,
spiked / undispatched kit detection, zero empty-row verification,
and streaming performance on 1,000+ records.
"""
from datetime import date
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from core.models import (
    InstallationOrder,
    StockDispatchScan,
    VehicleInstallationPair,
    EvidenceImage,
    SubmissionAuditLog,
)
from core.services import stock_monitoring_service, report_service, date_service

User = get_user_model()


class ReportExportsAndSpikedKitTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="test_supervisor",
            password="test_password_123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.test_suffix = "181026"
        self.test_date = date(2026, 10, 18)

    def test_spiked_undispatched_kit_detection(self):
        """
        Verify that kits present in ITMS (installed archive or active pending)
        that were never scanned out at the safe room are properly detected as spiked.
        """
        # Order 1: Installed/Archived without StockDispatchScan
        InstallationOrder.objects.create(
            order_number=f"ORD-SP-001-{self.test_suffix}",
            registration_number="UMA901SP",
            order_status="Installed",
            is_archived=True,
            warehouse_name="AGM",
        )

        # Order 2: Active pending queue without StockDispatchScan
        InstallationOrder.objects.create(
            order_number=f"ORD-SP-002-{self.test_suffix}",
            registration_number="UMA902SP",
            order_status="Ready for installation",
            is_archived=False,
            warehouse_name="AGM",
        )

        # Order 3: Normal order with proper StockDispatchScan
        InstallationOrder.objects.create(
            order_number=f"ORD-NORM-003-{self.test_suffix}",
            registration_number="UMA903DS",
            order_status="Installed",
            is_archived=True,
            warehouse_name="AGM",
        )
        StockDispatchScan.objects.create(
            work_date=self.test_date,
            work_date_suffix=self.test_suffix,
            registration_number="UMA903DS",
            status=StockDispatchScan.Status.DISPATCHED,
        )

        # Compute daily reconciliation
        recon = stock_monitoring_service.compute_daily_reconciliation(self.test_suffix)
        self.assertIn("UMA901SP", recon["spiked_installed"])
        self.assertIn("UMA902SP", recon["spiked_pending"])
        self.assertNotIn("UMA903DS", recon["spiked_installed"])
        self.assertEqual(recon["spiked_undispatched_count"], 2)

        # Verify floor operations summary
        floor_ops = recon.get("floor_operations", {})
        self.assertEqual(floor_ops["spiked_undispatched_count"], 2)
        self.assertEqual(floor_ops["spiked_installed_count"], 1)
        self.assertEqual(floor_ops["spiked_pending_count"], 1)
        self.assertEqual(floor_ops["graduated_scale"]["spiked_undispatched"]["count"], 2)

        # Test date-driven totals in report_service
        date_totals = report_service.get_date_driven_plate_totals(self.test_suffix)
        self.assertIn("spiked_undispatched", date_totals)
        self.assertEqual(date_totals["spiked_undispatched"]["total_count"], 2)
        self.assertEqual(date_totals["spiked_undispatched"]["installed_count"], 1)
        self.assertEqual(date_totals["spiked_undispatched"]["pending_count"], 1)

    def test_spiked_csv_export_content_and_headers(self):
        """
        Verify that spiked / undispatched CSV export generates clean, standardized rows.
        """
        InstallationOrder.objects.create(
            order_number=f"ORD-SP-101-{self.test_suffix}",
            registration_number="UMA101SP",
            order_status="Installed",
            is_archived=True,
            warehouse_name="AGM",
        )

        csv_content = stock_monitoring_service.generate_category_csv_content("spiked", self.test_suffix)
        lines = [line.strip() for line in csv_content.splitlines() if line.strip()]

        # Verify header structure
        self.assertGreaterEqual(len(lines), 2)
        header = lines[0]
        self.assertIn("Row", header)
        self.assertIn("Plate Number", header)
        self.assertIn("Order Status", header)
        self.assertIn("Audit Alert", header)

        # Verify record row
        record_line = lines[1]
        self.assertIn("UMA101SP", record_line)
        self.assertIn("MISSING DISPATCH SCAN", record_line)

        # Test endpoint
        resp = self.client.get(f"/api/stock/export/category/spiked/?date={self.test_suffix}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn(b"UMA101SP", resp.content)

    def test_zero_empty_rows_and_crlf_format(self):
        """
        Ensures that exported CSV files do NOT contain duplicate empty rows (


 or blank lines).
        All records must be sequentially contiguous.
        """
        # Create sample data
        InstallationOrder.objects.create(
            order_number=f"ORD-CRLF-1-{self.test_suffix}",
            registration_number="UMA701CR",
            order_status="Installed",
            is_archived=True,
        )
        InstallationOrder.objects.create(
            order_number=f"ORD-CRLF-2-{self.test_suffix}",
            registration_number="UMA702CR",
            order_status="Installed",
            is_archived=True,
        )

        # 1. Test master reconciliation CSV
        recon_csv = stock_monitoring_service.export_stock_reconciliation_csv(self.test_suffix)
        recon_bytes = recon_csv.encode("utf-8-sig")

        # Must not contain duplicate CR
        self.assertNotIn(b"\r\r\n", recon_bytes)
        # Verify no consecutive empty newlines
        self.assertNotIn(b"\n\n", recon_bytes.replace(b"\r", b""))

        # 2. Test shift CSV export files on disk
        exported_paths = stock_monitoring_service.export_shift_csvs(self.test_suffix)
        for p in exported_paths.values():
            file_path = Path(p)
            raw_bytes = file_path.read_bytes()
            self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"), f"{file_path} missing UTF-8 BOM")
            self.assertNotIn(b"\r\r\n", raw_bytes, f"{file_path} contains double-CRLF empty rows")
            self.assertNotIn(b"\n\n", raw_bytes.replace(b"\r", b""), f"{file_path} contains empty lines")

    def test_streaming_export_with_1000_records(self):
        """
        Simulate 1,000 installation pairs and test streaming CSV export without memory blowout.
        """
        # Bulk create 1,000 orders and pairs
        orders_to_create = []
        pairs_to_create = []
        now = timezone.now()

        for i in range(1, 1001):
            plate = f"UMA{i:04d}ST"
            orders_to_create.append(
                InstallationOrder(
                    order_number=f"ORD-{i:04d}-{self.test_suffix}",
                    registration_number=plate,
                    order_status="Installed",
                    is_archived=True,
                    warehouse_name="AGM",
                    vin=f"VIN{i:06d}",
                )
            )

        InstallationOrder.objects.bulk_create(orders_to_create)
        created_orders = list(InstallationOrder.objects.filter(order_number__endswith=self.test_suffix))

        for idx, ord_obj in enumerate(created_orders, 1):
            pairs_to_create.append(
                VehicleInstallationPair(
                    order=ord_obj,
                    registration_number_detected=ord_obj.registration_number,
                    verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
                    match_type="EXACT",
                    match_score=100.0,
                    matched_via="VISION",
                    is_manual_override=False,
                    updated_at=now,
                    submitted_at=now,
                )
            )

        VehicleInstallationPair.objects.bulk_create(pairs_to_create)

        # Call streaming endpoint
        resp = self.client.get(f"/api/export/report/?date={self.test_suffix}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get("Content-Type"), "text/csv; charset=utf-8")

        # Consume streaming response
        stream_content = b"".join(resp.streaming_content)
        self.assertTrue(stream_content.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\r\n", stream_content)

        lines = stream_content.decode("utf-8-sig").splitlines()
        # Header + 1,000 records = 1,001 lines
        self.assertEqual(len(lines), 1001)
        self.assertIn("Row,Pair ID,Detected Plate", lines[0])
        self.assertIn("1000,", lines[-1])

        # Test high-performance system totals aggregation
        totals = report_service.get_system_totals(scope="ALL", target_date=self.test_suffix)
        self.assertEqual(totals["pairs"]["approved"], 1000)
        self.assertIn("spiked_undispatched", totals["date_driven"])
        self.assertIn("scanned_vs_skipped", totals["date_driven"])
        self.assertIn("all_stages", totals["date_driven"])

    def test_order_stages_breakdown_metrics(self):
        """
        Verify that order stages are dynamically calculated across all given stages
        without any cutoff or omissions.
        """
        suffix = "191026"
        # Create orders in various stages
        InstallationOrder.objects.create(
            order_number=f"ORD-STG-1-{suffix}",
            registration_number="UMA111ST",
            order_status="Ready for installation",
            itms_stage="STAGE_1_INSTALLATION",
            is_archived=False,
            warehouse_name="AGM",
        )
        InstallationOrder.objects.create(
            order_number=f"ORD-STG-2-{suffix}",
            registration_number="UMA112ST",
            order_status="Under installation",
            itms_stage="STAGE_2_APPROVE",
            is_archived=False,
            warehouse_name="AGM",
        )
        InstallationOrder.objects.create(
            order_number=f"ORD-STG-3-{suffix}",
            registration_number="UMA113ST",
            order_status="Installed",
            itms_stage="ARCHIVED",
            is_archived=True,
            warehouse_name="AGM",
        )

        date_totals = report_service.get_date_driven_plate_totals(suffix)
        self.assertEqual(date_totals["total_orders_for_date"], 3)
        self.assertEqual(date_totals["installed_archive"]["count"], 1)
        self.assertEqual(date_totals["pending_orders"]["count"], 2)

        # Check pending stages
        pending_stages = date_totals["pending_orders"]["stages"]
        self.assertIn("STAGE_1_INSTALLATION", pending_stages)
        self.assertIn("STAGE_2_APPROVE", pending_stages)
        self.assertEqual(pending_stages["STAGE_1_INSTALLATION"], 1)
        self.assertEqual(pending_stages["STAGE_2_APPROVE"], 1)

        # Check all stages
        all_stages = date_totals["all_stages"]
        self.assertIn("ARCHIVED", all_stages)
        self.assertEqual(all_stages["ARCHIVED"], 1)

    def test_scanned_vs_skipped_discrepancy_detection(self):
        """
        Verify that plates/kits that skipped safe-room scanning (spiked)
        or skipped camera photo evidence are properly categorized and flagged.
        """
        suffix = "201026"
        work_date = date(2026, 10, 20)

        # 1. Properly scanned plate: has dispatch scan AND camera evidence
        order_scanned = InstallationOrder.objects.create(
            order_number=f"ORD-SCAN-1-{suffix}",
            registration_number="UMA201SC",
            order_status="Installed",
            is_archived=True,
            has_front_photo=True,
            has_rear_photo=True,
            warehouse_name="AGM",
        )
        StockDispatchScan.objects.create(
            work_date=work_date,
            work_date_suffix=suffix,
            registration_number="UMA201SC",
            status=StockDispatchScan.Status.DISPATCHED,
        )
        VehicleInstallationPair.objects.create(
            order=order_scanned,
            registration_number_detected="UMA201SC",
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            match_type="EXACT",
            match_score=100.0,
            matched_via="VISION",
        )

        # 2. Skipped Safe-Room Scan (Spiked): in active orders, never scanned at safe-room
        InstallationOrder.objects.create(
            order_number=f"ORD-SKIP-DISP-{suffix}",
            registration_number="UMA202SD",
            order_status="Ready for installation",
            is_archived=False,
            warehouse_name="AGM",
        )

        # 3. Skipped Camera Photos: dispatched from safe-room & installed in ITMS without camera photo evidence
        InstallationOrder.objects.create(
            order_number=f"ORD-SKIP-CAM-{suffix}",
            registration_number="UMA203SC",
            order_status="Installed",
            is_archived=True,
            has_front_photo=False,
            has_rear_photo=False,
            warehouse_name="AGM",
        )
        StockDispatchScan.objects.create(
            work_date=work_date,
            work_date_suffix=suffix,
            registration_number="UMA203SC",
            status=StockDispatchScan.Status.DISPATCHED,
        )

        date_totals = report_service.get_date_driven_plate_totals(suffix)
        scanned_vs_skipped = date_totals["scanned_vs_skipped"]

        self.assertGreaterEqual(scanned_vs_skipped["total_scanned"], 1)
        self.assertGreaterEqual(scanned_vs_skipped["total_skipped"], 2)

        items = scanned_vs_skipped["items"]
        skipped_plates = [it["plate"] for it in items]
        self.assertIn("UMA202SD", skipped_plates)
        self.assertIn("UMA203SC", skipped_plates)

        # Check classifications
        disp_skip_item = next(it for it in items if it["plate"] == "UMA202SD")
        self.assertEqual(disp_skip_item["problem_type"], "SKIPPED_SAFE_ROOM_SCAN")

        cam_skip_item = next(it for it in items if it["plate"] == "UMA203SC")
        self.assertEqual(cam_skip_item["problem_type"], "SKIPPED_CAMERA_PHOTOS")

    def test_scoped_report_csv_exports(self):
        """
        Verify that /api/export/report/ cleanly routes each scoped report type:
        stages, scanned_vs_skipped, unallocated, archived, pending.
        """
        suffix = "211026"
        work_date = date(2026, 10, 21)

        InstallationOrder.objects.create(
            order_number=f"ORD-EXP-1-{suffix}",
            registration_number="UMA301EX",
            order_status="Ready for installation",
            itms_stage="STAGE_1_INSTALLATION",
            is_archived=False,
            warehouse_name="AGM",
        )
        InstallationOrder.objects.create(
            order_number=f"ORD-EXP-2-{suffix}",
            registration_number="UMA302EX",
            order_status="Installed",
            itms_stage="ARCHIVED",
            is_archived=True,
            installation_officer="Officer John",
            installation_date="21.10.2026",
            warehouse_name="AGM",
        )

        # 1. Stages Export
        resp = self.client.get(f"/api/export/report/?type=stages&date={suffix}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        stages_content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("Order Number,Plate Number,VIN,Order Status,ITMS Stage", stages_content)
        self.assertIn("UMA301EX", stages_content)
        self.assertIn("STAGE_1_INSTALLATION", stages_content)

        # 2. Scanned vs Skipped Export
        resp = self.client.get(f"/api/export/report/?type=scanned_vs_skipped&date={suffix}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        skipped_content = b"".join(resp.streaming_content).decode("utf-8-sig")
        self.assertIn("Discrepancy Classification", skipped_content)

        # 3. Archived Orders Export
        resp = self.client.get(f"/api/export/report/?type=archived&date={suffix}")
        self.assertEqual(resp.status_code, 200)
        archived_content = resp.content.decode("utf-8-sig")
        self.assertIn("UMA302EX", archived_content)

        # 4. Pending Orders Export
        resp = self.client.get(f"/api/export/report/?type=pending&date={suffix}")
        self.assertEqual(resp.status_code, 200)
        pending_content = resp.content.decode("utf-8-sig")
        self.assertIn("UMA301EX", pending_content)
