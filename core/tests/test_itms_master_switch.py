"""
Tests for ITMS Master Service Switch and Admin Registrations.
"""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    FacilitySyncMetadata,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    OperatorITMSSession,
    OperatorProfile,
    SafeAuditScan,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import config_service
from core.services.itms_web_client import ITMSWebClient
from core.services.submission_worker import submit_pair

User = get_user_model()


class AdminRegistrationTests(TestCase):
    def test_all_expected_models_registered_in_admin(self):
        registered = admin.site._registry
        expected_models = [
            Session,
            OperatorITMSSession,
            OperatorProfile,
            FacilitySyncMetadata,
            IngestionBatch,
            InstallationOrder,
            InstallationKit,
            EvidenceImage,
            VehicleInstallationPair,
            SubmissionAuditLog,
            StockDelivery,
            StockBondTransfer,
            StockDispatchScan,
            StockReturnScan,
            SafeAuditScan,
            DailyStockLedger,
        ]
        for model in expected_models:
            self.assertIn(
                model,
                registered,
                f"Model {model.__name__} should be registered in admin.site",
            )

    def test_session_admin_get_decoded_user(self):
        user = User.objects.create_user(username="testadmin", email="test@example.com")
        # Create session
        session = Session.objects.create(
            session_key="test_session_key_123",
            session_data=Session.objects.encode({"_auth_user_id": str(user.pk)}),
            expire_date=timezone.now() + timezone.timedelta(days=1),
        )
        session_admin = admin.site._registry[Session]
        display_user = session_admin.get_decoded_user(session)
        self.assertIn("testadmin", display_user)
        self.assertFalse(session_admin.is_expired(session))


class ITMSMasterSwitchTests(TestCase):
    def setUp(self):
        # Ensure enabled at start of test
        config_service.set_itms_enabled(True)

    def tearDown(self):
        # Reset to enabled
        config_service.set_itms_enabled(True)

    def test_toggle_master_switch(self):
        self.assertTrue(config_service.is_itms_enabled())

        config_service.set_itms_enabled(False, paused_by="Tester", reason="Unit test pause")
        self.assertFalse(config_service.is_itms_enabled())

        cfg = config_service.load_config()
        self.assertEqual(cfg["itms"]["paused_by"], "Tester")
        self.assertEqual(cfg["itms"]["pause_reason"], "Unit test pause")
        self.assertTrue(bool(cfg["itms"]["paused_at"]))

        config_service.set_itms_enabled(True)
        self.assertTrue(config_service.is_itms_enabled())
        cfg = config_service.load_config()
        self.assertEqual(cfg["itms"]["paused_at"], "")

    def test_web_client_login_blocked_when_paused(self):
        config_service.set_itms_enabled(False)
        client = ITMSWebClient()
        success, msg, data = client.login("user@example.com", "secret")
        self.assertFalse(success)
        self.assertIn("PAUSED", msg)
        self.assertTrue(data.get("paused"))

    def test_submit_pair_holds_locally_when_paused(self):
        batch = IngestionBatch.objects.create(batch_id="TEST-BATCH-001", source_label="Test")
        front = EvidenceImage.objects.create(batch=batch, file_hash="hash_front", detected_plate="UBA123A")
        rear = EvidenceImage.objects.create(batch=batch, file_hash="hash_rear", detected_plate="UBA123A")
        order = InstallationOrder.objects.create(order_number="PO-123", registration_number="UBA123A")
        pair = VehicleInstallationPair.objects.create(
            order=order,
            front_image=front,
            rear_image=rear,
            registration_number_detected="UBA123A",
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
        )

        config_service.set_itms_enabled(False)

        outcome = submit_pair(pair, backend="web")
        self.assertFalse(outcome.success)
        self.assertIn("PAUSED", outcome.error)

        # Ensure pair was NOT marked FAILED, but held in APPROVED
        pair.refresh_from_db()
        self.assertEqual(
            pair.verification_status,
            VehicleInstallationPair.VerificationStatus.APPROVED,
            "Pair should remain in APPROVED status when ITMS is paused",
        )
