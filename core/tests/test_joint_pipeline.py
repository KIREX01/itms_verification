import uuid
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from django.core.management import call_command
from django.test import TestCase

from core.models import EvidenceImage, IngestionBatch, InstallationOrder, VehicleInstallationPair
from core.services import vault_service
from core.vision import detector, ocr_engine
from core.vision.joint_pipeline import DualStreamVisionEngine, _character_level_consensus


class DualStreamJointVisionTests(TestCase):
    def test_character_level_consensus_exact_match(self):
        plate, conf, status, details = _character_level_consensus(
            p_front="UMA291PK", conf_front=0.92,
            p_rear="UMA291PK", conf_rear=0.96,
        )
        self.assertEqual(plate, "UMA291PK")
        self.assertEqual(status, "EXACT_MATCH")
        self.assertGreaterEqual(conf, 0.95)

    def test_character_level_consensus_asymmetric_recovery_from_rear(self):
        # Front mudguard is unreadable/empty, stamped rear plate is crisp
        plate, conf, status, details = _character_level_consensus(
            p_front="", conf_front=0.0,
            p_rear="UMA291PK", conf_rear=0.95,
        )
        self.assertEqual(plate, "UMA291PK")
        self.assertEqual(status, "ASYMMETRIC_RECOVERED")
        self.assertGreaterEqual(conf, 0.85)

    def test_character_level_consensus_syntax_slot_disambiguation(self):
        # Digit slot 3 (index 3) confused '1' with 'I' in front OCR
        plate, conf, status, details = _character_level_consensus(
            p_front="UMA29IPK", conf_front=0.88,
            p_rear="UMA291PK", conf_rear=0.94,
        )
        self.assertEqual(plate, "UMA291PK")
        self.assertEqual(status, "SYNTAX_RESOLVED")

    def test_character_level_consensus_active_order_prior(self):
        InstallationOrder.objects.create(
            order_number="ORD-TEST-001",
            registration_number="UMA777Z",
            is_active_on_itms=True,
        )
        # Raw reading has confusion (B vs 8)
        plate, conf, status, details = _character_level_consensus(
            p_front="UMA7778", conf_front=0.80,
            p_rear="UMA777Z", conf_rear=0.85,
        )
        self.assertEqual(plate, "UMA777Z")
        self.assertEqual(status, "ORDER_PRIOR_MATCH")


class ProcessVisionPipelineFlowTests(TestCase):
    def setUp(self):
        self.batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.CLI,
            source_label="Test Pipeline Flow",
        )

    def _create_image(self, rel_path, orient):
        return EvidenceImage.objects.create(
            batch=self.batch,
            file_hash=uuid.uuid4().hex * 2,
            original_source_path=f"/test/{rel_path}",
            vault_file=rel_path,
            orientation=orient,
            folder_orientation=orient,
            status=EvidenceImage.Status.NEW,
        )

    @patch("core.vision.preprocess.load_image")
    @patch("core.vision.detector.detect_plate")
    @patch("core.vision.ocr_engine.read_plate_text")
    def test_pair_first_and_joint_vision_execution(self, mock_ocr, mock_detect, mock_load):
        # Setup synthetic frame
        dummy_frame = np.zeros((300, 300, 3), dtype=np.uint8)
        mock_load.return_value = dummy_frame
        mock_detect.return_value = detector.Detection(
            bbox=[10, 10, 80, 50],
            confidence=0.92,
            backend="yolo",
        )
        mock_ocr.return_value = ocr_engine.OCRResult(
            text="UMA291PK",
            confidence=0.95,
            backend="paddleocr",
        )

        f_img = self._create_image("front/01.jpg", EvidenceImage.Orientation.FRONT)
        r_img = self._create_image("rear/01.jpg", EvidenceImage.Orientation.REAR)

        # Execute unified process_vision command
        call_command("process_vision", f"--batch={self.batch.batch_id}")

        # Assert physical pairing occurred
        pair = VehicleInstallationPair.objects.filter(front_image=f_img, rear_image=r_img).first()
        self.assertIsNotNone(pair)
        self.assertTrue(pair.is_complete)
        self.assertEqual(pair.registration_number_detected, "UMA291PK")
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)
