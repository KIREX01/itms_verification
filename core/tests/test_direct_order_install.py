"""
Tests for Two-Way Direct Single-Order Installation & Submission API.
Verifies stage detection, hardware Step 1 Save & Proceed, direct photo attachment, and submission.
"""
import io
from unittest.mock import patch, MagicMock
from PIL import Image

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from core.models import (
    InstallationOrder,
    VehicleInstallationPair,
    EvidenceImage,
    SubmissionAuditLog,
)
from core.services import config_service
from core.services.submission_worker import SubmissionOutcome


def _create_test_image(text="TEST"):
    """Helper to generate an in-memory JPEG image."""
    img = Image.new("RGB", (640, 480), color=(73, 109, 137))
    bio = io.BytesIO()
    img.save(bio, format="JPEG")
    bio.seek(0)
    bio.name = f"{text.lower()}.jpg"
    return bio


class DirectOrderInstallApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testoperator", password="password123")
        self.client.login(username="testoperator", password="password123")

        # Enable dry run mode for safety
        cfg = config_service.load_config()
        cfg.setdefault("submission", {})["dry_run_mode"] = True
        cfg.setdefault("submission", {})["backend"] = "mock"
        config_service.save_config(cfg)

        self.order = InstallationOrder.objects.create(
            order_number="PO-TEST-001",
            registration_number="UMA123A",
            vin="VIN1234567890",
            status=InstallationOrder.Status.PENDING,
            front_plate_serial="FP-001",
            rear_plate_serial="RP-001",
            gps_tracker_id="GPS-001",
        )

    def test_unauthenticated_access_denied(self):
        anon_client = Client()
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        response = anon_client.get(url)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json().get("success"))

    def test_order_not_found_returns_404(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": "NONEXISTENT-999"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json().get("success"))

    def test_get_order_install_status(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["order"]["order_number"], "PO-TEST-001")
        self.assertEqual(data["order"]["registration_number"], "UMA123A")
        self.assertEqual(data["order"]["front_plate_serial"], "FP-001")

    def test_save_step1_dry_run(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        with patch("core.services.itms_web_client.ITMSWebClient.submit_installation_step1") as mock_step1:
            mock_step1.return_value = {
                "success": True,
                "step": 1,
                "order_uuid": "mock-uuid-001",
                "message": "Step 1 saved.",
            }
            response = self.client.post(url, {
                "action": "save_step1",
                "dry_run": "true",
                "front_plate_id": "FP-001",
                "rear_plate_id": "RP-001",
                "tracker_id": "GPS-001",
            })
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("stage"), "STAGE_2_EVIDENCE")

            # Verify audit log recorded
            log = SubmissionAuditLog.objects.filter(simulated_token=self.order.order_number).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.action, SubmissionAuditLog.Action.SUBMIT)

    def test_attach_photos_and_direct_submit(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        front_img = _create_test_image("FRONT")
        rear_img = _create_test_image("REAR")

        # 1. Test attaching photos
        response = self.client.post(url, {
            "action": "attach_photos",
            "front_photo": front_img,
            "rear_photo": rear_img,
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("is_complete"))
        pair_id = data.get("pair_id")
        self.assertIsNotNone(pair_id)

        pair = VehicleInstallationPair.objects.get(id=pair_id)
        self.assertEqual(pair.order, self.order)
        self.assertIsNotNone(pair.front_image)
        self.assertIsNotNone(pair.rear_image)

        # 2. Test direct submit on the attached pair with mocked submission
        with patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(
                pair_id=pair.id,
                success=True,
                token="TEST-DIRECT-TOKEN",
                dry_run=True,
            )
            response_submit = self.client.post(url, {
                "action": "submit",
                "dry_run": "true",
            })
            self.assertEqual(response_submit.status_code, 200)
            data_submit = response_submit.json()
            self.assertTrue(data_submit.get("success"))
            self.assertTrue(data_submit.get("is_finalized"))
            self.assertEqual(data_submit.get("order_number"), self.order.order_number)

    def test_1_click_quick_submit_with_photos_in_same_request(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        front_img = _create_test_image("FRONT")
        rear_img = _create_test_image("REAR")

        # Ingest and submit in a single 1-click request with mocked submission
        with patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(
                pair_id=1,
                success=True,
                token="1CLICK-SIM-TOKEN",
                dry_run=True,
            )
            response = self.client.post(url, {
                "action": "submit",
                "dry_run": "true",
                "front_photo": front_img,
                "rear_photo": rear_img,
            })
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("is_finalized"))
            self.assertEqual(data.get("order_number"), self.order.order_number)

            pair = VehicleInstallationPair.objects.filter(order=self.order).first()
            self.assertIsNotNone(pair)
            self.assertTrue(pair.is_complete)
