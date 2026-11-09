"""
Comprehensive verification test suite for Tab 5: History & Audit Logs.
Tests:
- Authentication requirement.
- Date scoping (TODAY vs ALL).
- Distinct filter categories (ALL, SUBMISSIONS, APPROVALS, OVERRIDES, ISSUES, STOCK).
- Normalized evidence photo URLs for front and rear images with /media/ prefix.
- Graceful null/missing image handling for pairs and non-pair system/stock logs.
"""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)

User = get_user_model()


class HistoryTabTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="history_officer",
            password="testpassword123",
            email="officer@ugandaclosing.test",
        )
        self.client.force_login(self.user)

        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-HIST-001",
            operator=self.user,
        )

        self.order = InstallationOrder.objects.create(
            order_number="ORD-HIST-001",
            registration_number="UMA100AB",
            status="READY",
        )

        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA100AB",
            order=self.order,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )

    def test_history_list_requires_authentication(self):
        """Unauthenticated GET /api/history/ must return 401."""
        self.client.logout()
        res = self.client.get(reverse("core:api_history_list"))
        self.assertEqual(res.status_code, 401)
        self.assertFalse(res.json()["success"])

    def test_history_date_scoping(self):
        """GET /api/history/ correctly scopes records between TODAY and ALL."""
        prior_pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA200CD",
            order=self.order,
            verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED,
        )

        today_log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Submitted in today's shift",
        )

        prior_log = SubmissionAuditLog.objects.create(
            pair=prior_pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Submitted 4 days ago",
        )
        four_days_ago = timezone.now() - timedelta(days=4)
        SubmissionAuditLog.objects.filter(id=prior_log.id).update(timestamp=four_days_ago)

        # 1. date_scope=TODAY
        res_today = self.client.get(reverse("core:api_history_list") + "?date_scope=TODAY")
        self.assertEqual(res_today.status_code, 200)
        data_today = res_today.json()
        self.assertTrue(data_today["success"])
        ids_today = [i["id"] for i in data_today["items"]]
        self.assertIn(f"log_{today_log.id}", ids_today)
        self.assertNotIn(f"log_{prior_log.id}", ids_today)

        # 2. date_scope=ALL
        res_all = self.client.get(reverse("core:api_history_list") + "?date_scope=ALL")
        self.assertEqual(res_all.status_code, 200)
        data_all = res_all.json()
        ids_all = [i["id"] for i in data_all["items"]]
        self.assertIn(f"log_{today_log.id}", ids_all)
        self.assertIn(f"log_{prior_log.id}", ids_all)

    def test_history_distinct_filter_categories(self):
        """GET /api/history/?filter=... properly partitions events into distinct audit trails."""
        # 1. Submissions
        sub_log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Final submission transmitted to ITMS",
        )
        # 2. Approvals
        app_log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator approved vehicle pair",
        )
        # 3. Overrides
        ovr_log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Plate override applied",
        )
        # 4. Issues & Failures
        fail_log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.VALIDATE,
            result=SubmissionAuditLog.ResultStatus.FAILURE,
            message="Validation mismatch detected",
        )
        # 5. Stock & Audits
        stock_log = SubmissionAuditLog.objects.create(
            action=SubmissionAuditLog.Action.STOCK_LEDGER_UPDATED,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            simulated_token="20261109",
            message="Closing stock balance ledger updated",
        )

        # Test SUBMISSIONS
        res = self.client.get(reverse("core:api_history_list") + "?filter=SUBMISSIONS&date_scope=TODAY")
        ids = [i["id"] for i in res.json()["items"]]
        self.assertIn(f"log_{sub_log.id}", ids)
        self.assertNotIn(f"log_{app_log.id}", ids)
        self.assertNotIn(f"log_{fail_log.id}", ids)
        self.assertNotIn(f"log_{stock_log.id}", ids)

        # Test APPROVALS
        res = self.client.get(reverse("core:api_history_list") + "?filter=APPROVALS&date_scope=TODAY")
        ids = [i["id"] for i in res.json()["items"]]
        self.assertIn(f"log_{app_log.id}", ids)
        self.assertNotIn(f"log_{sub_log.id}", ids)
        self.assertNotIn(f"log_{ovr_log.id}", ids)

        # Test OVERRIDES
        res = self.client.get(reverse("core:api_history_list") + "?filter=OVERRIDES&date_scope=TODAY")
        ids = [i["id"] for i in res.json()["items"]]
        self.assertIn(f"log_{ovr_log.id}", ids)
        self.assertNotIn(f"log_{sub_log.id}", ids)

        # Test ISSUES
        res = self.client.get(reverse("core:api_history_list") + "?filter=ISSUES&date_scope=TODAY")
        ids = [i["id"] for i in res.json()["items"]]
        self.assertIn(f"log_{fail_log.id}", ids)
        self.assertNotIn(f"log_{app_log.id}", ids)

        # Test STOCK
        res = self.client.get(reverse("core:api_history_list") + "?filter=STOCK&date_scope=TODAY")
        ids = [i["id"] for i in res.json()["items"]]
        self.assertIn(f"log_{stock_log.id}", ids)
        self.assertNotIn(f"log_{sub_log.id}", ids)

    def test_history_evidence_photo_urls_normalization(self):
        """GET /api/history/ provides normalized, accessible /media/ URLs for evidence photos."""
        front_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_front_hist_test",
            original_source_path="/raw/front.jpg",
            vault_file="vault/2026-11-09/front_vault.jpg",
            thumbnail_file="vault/2026-11-09/front_thumb.webp",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_rear_hist_test",
            original_source_path="/raw/rear.jpg",
            vault_file="media/vault/2026-11-09/rear_vault.jpg",  # Already has media/
            thumbnail_file="media/vault/2026-11-09/rear_thumb.webp",
            orientation=EvidenceImage.Orientation.REAR,
        )

        self.pair.front_image = front_img
        self.pair.rear_image = rear_img
        self.pair.save()

        log = SubmissionAuditLog.objects.create(
            pair=self.pair,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Verified with dual evidence photos",
        )

        res = self.client.get(reverse("core:api_history_list") + "?filter=ALL&date_scope=TODAY")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        item = next(i for i in data["items"] if i["id"] == f"log_{log.id}")

        # Ensure single /media/ prefix without duplicates or backslashes
        self.assertEqual(item["front_url"], "/media/vault/2026-11-09/front_vault.jpg")
        self.assertEqual(item["front_thumb_url"], "/media/vault/2026-11-09/front_thumb.webp")
        self.assertEqual(item["rear_url"], "/media/vault/2026-11-09/rear_vault.jpg")
        self.assertEqual(item["rear_thumb_url"], "/media/vault/2026-11-09/rear_thumb.webp")

    def test_history_null_image_handling(self):
        """Records without images (e.g., Stock Ledger or pair with missing photo) return None without crashing."""
        stock_log = SubmissionAuditLog.objects.create(
            action=SubmissionAuditLog.Action.STOCK_REPORT_EMAILED,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            simulated_token="20261109",
            message="Shift Stock Report dispatched via email",
        )

        res = self.client.get(reverse("core:api_history_list") + "?filter=STOCK&date_scope=TODAY")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        item = next(i for i in data["items"] if i["id"] == f"log_{stock_log.id}")

        self.assertIsNone(item["front_url"])
        self.assertIsNone(item["front_thumb_url"])
        self.assertIsNone(item["rear_url"])
        self.assertIsNone(item["rear_thumb_url"])
        self.assertEqual(item["plate"], "SHIFT_20261109")
        self.assertEqual(item["order_number"], "EMAIL_DISPATCH")
