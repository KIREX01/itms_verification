"""
Unit and integration tests for Quick Install pipeline improvements:
- Serial-to-UUID option resolution (resolve_option_uuid)
- Clean HTML option parsing with serials (parse_installation_page_html)
- Fresh CSRF token extraction and pass-through on Step 1 save
- 1-Click direct submit carrying hardware serials through submission_worker and orchestrator
- Pre-fill from InstallationKit inventory for walk-in orders
"""
import io
import re
from unittest.mock import MagicMock, patch
from PIL import Image

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    InstallationKit,
    InstallationOrder,
    VehicleInstallationPair,
    EvidenceImage,
    SubmissionAuditLog,
)
from core.services import config_service
from core.services.itms_web_client import ITMSWebClient, ITMSWebSessionData, get_web_client
from core.services.submission_worker import SubmissionOutcome


def _create_test_image(text="TEST"):
    """Helper to generate an in-memory JPEG image."""
    img = Image.new("RGB", (640, 480), color=(73, 109, 137))
    bio = io.BytesIO()
    img.save(bio, format="JPEG")
    bio.seek(0)
    bio.name = f"{text.lower()}.jpg"
    return bio


class QuickInstallHardwareResolutionTests(TestCase):
    def setUp(self):
        self.client = ITMSWebClient()
        self.sample_dropdown = {
            "selected_id": "a90e3bf1-5176-422e-9e3f-ac8235e2d243",
            "selected_text": "001182812 (UMA 123A)",
            "options": [
                {
                    "value": "a90e3bf1-5176-422e-9e3f-ac8235e2d243",
                    "text": "001182812 (UMA 123A)",
                    "serial": "001182812",
                    "selected": True,
                },
                {
                    "value": "bbbbbbbb-2222-3333-4444-555555555555",
                    "text": "001182813 (UMA 123B)",
                    "serial": "001182813",
                    "selected": False,
                },
            ],
        }

    def test_resolve_exact_serial_match(self):
        # Human operator types serial '001182813'
        resolved = ITMSWebClient.resolve_option_uuid(self.sample_dropdown, "001182813")
        self.assertEqual(resolved, "bbbbbbbb-2222-3333-4444-555555555555")

    def test_resolve_leading_zero_variations(self):
        # Operator types serial without leading zeros: '1182812'
        resolved = ITMSWebClient.resolve_option_uuid(self.sample_dropdown, "1182812")
        self.assertEqual(resolved, "a90e3bf1-5176-422e-9e3f-ac8235e2d243")

    def test_resolve_plate_number_contained_in_option_text(self):
        # Operator enters plate number 'UMA 123B'
        resolved = ITMSWebClient.resolve_option_uuid(self.sample_dropdown, "UMA 123B")
        self.assertEqual(resolved, "bbbbbbbb-2222-3333-4444-555555555555")

    def test_resolve_already_valid_uuid(self):
        # Client sends the UUID directly
        uuid_in = "a90e3bf1-5176-422e-9e3f-ac8235e2d243"
        resolved = ITMSWebClient.resolve_option_uuid(self.sample_dropdown, uuid_in)
        self.assertEqual(resolved, uuid_in)

    def test_resolve_empty_input_falls_back_to_selected_id(self):
        resolved = ITMSWebClient.resolve_option_uuid(self.sample_dropdown, "")
        self.assertEqual(resolved, "a90e3bf1-5176-422e-9e3f-ac8235e2d243")

    def test_resolve_single_option_fallback(self):
        single_dropdown = {
            "selected_id": "",
            "options": [
                {
                    "value": "11111111-2222-3333-4444-555555555555",
                    "text": "8BAE470808CF - Tracker",
                    "serial": "8BAE470808CF",
                }
            ],
        }
        resolved = ITMSWebClient.resolve_option_uuid(single_dropdown, "UNKNOWN-SERIAL")
        self.assertEqual(resolved, "11111111-2222-3333-4444-555555555555")


