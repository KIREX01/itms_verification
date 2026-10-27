"""
Tests for Web UI Photo Upload Vision Processing, Association, and Settings Configuration.
"""
from io import BytesIO
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    VehicleInstallationPair,
)
from core.services import config_service


class WebUploadAndSettingsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testoperator", password="password123")
        self.client = Client()
        self.client.login(username="testoperator", password="password123")

    def _create_dummy_image_file(self, name="photo.jpg", color=(73, 109, 137)):
        file_obj = BytesIO()
        image = Image.new("RGB", (100, 100), color=color)
        image.save(file_obj, format="JPEG")
        file_obj.seek(0)
        return SimpleUploadedFile(name, file_obj.read(), content_type="image/jpeg")

    @patch("core.services.vault_service.process_single_image_vision")
    def test_upload_photos_view_runs_vision_and_pairs_complete(self, mock_vision):
        # Create a pending order awaiting fitment
        order = InstallationOrder.objects.create(
            order_number="ORD-UPLOAD-01",
            registration_number="UMA 888X",
            vin="CHAS-888X",
            itms_stage="Awaiting Fitment",
            is_active_on_itms=True,
            is_archived=False,
        )

        def mock_vision_impl(img, save_crops=False):
            img.detected_plate = "UMA 888X"
            img.ocr_confidence = 0.95
            img.status = EvidenceImage.Status.PLATE_DETECTED
            img.save(update_fields=["detected_plate", "ocr_confidence", "status"])
            return {"success": True, "detected_plate": "UMA 888X"}

        mock_vision.side_effect = mock_vision_impl

        front_file = self._create_dummy_image_file("front_001.jpg", color=(255, 0, 0))
        rear_file = self._create_dummy_image_file("rear_001.jpg", color=(0, 0, 255))

        url = reverse("core:upload_photos")
        response = self.client.post(
            url,
            {
                "front_photos": [front_file],
                "rear_photos": [rear_file],
                "batch_label": "Test Direct Upload",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("front_count"), 1)
        self.assertEqual(data.get("rear_count"), 1)
        self.assertTrue(data.get("is_symmetric"))

        # Verify vision processing was invoked for both photos
        self.assertEqual(mock_vision.call_count, 2)

        # Verify pair was created and is complete
        pair = VehicleInstallationPair.objects.filter(registration_number_detected="UMA 888X").first()
        self.assertIsNotNone(pair)
        self.assertTrue(pair.is_complete)
        self.assertIsNotNone(pair.front_image)
        self.assertIsNotNone(pair.rear_image)
        # Verify order was automatically linked
        self.assertEqual(pair.order, order)

    def test_config_service_order_sync_interval_schema_and_defaults(self):
        config_service.set_setting("sync.order_sync_interval_seconds", 600)
        default_val = config_service.get_setting("sync.order_sync_interval_seconds")
        self.assertEqual(default_val, 600)

        # Update setting
        config_service.set_setting("sync.order_sync_interval_seconds", 300)
        updated_val = config_service.get_setting("sync.order_sync_interval_seconds")
        self.assertEqual(updated_val, 300)

        # Restore default
        config_service.set_setting("sync.order_sync_interval_seconds", 600)

        # Check schema
        schema = config_service.USER_SETTINGS_SCHEMA.get("sync.order_sync_interval_seconds")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["default"], 600)
