import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.matcher import association
from core.models import EvidenceImage, IngestionBatch, VehicleInstallationPair
from core.services import file_dialog, vault_service


class BatchFolderScanTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="itms_test_batch_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_empty_folder(self):
        res = file_dialog.scan_batch_folder(self.temp_dir)
        self.assertEqual(res["status"], "EMPTY")
        self.assertFalse(res["is_symmetric"])
        self.assertEqual(res["front_count"], 0)
        self.assertEqual(res["rear_count"], 0)

    def test_scan_symmetric_batch_folder(self):
        front_dir = Path(self.temp_dir) / "front"
        rear_dir = Path(self.temp_dir) / "rear"
        front_dir.mkdir()
        rear_dir.mkdir()

        (front_dir / "01.jpg").write_bytes(b"dummy1")
        (front_dir / "02.jpg").write_bytes(b"dummy2")
        (rear_dir / "01.jpg").write_bytes(b"dummy3")
        (rear_dir / "02.jpg").write_bytes(b"dummy4")

        res = file_dialog.scan_batch_folder(self.temp_dir)
        self.assertEqual(res["status"], "SYMMETRIC")
        self.assertTrue(res["is_symmetric"])
        self.assertEqual(res["front_count"], 2)
        self.assertEqual(res["rear_count"], 2)
        self.assertEqual(res["discrepancy"], 0)

    def test_scan_asymmetric_batch_folder(self):
        front_dir = Path(self.temp_dir) / "front"
        rear_dir = Path(self.temp_dir) / "rear"
        front_dir.mkdir()
        rear_dir.mkdir()

        (front_dir / "01.jpg").write_bytes(b"dummy1")
        (front_dir / "02.jpg").write_bytes(b"dummy2")
        (front_dir / "03.jpg").write_bytes(b"dummy3")
        (rear_dir / "01.jpg").write_bytes(b"dummy4")

        res = file_dialog.scan_batch_folder(self.temp_dir)
        self.assertEqual(res["status"], "ASYMMETRIC")
        self.assertFalse(res["is_symmetric"])
        self.assertEqual(res["front_count"], 3)
        self.assertEqual(res["rear_count"], 1)
        self.assertEqual(res["discrepancy"], 2)

    def test_scan_missing_rear_subfolder(self):
        front_dir = Path(self.temp_dir) / "front"
        front_dir.mkdir()
        (front_dir / "01.jpg").write_bytes(b"dummy1")

        res = file_dialog.scan_batch_folder(self.temp_dir)
        self.assertEqual(res["status"], "MISSING_REAR")
        self.assertFalse(res["is_symmetric"])
        self.assertEqual(res["front_count"], 1)
        self.assertEqual(res["rear_count"], 0)


