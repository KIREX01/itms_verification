from pathlib import Path

import cv2
import numpy as np
from django.test import TestCase

from core.vision import detector, orientation, preprocess


def _synthetic_plate_frame(width=1280, height=720):
    """A synthetic frame with a bright wide rectangle standing in for a plate,
    on a noisy dark background -- enough to exercise the heuristic detector's
    aspect-ratio/contour logic without needing a real photo or trained model.
    """
    frame = (np.random.rand(height, width, 3) * 40).astype(np.uint8)  # dark noisy bg
    # Draw a plate-shaped bright rectangle with vertical "character" edges
    x1, y1, x2, y2 = width // 2 - 150, height // 2 - 30, width // 2 + 150, height // 2 + 30
    frame[y1:y2, x1:x2] = 220
    for stripe_x in range(x1 + 10, x2 - 10, 20):
        frame[y1:y2, stripe_x:stripe_x + 4] = 30
    return frame


def _synthetic_motorcycle_plate_frame(width=1280, height=720):
    """A synthetic frame with a squarish plate (~1.5:1 aspect ratio), typical
    of motorcycle number plates which are nearly as tall as they are wide.
    """
    frame = (np.random.rand(height, width, 3) * 40).astype(np.uint8)
    pw, ph = 120, 80  # ~1.5:1 aspect ratio
    cx, cy = width // 2, height // 2
    x1, y1, x2, y2 = cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2
    frame[y1:y2, x1:x2] = 220
    for stripe_x in range(x1 + 10, x2 - 10, 20):
        frame[y1:y2, stripe_x:stripe_x + 4] = 30
    return frame


class PreprocessTests(TestCase):
    def test_normalize_resolution_targets_height(self):
        frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
        out = preprocess.normalize_resolution(frame, target_height=500)
        self.assertEqual(out.shape[0], 500)

    def test_enhance_contrast_clahe_preserves_shape(self):
        frame = (np.random.rand(200, 300, 3) * 255).astype(np.uint8)
        out = preprocess.enhance_contrast_clahe(frame)
        self.assertEqual(out.shape, frame.shape)

    def test_full_pipeline_runs_without_error(self):
        frame = _synthetic_plate_frame()
        out = preprocess.preprocess_pipeline(frame)
        self.assertEqual(out.shape[0], preprocess.TARGET_HEIGHT)

    def test_auto_orient_rotates_90_clockwise(self):
        """EXIF orientation 6 = camera held in portrait: rotate 90° CW."""
        original = np.zeros((100, 200, 3), dtype=np.uint8)  # landscape
        original[10, 20] = (255, 0, 0)  # unique marker pixel
        oriented = preprocess.auto_orient(original, exif_orientation=6)
        # After 90° CW rotation, (100, 200) landscape → (200, 100) portrait
        self.assertEqual(oriented.shape[:2], (200, 100))

    def test_auto_orient_noop_for_normal(self):
        """EXIF orientation 1 = normal; image should be unchanged."""
        original = np.zeros((100, 200, 3), dtype=np.uint8)
        oriented = preprocess.auto_orient(original, exif_orientation=1)
        self.assertEqual(oriented.shape, original.shape)
        np.testing.assert_array_equal(oriented, original)

    def test_auto_orient_rotate_180(self):
        """EXIF orientation 3 = upside-down: rotate 180°."""
        original = np.zeros((100, 200, 3), dtype=np.uint8)
        oriented = preprocess.auto_orient(original, exif_orientation=3)
        self.assertEqual(oriented.shape[:2], (100, 200))

    def test_auto_orient_rotate_90_ccw(self):
        """EXIF orientation 8 = rotate 90° CCW."""
        original = np.zeros((100, 200, 3), dtype=np.uint8)
        oriented = preprocess.auto_orient(original, exif_orientation=8)
        self.assertEqual(oriented.shape[:2], (200, 100))


