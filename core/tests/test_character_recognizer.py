"""
Unit and Integration Tests for Direct Character Recognition (ALPR).

Verifies:
  1. CharacterBox geometric spatial properties.
  2. Overlap filtering / NMS deduplication.
  3. Spatial 2-row clustering and reading-order reconstruction:
     - 1-row standard plates (e.g. UBB456C)
     - 2-row motorcycle plates (e.g. UMA123A, UMA421NC)
  4. Confidence thresholding and Uganda syntax validation.
  5. Graceful fallback behavior to PaddleOCR/Tesseract when direct recognition
     is unconfigured, disabled, missing weights, or yields low confidence.
"""
from unittest.mock import patch
import numpy as np
from django.test import SimpleTestCase

from core.vision.character_recognizer import (
    CharacterBox,
    DirectCharacterRecognizer,
    cluster_and_order_characters,
    filter_overlapping_boxes,
    get_character_recognizer,
    recognize_plate_characters,
)
from core.vision.ocr_engine import OCRResult, read_plate_text


class CharacterBoxGeometryTests(SimpleTestCase):
    """Test geometric calculations for character bounding boxes."""

    def test_box_properties(self):
        box = CharacterBox(char="U", confidence=0.95, x1=10.0, y1=20.0, x2=30.0, y2=60.0)
        self.assertEqual(box.xc, 20.0)
        self.assertEqual(box.yc, 40.0)
        self.assertEqual(box.width, 20.0)
        self.assertEqual(box.height, 40.0)

    def test_filter_overlapping_boxes_deduplication(self):
        # Two boxes overlapping heavily on the same letter 'U'
        box_high = CharacterBox(char="U", confidence=0.92, x1=10.0, y1=10.0, x2=30.0, y2=50.0)
        box_low = CharacterBox(char="O", confidence=0.45, x1=12.0, y1=11.0, x2=31.0, y2=49.0)
        box_other = CharacterBox(char="M", confidence=0.88, x1=40.0, y1=10.0, x2=60.0, y2=50.0)

        filtered = filter_overlapping_boxes([box_low, box_high, box_other], iou_threshold=0.45)
        self.assertEqual(len(filtered), 2)
        chars = [b.char for b in filtered]
        self.assertIn("U", chars)
        self.assertIn("M", chars)
        self.assertNotIn("O", chars)


class SpatialClusteringTests(SimpleTestCase):
    """Test spatial row clustering for 1-row and 2-row plates."""

    def test_single_row_ordering(self):
        # Scrambled x order for 'UBB456C' on a single horizontal row (yc ~ 25)
        boxes = [
            CharacterBox(char="B", confidence=0.90, x1=50, y1=10, x2=70, y2=40),
            CharacterBox(char="U", confidence=0.95, x1=10, y1=10, x2=30, y2=40),
            CharacterBox(char="4", confidence=0.85, x1=70, y1=10, x2=90, y2=40),
            CharacterBox(char="B", confidence=0.90, x1=30, y1=10, x2=50, y2=40),
            CharacterBox(char="C", confidence=0.88, x1=130, y1=10, x2=150, y2=40),
            CharacterBox(char="6", confidence=0.89, x1=110, y1=10, x2=130, y2=40),
            CharacterBox(char="5", confidence=0.87, x1=90, y1=10, x2=110, y2=40),
        ]
        text, conf = cluster_and_order_characters(boxes)
        self.assertEqual(text, "UBB456C")
        self.assertAlmostEqual(conf, (0.90 + 0.95 + 0.85 + 0.90 + 0.88 + 0.89 + 0.87) / 7.0, places=2)

    def test_motorcycle_two_row_ordering_7_chars(self):
        # Motorcycle plate: Top row 'UMA' (yc ~ 25), Bottom row '123A' (yc ~ 75)
        boxes = [
            # Bottom row scrambled
            CharacterBox(char="3", confidence=0.90, x1=60, y1=60, x2=80, y2=90),
            CharacterBox(char="1", confidence=0.95, x1=20, y1=60, x2=40, y2=90),
            CharacterBox(char="A", confidence=0.92, x1=80, y1=60, x2=100, y2=90),
            CharacterBox(char="2", confidence=0.91, x1=40, y1=60, x2=60, y2=90),
            # Top row scrambled
            CharacterBox(char="A", confidence=0.94, x1=70, y1=10, x2=90, y2=40),
            CharacterBox(char="U", confidence=0.96, x1=25, y1=10, x2=45, y2=40),
            CharacterBox(char="M", confidence=0.93, x1=48, y1=10, x2=68, y2=40),
        ]
        text, conf = cluster_and_order_characters(boxes)
        self.assertEqual(text, "UMA123A")
        self.assertGreater(conf, 0.90)

    def test_motorcycle_two_row_ordering_8_chars(self):
        # Motorcycle plate: Top row 'UMA', Bottom row '421NC'
        boxes = [
            # Top row
            CharacterBox(char="U", confidence=0.95, x1=25, y1=10, x2=45, y2=40),
            CharacterBox(char="M", confidence=0.94, x1=48, y1=10, x2=68, y2=40),
            CharacterBox(char="A", confidence=0.93, x1=70, y1=10, x2=90, y2=40),
            # Bottom row
            CharacterBox(char="4", confidence=0.92, x1=15, y1=60, x2=35, y2=90),
            CharacterBox(char="2", confidence=0.91, x1=35, y1=60, x2=55, y2=90),
            CharacterBox(char="1", confidence=0.90, x1=55, y1=60, x2=75, y2=90),
            CharacterBox(char="N", confidence=0.89, x1=75, y1=60, x2=95, y2=90),
            CharacterBox(char="C", confidence=0.88, x1=95, y1=60, x2=115, y2=90),
        ]
        text, conf = cluster_and_order_characters(boxes)
        self.assertEqual(text, "UMA421NC")


