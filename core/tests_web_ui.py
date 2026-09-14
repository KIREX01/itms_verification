"""
Unit and integration tests for the Web Operator Dashboard, Authentication, and REST API endpoints.
"""
import json
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import config_service


class WebUITestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # Create operator user
        self.user = User.objects.create_user(
            username="test_op",
            password="password123",
            first_name="Test",
            last_name="Operator",
        )

        # Create sample batch
        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-TEST-001",
            source_type=IngestionBatch.SourceType.WEB,
            source_label="Test Batch",
        )

        # Create sample evidence photos
        self.front_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_front_111",
            original_source_path="/test/front_01.jpg",
            vault_file="vault/2026-09-13/front_01.jpg",
            detected_plate="UMA123A",
            orientation=EvidenceImage.Orientation.FRONT,
            ocr_confidence=0.95,
            detector_confidence=0.92,
        )

        self.rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_rear_222",
            original_source_path="/test/rear_01.jpg",
            vault_file="vault/2026-09-13/rear_01.jpg",
            detected_plate="UMA123A",
            orientation=EvidenceImage.Orientation.REAR,
            ocr_confidence=0.93,
            detector_confidence=0.90,
        )

        # Create sample order
        self.order = InstallationOrder.objects.create(
            order_number="ORD-1001",
            registration_number="UMA 123A",
            vin="VIN1234567890",
            warehouse_name="Kampala Hub",
            status=InstallationOrder.Status.PENDING,
        )

        # Create sample pair
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA123A",
            order=self.order,
            front_image=self.front_img,
            rear_image=self.rear_img,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            match_type=VehicleInstallationPair.MatchType.EXACT,
            match_score=100.0,
            is_complete=True,
        )

    # --- Authentication & Protection Tests ---

    def test_anonymous_access_redirects_to_login(self):
        """Unauthenticated visitor is redirected to login page."""
        res = self.client.get(reverse("core:dashboard"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login/", res.headers["Location"])

    def test_login_view_renders(self):
        """GET /login/ renders login template."""
        res = self.client.get(reverse("core:login"))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "core/login.html")
        self.assertContains(res, "Operator Sign In")

    def test_login_successful(self):
        """POST /login/ with valid credentials signs in operator and redirects to dashboard."""
        res = self.client.post(reverse("core:login"), {
            "username": "test_op",
            "password": "password123",
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["Location"], "/")

        # Subsequent dashboard request succeeds
        res_dash = self.client.get(reverse("core:dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "test_op")

    def test_login_invalid_credentials(self):
        """POST /login/ with wrong password displays error."""
        res = self.client.post(reverse("core:login"), {
            "username": "test_op",
            "password": "wrong_password",
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid password")

    def test_signup_view_creates_account(self):
        """POST /signup/ creates new operator user and redirects to dashboard."""
        res = self.client.post(reverse("core:signup"), {
            "username": "new_operator",
            "full_name": "New Officer",
            "email": "officer@works.go.ug",
            "password": "securepassword123",
            "password_confirm": "securepassword123",
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(User.objects.filter(username="new_operator").exists())

    def test_logout_clears_session(self):
        """GET /logout/ clears authentication and redirects to login."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:logout"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login/", res.headers["Location"])

    # --- ITMS Account Connection Tests ---

    def test_api_itms_status(self):
        """GET /api/itms/status/ returns connection status."""
        res = self.client.get(reverse("core:api_itms_status"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("authenticated", data["status"])

    def test_api_itms_connect_missing_credentials(self):
        """POST /api/itms/connect/ without credentials returns 400."""
        res = self.client.post(reverse("core:api_itms_connect"), data={})
        self.assertEqual(res.status_code, 400)

    def test_api_itms_disconnect(self):
        """POST /api/itms/disconnect/ returns success."""
        res = self.client.post(reverse("core:api_itms_disconnect"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

    # --- Operator Workflow REST API Tests (Authenticated) ---

    def test_dashboard_view_renders_authenticated(self):
        """GET / returns HTTP 200 with dashboard template when logged in."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ITMS VERIFICATION COPILOT")
        self.assertTemplateUsed(res, "core/dashboard.html")

    def test_api_stats(self):
        """GET /api/stats/ returns valid operational metrics."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:api_stats"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["total_pairs"], 1)
        self.assertEqual(data["pending_review"], 1)

    def test_api_pairs_list(self):
        """GET /api/pairs/ returns array of pair cards."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:api_pairs_list"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["pairs"]), 1)

    def test_api_pair_detail(self):
        """GET /api/pairs/<id>/ returns complete inspection details."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:api_pair_detail", args=[self.pair.id]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        pair = data["pair"]
        self.assertEqual(pair["id"], self.pair.id)
        self.assertIsNotNone(pair["front"])
        self.assertIsNotNone(pair["rear"])

    def test_api_pair_action_approve(self):
        """POST /api/pairs/<id>/action/ with 'approve' marks pair APPROVED."""
        self.client.login(username="test_op", password="password123")
        res = self.client.post(
            reverse("core:api_pair_action", args=[self.pair.id]),
            data={"action": "approve"},
        )
        self.assertEqual(res.status_code, 200)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)

    def test_api_pair_action_swap(self):
        """POST /api/pairs/<id>/action/ with 'swap' swaps front and rear photos."""
        self.client.login(username="test_op", password="password123")
        old_front = self.pair.front_image
        old_rear = self.pair.rear_image
        res = self.client.post(
            reverse("core:api_pair_action", args=[self.pair.id]),
            data={"action": "swap"},
        )
        self.assertEqual(res.status_code, 200)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.front_image, old_rear)
        self.assertEqual(self.pair.rear_image, old_front)

    def test_api_pair_action_edit_plate(self):
        """POST /api/pairs/<id>/action/ with 'edit_plate' corrects plate and normalizes."""
        self.client.login(username="test_op", password="password123")
        res = self.client.post(
            reverse("core:api_pair_action", args=[self.pair.id]),
            data={"action": "edit_plate", "plate": "uma 999 z"},
        )
        self.assertEqual(res.status_code, 200)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.registration_number_detected, "UMA999Z")

    def test_api_pair_action_link_order(self):
        """POST /api/pairs/<id>/action/ with 'link_order' rebinds to specified order."""
        self.client.login(username="test_op", password="password123")
        new_order = InstallationOrder.objects.create(
            order_number="ORD-2002",
            registration_number="UBB 555B",
        )
        res = self.client.post(
            reverse("core:api_pair_action", args=[self.pair.id]),
            data={"action": "link_order", "order_id": new_order.id},
        )
        self.assertEqual(res.status_code, 200)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.order, new_order)

    def test_api_export_report(self):
        """GET /api/export/report/ returns valid CSV file."""
        self.client.login(username="test_op", password="password123")
        res = self.client.get(reverse("core:api_export_report"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", res["Content-Disposition"])

    def test_api_toggle_dry_run(self):
        """POST /api/settings/toggle-dry-run/ toggles simulation mode."""
        self.client.login(username="test_op", password="password123")
        initial = config_service.get_setting("submission.dry_run_mode", True)
        res = self.client.post(reverse("core:api_toggle_dry_run"))
        self.assertEqual(res.status_code, 200)
        new_val = config_service.get_setting("submission.dry_run_mode", True)
        self.assertNotEqual(initial, new_val)

    # --- ITMS Orders & Archive Explorer Tests ---

    def test_api_itms_orders_explorer_local_active(self):
        """GET /api/itms/orders/?source=local&tab=active returns active orders."""
        res = self.client.get(reverse("core:api_itms_orders_explorer"), {
            "source": "local",
            "tab": "active",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["source"], "local")
        self.assertEqual(data["tab"], "active")
        self.assertGreaterEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["order_number"], self.order.order_number)

    def test_api_itms_orders_explorer_local_archive(self):
        """GET /api/itms/orders/?source=local&tab=archive filters archived orders."""
        archived_order = InstallationOrder.objects.create(
            order_number="ORD-ARCHIVE-99",
            registration_number="UMA 999Z",
            is_archived=True,
            status=InstallationOrder.Status.INSTALLED,
        )
        res = self.client.get(reverse("core:api_itms_orders_explorer"), {
            "source": "local",
            "tab": "archive",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tab"], "archive")
        order_numbers = [o["order_number"] for o in data["orders"]]
        self.assertIn("ORD-ARCHIVE-99", order_numbers)
        self.assertNotIn(self.order.order_number, order_numbers)

    def test_api_itms_orders_search(self):
        """GET /api/itms/orders/ filters by plate search."""
        res = self.client.get(reverse("core:api_itms_orders_explorer"), {
            "source": "local",
            "tab": "active",
            "search": "123A",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["order_number"], "ORD-1001")

    def test_api_itms_order_detail(self):
        """GET /api/itms/orders/<order_number>/ returns complete hardware and photo data."""
        self.order.gps_tracker_id = "GPS-998877"
        self.order.front_plate_serial = "SER-FRONT-11"
        self.order.rear_plate_serial = "SER-REAR-22"
        self.order.front_photo_url = "https://stock.itms.ug/media/test_front.jpg"
        self.order.save()

        res = self.client.get(reverse("core:api_itms_order_detail", args=[self.order.order_number]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        o = data["order"]
        self.assertEqual(o["order_number"], "ORD-1001")
        self.assertEqual(o["hardware"]["gps_tracker_id"], "GPS-998877")
        self.assertEqual(o["hardware"]["front_plate_serial"], "SER-FRONT-11")
        self.assertIsNotNone(o["matched_pair"])
        self.assertEqual(o["matched_pair"]["id"], self.pair.id)

    def test_api_itms_order_detail_not_found(self):
        """GET /api/itms/orders/<non_existent>/ returns 404."""
        res = self.client.get(reverse("core:api_itms_order_detail", args=["NON-EXISTENT-ORDER"]))
        self.assertEqual(res.status_code, 404)
        data = res.json()
        self.assertFalse(data["success"])

    def test_api_itms_sync_now_unauthenticated(self):
        """POST /api/itms/orders/sync/now/ returns 401 when not connected to ITMS."""
        res = self.client.post(reverse("core:api_itms_sync_now"), {"tab": "active"})
        # Since test environment has no live cookies, expect 401
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertFalse(data["success"])
        self.assertIn("not connected", data["error"])

    def test_api_batches_list(self):
        """GET /api/batches/ returns list of ingestion batches."""
        res = self.client.get(reverse("core:api_batches_list"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(len(data["batches"]), 1)
        batch_ids = [b["batch_id"] for b in data["batches"]]
        self.assertIn(self.batch.batch_id, batch_ids)

    def test_api_batch_detail(self):
        """GET /api/batches/<batch_id>/ returns batch metadata, photos, and pair assignments."""
        res = self.client.get(reverse("core:api_batch_detail", args=[self.batch.batch_id]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["batch_id"], self.batch.batch_id)
        self.assertEqual(len(data["images"]), 2)
        # Check image and pair mapping
        first_img = data["images"][0]
        self.assertIn("url", first_img)
        self.assertIn("orientation", first_img)
        self.assertIsNotNone(first_img["pair"])
        self.assertEqual(first_img["pair"]["pair_id"], self.pair.id)

    def test_api_batch_detail_not_found(self):
        """GET /api/batches/<non_existent>/ returns 404."""
        res = self.client.get(reverse("core:api_batch_detail", args=["NON-EXISTENT-BATCH"]))
        self.assertEqual(res.status_code, 404)

    def test_api_toggle_dry_run_updates_settings_and_config(self):
        """POST /api/settings/toggle-dry-run/ toggles mode and updates settings.ITMS_WEB_DRY_RUN."""
        from django.conf import settings
        from core.services import config_service

        # Set to True first
        config_service.set_setting("submission.dry_run_mode", True)
        setattr(settings, "ITMS_WEB_DRY_RUN", True)

        # Toggle to False
        res = self.client.post(reverse("core:api_toggle_dry_run"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["dry_run"])
        self.assertFalse(config_service.get_setting("submission.dry_run_mode"))
        self.assertFalse(settings.ITMS_WEB_DRY_RUN)

        # Toggle back with explicit mode=true
        res2 = self.client.post(reverse("core:api_toggle_dry_run"), {"mode": "true"})
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertTrue(data2["dry_run"])
        self.assertTrue(config_service.get_setting("submission.dry_run_mode"))
        self.assertTrue(settings.ITMS_WEB_DRY_RUN)

    def test_api_pair_action_submit_respects_explicit_dry_run(self):
        """POST /api/pairs/<id>/action/ with action=submit respects explicit dry_run param."""
        from unittest.mock import patch
        from core.services.submission_worker import SubmissionOutcome

        with patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(pair_id=self.pair.id, success=True, token="TEST-RECEIPT", dry_run=False)

            res = self.client.post(
                reverse("core:api_pair_action", args=[self.pair.id]),
                {"action": "submit", "dry_run": "false"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["success"])
            self.assertFalse(data["dry_run"])
            # Assert mock was called with dry_run=False
            mock_sub.assert_called_once()
            _, kwargs = mock_sub.call_args
            self.assertFalse(kwargs.get("dry_run"))

    def test_api_pair_action_submit_follows_config_dry_run_when_unspecified(self):
        """POST /api/pairs/<id>/action/ with action=submit defaults to config.json setting."""
        from unittest.mock import patch
        from core.services import config_service
        from core.services.submission_worker import SubmissionOutcome

        config_service.set_setting("submission.dry_run_mode", True)

        with patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(pair_id=self.pair.id, success=True, token="SIM-RECEIPT", dry_run=True)

            res = self.client.post(
                reverse("core:api_pair_action", args=[self.pair.id]),
                {"action": "submit"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["success"])
            self.assertTrue(data["dry_run"])
            mock_sub.assert_called_once()
            _, kwargs = mock_sub.call_args
            self.assertTrue(kwargs.get("dry_run"))