class PairingSynergyTests(TestCase):
    def setUp(self):
        self.batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label="Test Batch Synergy",
        )

    def _create_img(self, filename, orientation, plate="", dt=None, status=EvidenceImage.Status.PLATE_DETECTED):
        import uuid
        return EvidenceImage.objects.create(
            batch=self.batch,
            file_hash=uuid.uuid4().hex * 2,
            original_source_path=f"/path/to/{filename}",
            vault_file=f"vault/{filename}",
            detected_plate=plate,
            orientation=orientation,
            captured_at=dt,
            status=status,
        )

    def test_tier1_plate_ocr_match(self):
        now = datetime.now(timezone.utc)
        f = self._create_img("front_camA.jpg", EvidenceImage.Orientation.FRONT, plate="UMA123X", dt=now)
        r = self._create_img("rear_camB.jpg", EvidenceImage.Orientation.REAR, plate="UMA123X", dt=now + timedelta(seconds=20))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 1)

        pair = VehicleInstallationPair.objects.get(front_image=f, rear_image=r)
        self.assertTrue(pair.is_complete)
        self.assertIn("Plate OCR Match", pair.operator_note)

    def test_tier2a_filename_stem_match(self):
        now = datetime.now(timezone.utc)
        # Different times, no detected plate, but matching file stem 'bike_42'
        f = self._create_img("front/bike_42.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=now)
        r = self._create_img("rear/bike_42.jpg", EvidenceImage.Orientation.REAR, plate="", dt=now + timedelta(seconds=120))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 1)

        pair = VehicleInstallationPair.objects.get(front_image=f, rear_image=r)
        self.assertTrue(pair.is_complete)
        self.assertIn("Filename Stem Alignment", pair.operator_note)
        self.assertIn("bike_42", pair.operator_note)

    def test_tier2b_camera_sequence_index_match(self):
        now = datetime.now(timezone.utc)
        # Dual camera system with different naming schemes but matching shutter counter #7
        # front_station_0007.jpg (Front) vs rear_line_0007.jpg (Rear)
        f = self._create_img("front_station_0007.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=now)
        r = self._create_img("rear_line_0007.jpg", EvidenceImage.Orientation.REAR, plate="", dt=now + timedelta(seconds=2))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 1)

        pair = VehicleInstallationPair.objects.get(front_image=f, rear_image=r)
        self.assertTrue(pair.is_complete)
        self.assertIn("Camera Shutter Index", pair.operator_note)
        self.assertIn("#7", pair.operator_note)

    def test_tier3_temporal_uturn_walk(self):
        # Single technician walk:
        # Rears: Bike 1 (10:00:00, IMG_0001), Bike 2 (10:00:15, IMG_0002)
        # Turnaround at Bike 2 (10:00:25)
        # Fronts: Bike 2 (10:00:35, IMG_0003), Bike 1 (10:00:50, IMG_0004)
        base_t = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
        r1 = self._create_img("IMG_0001.jpg", EvidenceImage.Orientation.REAR, plate="", dt=base_t)
        r2 = self._create_img("IMG_0002.jpg", EvidenceImage.Orientation.REAR, plate="", dt=base_t + timedelta(seconds=15))
        f2 = self._create_img("IMG_0003.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=base_t + timedelta(seconds=35))
        f1 = self._create_img("IMG_0004.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=base_t + timedelta(seconds=50))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 2)

        pair_bike1 = VehicleInstallationPair.objects.get(front_image=f1, rear_image=r1)
        pair_bike2 = VehicleInstallationPair.objects.get(front_image=f2, rear_image=r2)
        self.assertTrue(pair_bike1.is_complete)
        self.assertTrue(pair_bike2.is_complete)
        self.assertIn("U-Turn Walk", pair_bike1.operator_note)
        self.assertIn("U-Turn Walk", pair_bike2.operator_note)

    def test_uturn_walk_with_intermediate_pause_does_not_split(self):
        # Technician shoots Front 1, pauses 12 minutes (720s), shoots Front 2, turns around (9s), shoots Rear 2, Rear 1
        base_t = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
        f1 = self._create_img("PAUSE_F1.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=base_t)
        # 12 minutes pause during front walk
        f2 = self._create_img("PAUSE_F2.jpg", EvidenceImage.Orientation.FRONT, plate="", dt=base_t + timedelta(seconds=720))
        # Turnaround in 9s
        r2 = self._create_img("PAUSE_R2.jpg", EvidenceImage.Orientation.REAR, plate="", dt=base_t + timedelta(seconds=729))
        r1 = self._create_img("PAUSE_R1.jpg", EvidenceImage.Orientation.REAR, plate="", dt=base_t + timedelta(seconds=745))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 2)
        self.assertEqual(summary.incomplete, 0)
        self.assertEqual(len(summary.discrepancies), 0)

        pair_bike1 = VehicleInstallationPair.objects.get(front_image=f1, rear_image=r1)
        pair_bike2 = VehicleInstallationPair.objects.get(front_image=f2, rear_image=r2)
        self.assertTrue(pair_bike1.is_complete)
        self.assertTrue(pair_bike2.is_complete)

    def test_asymmetric_count_surplus_handling(self):
        now = datetime.now(timezone.utc)
        f1 = self._create_img("front_01.jpg", EvidenceImage.Orientation.FRONT, plate="UMA100A", dt=now)
        f2 = self._create_img("front_02.jpg", EvidenceImage.Orientation.FRONT, plate="UMA200B", dt=now + timedelta(seconds=10))
        r1 = self._create_img("rear_01.jpg", EvidenceImage.Orientation.REAR, plate="UMA100A", dt=now + timedelta(seconds=5))

        summary = association.run_association(batch_id=self.batch.batch_id)
        self.assertEqual(summary.complete_pairs, 1)
        self.assertEqual(summary.incomplete, 1)

        surplus_pair = VehicleInstallationPair.objects.get(front_image=f2)
        self.assertFalse(surplus_pair.is_complete)
        self.assertEqual(surplus_pair.verification_status, VehicleInstallationPair.VerificationStatus.INCOMPLETE)
        self.assertIn("Rear photo missing", surplus_pair.operator_note)


class IngestPhotosCommandCountValidationTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="itms_cli_ingest_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_strict_counts_aborts_on_asymmetric_batch(self):
        front_dir = Path(self.temp_dir) / "front"
        rear_dir = Path(self.temp_dir) / "rear"
        front_dir.mkdir()
        rear_dir.mkdir()

        (front_dir / "01.jpg").write_bytes(b"dummy1")
        (front_dir / "02.jpg").write_bytes(b"dummy2")
        (rear_dir / "01.jpg").write_bytes(b"dummy3")

        with self.assertRaises(CommandError) as ctx:
            call_command(
                "ingest_photos",
                self.temp_dir,
                strict_counts=True,
            )
        self.assertIn("PHOTO COUNT MISMATCH", str(ctx.exception))