class DirectCharacterRecognizerLogicTests(SimpleTestCase):
    """Test recognition logic, confidence filters, and validation rules."""

    def setUp(self):
        self.recognizer = DirectCharacterRecognizer(min_confidence=0.65)
        self.dummy_crop = np.zeros((100, 200, 3), dtype=np.uint8)

    def test_empty_or_none_crop_returns_none(self):
        self.assertIsNone(self.recognizer.recognize(None))
        self.assertIsNone(self.recognizer.recognize(np.array([])))

    def test_successful_direct_recognition(self):
        # Inject valid mock detections for UMA421NC
        mock_boxes = [
            CharacterBox(char="U", confidence=0.95, x1=25, y1=10, x2=45, y2=40),
            CharacterBox(char="M", confidence=0.94, x1=48, y1=10, x2=68, y2=40),
            CharacterBox(char="A", confidence=0.93, x1=70, y1=10, x2=90, y2=40),
            CharacterBox(char="4", confidence=0.92, x1=15, y1=60, x2=35, y2=90),
            CharacterBox(char="2", confidence=0.91, x1=35, y1=60, x2=55, y2=90),
            CharacterBox(char="1", confidence=0.90, x1=55, y1=60, x2=75, y2=90),
            CharacterBox(char="N", confidence=0.89, x1=75, y1=60, x2=95, y2=90),
            CharacterBox(char="C", confidence=0.88, x1=95, y1=60, x2=115, y2=90),
        ]
        self.recognizer.set_mock_boxes(mock_boxes)
        res = self.recognizer.recognize(self.dummy_crop)

        self.assertIsNotNone(res)
        self.assertEqual(res.text, "UMA421NC")
        self.assertEqual(res.backend, "direct_char_reader")
        self.assertGreaterEqual(res.confidence, 0.90)

    def test_rejection_on_low_confidence(self):
        # Boxes average to 0.50, which is below min_confidence 0.65
        mock_boxes = [
            CharacterBox(char="U", confidence=0.50, x1=25, y1=10, x2=45, y2=40),
            CharacterBox(char="M", confidence=0.50, x1=48, y1=10, x2=68, y2=40),
            CharacterBox(char="A", confidence=0.50, x1=70, y1=10, x2=90, y2=40),
            CharacterBox(char="1", confidence=0.50, x1=15, y1=60, x2=35, y2=90),
            CharacterBox(char="2", confidence=0.50, x1=35, y1=60, x2=55, y2=90),
            CharacterBox(char="3", confidence=0.50, x1=55, y1=60, x2=75, y2=90),
            CharacterBox(char="A", confidence=0.50, x1=75, y1=60, x2=95, y2=90),
        ]
        self.recognizer.set_mock_boxes(mock_boxes)
        res = self.recognizer.recognize(self.dummy_crop)
        # Low confidence must return None so pipeline falls back to OCR
        self.assertIsNone(res)

    def test_rejection_on_invalid_syntax(self):
        # Boxes spell 'XYZ999999' which is not a valid Uganda registration
        mock_boxes = [
            CharacterBox(char="X", confidence=0.95, x1=10, y1=20, x2=30, y2=50),
            CharacterBox(char="Y", confidence=0.95, x1=30, y1=20, x2=50, y2=50),
            CharacterBox(char="Z", confidence=0.95, x1=50, y1=20, x2=70, y2=50),
            CharacterBox(char="9", confidence=0.95, x1=70, y1=20, x2=90, y2=50),
            CharacterBox(char="9", confidence=0.95, x1=90, y1=20, x2=110, y2=50),
            CharacterBox(char="9", confidence=0.95, x1=110, y1=20, x2=130, y2=50),
        ]
        self.recognizer.set_mock_boxes(mock_boxes)
        res = self.recognizer.recognize(self.dummy_crop)
        # Invalid syntax must return None so pipeline falls back to OCR
        self.assertIsNone(res)

    def test_disabled_recognizer_returns_none(self):
        with patch.object(self.recognizer, "is_enabled", return_value=False):
            mock_boxes = [
                CharacterBox(char="U", confidence=0.95, x1=25, y1=10, x2=45, y2=40),
                CharacterBox(char="M", confidence=0.94, x1=48, y1=10, x2=68, y2=40),
                CharacterBox(char="A", confidence=0.93, x1=70, y1=10, x2=90, y2=40),
                CharacterBox(char="1", confidence=0.92, x1=15, y1=60, x2=35, y2=90),
                CharacterBox(char="2", confidence=0.91, x1=35, y1=60, x2=55, y2=90),
                CharacterBox(char="3", confidence=0.90, x1=55, y1=60, x2=75, y2=90),
                CharacterBox(char="A", confidence=0.89, x1=75, y1=60, x2=95, y2=90),
            ]
            self.recognizer.set_mock_boxes(mock_boxes)
            self.assertIsNone(self.recognizer.recognize(self.dummy_crop))