class HeuristicDetectorTests(TestCase):
    def test_heuristic_detects_plate_shaped_region(self):
        frame = _synthetic_plate_frame()
        result = detector._detect_with_heuristic(frame)
        self.assertIsNotNone(result)
        self.assertEqual(result.backend, "heuristic")
        x1, y1, x2, y2 = result.bbox
        aspect_ratio = (x2 - x1) / float(y2 - y1)
        self.assertGreaterEqual(aspect_ratio, 1.0)
        self.assertLessEqual(aspect_ratio, 6.5)

    def test_heuristic_detects_motorcycle_plate(self):
        """Squarish motorcycle plates (~1.5:1) should be detected."""
        frame = _synthetic_motorcycle_plate_frame()
        result = detector._detect_with_heuristic(frame)
        self.assertIsNotNone(result, "Heuristic detector should find motorcycle-shaped plates")
        self.assertEqual(result.backend, "heuristic")

    def test_heuristic_returns_none_on_blank_frame(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = detector._detect_with_heuristic(frame)
        self.assertIsNone(result)

    def test_detect_plate_falls_back_to_heuristic_when_yolo_unavailable(self):
        # In the test environment, ultralytics model loading may fail/be absent,
        # so detect_plate() should still return a usable Detection via fallback.
        frame = _synthetic_plate_frame()
        result = detector.detect_plate(frame)
        self.assertIsNotNone(result)


class OrientationTests(TestCase):
    def test_rear_leaning_frame_prefers_rear_or_unknown(self):
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        # Paint strong red taillight-like blobs near left/right edges (BGR: red = (0,0,255))
        frame[50:150, 20:100] = (0, 0, 255)
        frame[50:150, 500:580] = (0, 0, 255)
        result = orientation.classify_orientation(frame)
        self.assertIn(result.orientation, ("REAR", "UNKNOWN"))

    def test_front_leaning_frame_prefers_front_or_unknown(self):
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        # Paint white/yellow headlight-like blobs near left/right edges
        frame[50:150, 20:100] = (230, 230, 230)
        frame[50:150, 500:580] = (230, 230, 230)
        result = orientation.classify_orientation(frame)
        self.assertIn(result.orientation, ("FRONT", "UNKNOWN"))

    def test_blank_frame_is_unknown(self):
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        result = orientation.classify_orientation(frame)
        self.assertEqual(result.orientation, "UNKNOWN")


class CleanCropsCommandTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.crops_dir = Path(self.temp_dir) / "crops"
        self.crops_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_clean_crops_deletes_crop_files(self):
        import io
        from django.core.management import call_command
        from django.test import override_settings

        date_dir = self.crops_dir / "2026-09-07"
        date_dir.mkdir()
        crop_file = date_dir / "test_plate.jpg"
        crop_file.write_bytes(b"dummy image bytes")

        with override_settings(CROPS_ROOT=self.crops_dir):
            out = io.StringIO()
            call_command("clean_crops", stdout=out)
            self.assertFalse(crop_file.exists())
            self.assertIn("Cleaned up 1 temporary crop/debug file", out.getvalue())

    def test_clean_crops_dry_run_preserves_files(self):
        import io
        from django.core.management import call_command
        from django.test import override_settings

        date_dir = self.crops_dir / "2026-09-07"
        date_dir.mkdir()
        crop_file = date_dir / "test_plate.jpg"
        crop_file.write_bytes(b"dummy image bytes")

        with override_settings(CROPS_ROOT=self.crops_dir):
            out = io.StringIO()
            call_command("clean_crops", dry_run=True, stdout=out)
            self.assertTrue(crop_file.exists())
            self.assertIn("[DRY RUN]", out.getvalue())

    def test_process_vision_command_increments_counters_cleanly(self):
        """Verifies process_vision management command processes images without UnboundLocalError."""
        import io
        from django.core.management import call_command
        from unittest.mock import patch, MagicMock
        from core.models import EvidenceImage, IngestionBatch
        from core.vision.detector import Detection

        batch = IngestionBatch.objects.create(batch_id="BATCH-TEST-CMD-01")
        img = EvidenceImage.objects.create(
            batch=batch,
            file_hash="test" * 16,
            original_source_path="fake.jpg",
            vault_file="vault/fake.jpg",
            status=EvidenceImage.Status.NEW,
        )

        dummy_raw = np.zeros((100, 100, 3), dtype=np.uint8)
        dummy_det = Detection(bbox=[10, 10, 50, 50], confidence=0.9, backend="yolo")
        mock_ocr = MagicMock()
        mock_ocr.text = "UMA291PK"
        mock_ocr.confidence = 0.95

        with patch("core.vision.preprocess.load_image", return_value=dummy_raw), \
             patch("core.vision.detector.detect_plate", return_value=dummy_det), \
             patch("core.vision.detector.crop_detection", return_value=dummy_raw), \
             patch("core.vision.ocr_engine.read_plate_text", return_value=mock_ocr):
            out = io.StringIO()
            call_command("process_vision", stdout=out)
            output = out.getvalue()
            self.assertIn("1 processed", output)
            self.assertIn("1 plates found", output)

            img.refresh_from_db()
            self.assertEqual(img.status, EvidenceImage.Status.PLATE_DETECTED)
            self.assertEqual(img.detected_plate, "UMA291PK")
            self.assertEqual(img.error_message, "")