class QuickInstallStep1FormParsingTests(TestCase):
    def test_parse_installation_page_html_with_options(self):
        html_sample = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="csrf-token" content="fresh-token-xyz-789">
        </head>
        <body>
            <form id="installationOrderCreateForm" action="/installation-orders/installation?id=a90e3bf1-5176-422e-9e3f-ac8235e2d243" method="post">
                <input type="hidden" name="_csrf-frontend" value="fresh-token-xyz-789">
                <select id="installationorderform-front_license_plate_id">
                    <option value="">Select Front Plate</option>
                    <option value="uuid-fp-1" selected>001182812 (UMA 300P)</option>
                    <option value="uuid-fp-2">001182899</option>
                </select>
                <select id="installationorderform-back_license_plate_id">
                    <option value="uuid-rp-1">001182813 (UMA 300P)</option>
                </select>
                <select id="installationorderform-tracker_id">
                    <option value="uuid-tr-1" selected>8BAE470808CF</option>
                </select>
            </form>
        </body>
        </html>
        """
        client = ITMSWebClient()
        parsed = client.parse_installation_page_html(html_sample)

        self.assertEqual(parsed["csrf_token"], "fresh-token-xyz-789")
        self.assertEqual(parsed["order_uuid"], "a90e3bf1-5176-422e-9e3f-ac8235e2d243")

        front_p = parsed["front_plate"]
        self.assertEqual(front_p["selected_id"], "uuid-fp-1")
        self.assertEqual(front_p["selected_text"], "001182812 (UMA 300P)")
        self.assertEqual(len(front_p["options"]), 2)
        self.assertEqual(front_p["options"][0]["serial"], "001182812")


class QuickInstallApiEndpointTests(TestCase):
    def setUp(self):
        self.http_client = Client()
        self.user = User.objects.create_user(username="quickop", password="password123")
        self.http_client.login(username="quickop", password="password123")

        cfg = config_service.load_config()
        cfg.setdefault("submission", {})["dry_run_mode"] = True
        cfg.setdefault("submission", {})["backend"] = "web"
        config_service.save_config(cfg)

        self.order = InstallationOrder.objects.create(
            order_number="PO-QUICK-001",
            registration_number="UMA300P",
            vin="VIN999888777",
            status=InstallationOrder.Status.PENDING,
            itms_order_uuid="a90e3bf1-5176-422e-9e3f-ac8235e2d243",
            front_plate_serial="",
            rear_plate_serial="",
            gps_tracker_id="",
        )

        # Create provisioned kit inventory to verify auto-prefill
        InstallationKit.objects.create(
            kit_code="IK-UMA300P",
            registration_number="UMA 300P",
            front_plate="001182812",
            rear_plate="001182813",
            gps_tracker="8BAE470808CF",
        )

    def test_get_auto_prefills_hardware_from_kit_and_returns_step1_form(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})

        mock_step1_data = {
            "success": True,
            "order_uuid": self.order.itms_order_uuid,
            "csrf_token": "fresh-page-token-999",
            "front_plate": {
                "selected_id": "uuid-fp-opt",
                "selected_text": "001182812",
                "options": [{"value": "uuid-fp-opt", "text": "001182812", "serial": "001182812"}],
            },
            "rear_plate": {
                "selected_id": "uuid-rp-opt",
                "selected_text": "001182813",
                "options": [{"value": "uuid-rp-opt", "text": "001182813", "serial": "001182813"}],
            },
            "tracker": {
                "selected_id": "uuid-tr-opt",
                "selected_text": "8BAE470808CF",
                "options": [{"value": "uuid-tr-opt", "text": "8BAE470808CF", "serial": "8BAE470808CF"}],
            },
        }

        with patch("core.services.itms_web_client.ITMSWebClient.detect_order_stage") as mock_stage, \
             patch("core.services.itms_web_client.ITMSWebClient.fetch_installation_step1") as mock_fetch, \
             patch.object(ITMSWebSessionData, "is_cookie_valid", return_value=True):
            mock_stage.return_value = {"success": True, "stage": "STAGE_1_INSTALLATION", "order_uuid": self.order.itms_order_uuid}
            mock_fetch.return_value = mock_step1_data

            resp = self.http_client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        # Verify hardware was pre-filled
        self.assertEqual(data["order"]["front_plate_serial"], "001182812")
        self.assertEqual(data["order"]["rear_plate_serial"], "001182813")
        self.assertEqual(data["order"]["gps_tracker_id"], "8BAE470808CF")
        # Verify step1_form payload returned for UI dropdown auto-selection
        self.assertIsNotNone(data.get("step1_form"))
        self.assertEqual(data["step1_form"]["csrf_token"], "fresh-page-token-999")

    def test_save_step1_resolves_serials_and_passes_fresh_csrf(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})

        mock_step1_info = {
            "success": True,
            "order_uuid": self.order.itms_order_uuid,
            "csrf_token": "fresh-csrf-abc-123",
            "front_plate": {
                "options": [{"value": "11111111-2222-3333-4444-555555555555", "text": "001182812", "serial": "001182812"}],
            },
            "rear_plate": {
                "options": [{"value": "22222222-3333-4444-5555-666666666666", "text": "001182813", "serial": "001182813"}],
            },
            "tracker": {
                "options": [{"value": "33333333-4444-5555-6666-777777777777", "text": "8BAE470808CF", "serial": "8BAE470808CF"}],
            },
        }

        with patch("core.services.itms_web_client.ITMSWebClient.fetch_installation_step1", return_value=mock_step1_info), \
             patch("core.services.itms_web_client.ITMSWebClient.submit_installation_step1") as mock_submit:
            mock_submit.return_value = {
                "success": True,
                "step": 1,
                "order_uuid": self.order.itms_order_uuid,
            }

            resp = self.http_client.post(url, {
                "action": "save_step1",
                "front_plate_id": "001182812",
                "rear_plate_id": "001182813",
                "tracker_id": "8BAE470808CF",
                "dry_run": "true",
            })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        # Verify submit_installation_step1 was called with RESOLVED UUIDs and FRESH CSRF TOKEN!
        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args[1]
        self.assertEqual(call_kwargs["front_plate_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(call_kwargs["back_plate_id"], "22222222-3333-4444-5555-666666666666")
        self.assertEqual(call_kwargs["tracker_id"], "33333333-4444-5555-6666-777777777777")
        self.assertEqual(call_kwargs["csrf_token"], "fresh-csrf-abc-123")

    def test_1_click_direct_submit_carries_hardware_serials(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        front_img = _create_test_image("FRONT")
        rear_img = _create_test_image("REAR")

        with patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(
                pair_id=1,
                success=True,
                token="1CLICK-PASS-TOKEN",
                dry_run=True,
            )

            resp = self.http_client.post(url, {
                "action": "submit",
                "front_plate_id": "001182812",
                "rear_plate_id": "001182813",
                "tracker_id": "8BAE470808CF",
                "front_photo": front_img,
                "rear_photo": rear_img,
                "dry_run": "true",
            })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

        # Verify hardware serials were saved to order
        self.order.refresh_from_db()
        self.assertEqual(self.order.front_plate_serial, "001182812")
        self.assertEqual(self.order.rear_plate_serial, "001182813")
        self.assertEqual(self.order.gps_tracker_id, "8BAE470808CF")

        # Verify hardware was passed into submit_pair
        mock_sub.assert_called_once()
        sub_kwargs = mock_sub.call_args[1]
        self.assertEqual(sub_kwargs["front_plate_id"], "001182812")
        self.assertEqual(sub_kwargs["rear_plate_id"], "001182813")
        self.assertEqual(sub_kwargs["tracker_id"], "8BAE470808CF")

    def test_quick_install_fast_tracks_vision_and_marks_verified(self):
        url = reverse("core:api_itms_order_install", kwargs={"order_ident": self.order.order_number})
        front_img = _create_test_image("FRONT")
        rear_img = _create_test_image("REAR")

        with patch("core.services.vault_service.process_single_image_vision") as mock_vision, \
             patch("core.services.submission_worker.submit_pair") as mock_sub:
            mock_sub.return_value = SubmissionOutcome(
                pair_id=1,
                success=True,
                token="FAST-TRACK-TOKEN",
                dry_run=True,
            )

            resp = self.http_client.post(url, {
                "action": "submit",
                "front_photo": front_img,
                "rear_photo": rear_img,
                "dry_run": "true",
            })

        self.assertEqual(resp.status_code, 200)
        # Verify heavy blocking vision was bypassed to prevent freeze
        mock_vision.assert_not_called()
        # Verify pair was created and marked approved with order registration
        pair = VehicleInstallationPair.objects.filter(order=self.order).first()
        self.assertIsNotNone(pair)
        self.assertEqual(pair.registration_number_detected, self.order.registration_number)
        self.assertEqual(pair.front_image.status, EvidenceImage.Status.PLATE_DETECTED)
        self.assertEqual(pair.rear_image.status, EvidenceImage.Status.PLATE_DETECTED)

    def test_kit_sync_honors_200_pages_from_tui_config(self):
        from core.services.order_sync import OrderSyncService
        sync_svc = OrderSyncService()

        with patch.object(config_service, "get_setting") as mock_get_setting, \
             patch.object(sync_svc.client, "fetch_installation_kits") as mock_fetch, \
             patch.object(sync_svc.client, "sync_kits_to_local_db") as mock_sync:
            mock_get_setting.side_effect = lambda k, default=None, **kwargs: 200 if k == "crawl.max_kit_pages" else (default if default is not None else True)
            mock_fetch.return_value = {
                "success": True,
                "kits": [{"kit_code": f"IK-{i}", "registration_number": f"UMA {i}A"} for i in range(20)],
                "has_next_page": True,
            }
            mock_sync.return_value = {"created": 20, "updated": 0}

            # Call without max_pages so it loads from crawl.max_kit_pages (200)
            res = sync_svc.sync_installation_kits_scoped(max_pages=None, force=True)

            self.assertTrue(res.get("success"))
            # Fetch should have continued past page 100 up to page 200
            self.assertEqual(res.get("pages_fetched"), 200)
            self.assertEqual(mock_fetch.call_count, 200)

    def test_execute_workflow_succeeds_even_if_archive_indexing_is_pending(self):
        client = get_web_client()
        with patch.object(client, "detect_order_stage") as mock_detect, \
             patch.object(client, "fetch_installation_step1") as mock_s1_fetch, \
             patch.object(client, "submit_installation_step1") as mock_s1_sub, \
             patch.object(client, "fetch_approve_step2") as mock_s2_fetch, \
             patch.object(client, "upload_installation_step2_photos") as mock_s2_up, \
             patch.object(client, "fetch_confirmation_step3") as mock_s3_fetch, \
             patch.object(client, "submit_confirmation_step3") as mock_s3_sub, \
             patch.object(client, "verify_order_archived") as mock_verif:

            mock_detect.return_value = {
                "success": True,
                "stage": "STAGE_1_INSTALLATION",
                "order_uuid": "dummy-order-uuid-1234",
            }
            mock_s1_fetch.return_value = {"success": True, "csrf_token": "token-1"}
            mock_s1_sub.return_value = {"success": True}
            mock_s2_fetch.return_value = {"success": True, "csrf_token": "token-2"}
            mock_s2_up.return_value = {"success": True, "step3_ready": True}
            mock_s3_fetch.return_value = {"success": True, "csrf_token": "token-3"}
            mock_s3_sub.return_value = {"success": True, "redirect_url": "/installation-orders/index"}
            # Archive verification times out on immediate check
            mock_verif.return_value = {"success": False, "verified": False, "error": "Order not confirmed in ITMS Archive."}

            res = client.execute_installation_order_workflow(
                order_identifier=self.order.order_number,
                front_photo_path="dummy_front.jpg",
                rear_photo_path="dummy_rear.jpg",
                dry_run=False,
                submit_step3=True,
            )

            # Workflow should succeed as Step 3 was submitted, rather than reporting failure!
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("is_finalized"))
            self.assertTrue(res.get("pending_archive_confirmation"))