class OCREngineFallbackIntegrationTests(SimpleTestCase):
    """Test seamless fallback between Direct Character Reader and existing OCR engines."""

    def setUp(self):
        self.dummy_crop = np.zeros((100, 200, 3), dtype=np.uint8)

    def test_read_plate_text_uses_direct_reader_when_valid(self):
        # Simulate direct reader producing a valid plate
        direct_result = OCRResult(text="UMA208NC", confidence=0.94, backend="direct_char_reader")
        with patch("core.vision.character_recognizer.recognize_plate_characters", return_value=direct_result):
            res = read_plate_text(self.dummy_crop)
            self.assertIsNotNone(res)
            self.assertEqual(res.text, "UMA208NC")
            self.assertEqual(res.backend, "direct_char_reader")
            self.assertAlmostEqual(res.confidence, 0.94)

    def test_read_plate_text_falls_back_to_paddle_when_direct_returns_none(self):
        # Direct reader returns None (missing weights or low confidence)
        paddle_result = OCRResult(text="UBB456C", confidence=0.88, backend="paddleocr")
        with patch("core.vision.character_recognizer.recognize_plate_characters", return_value=None):
            with patch("core.vision.ocr_engine._ocr_with_paddle", return_value=paddle_result):
                res = read_plate_text(self.dummy_crop)
                self.assertIsNotNone(res)
                self.assertEqual(res.text, "UBB456C")
                self.assertEqual(res.backend, "paddleocr")

    def test_read_plate_text_falls_back_to_tesseract_when_direct_and_paddle_fail(self):
        # Direct reader returns None, Paddle returns None, Tesseract returns valid
        tess_result = OCRResult(text="UMA123AA", confidence=0.92, backend="tesseract")
        with patch("core.vision.character_recognizer.recognize_plate_characters", return_value=None):
            with patch("core.vision.ocr_engine._ocr_with_paddle", return_value=None):
                with patch("core.vision.ocr_engine._ocr_with_tesseract", return_value=tess_result):
                    res = read_plate_text(self.dummy_crop)
                    self.assertIsNotNone(res)
                    self.assertEqual(res.text, "UMA123AA")
                    self.assertEqual(res.backend, "tesseract")
