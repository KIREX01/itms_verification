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
