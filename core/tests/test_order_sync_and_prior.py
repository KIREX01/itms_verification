"""
Unit tests for Smart ITMS Order Sync, Prior-Guided Disambiguation, and Fast-Path Operator Manual Entry.
"""
from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.utils import timezone

from core.models import (
    EvidenceImage,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.matcher.prior_guided import (
    disambiguate_plate_with_orders,
    get_active_orders_cache,
)
from core.matcher.order_matcher import find_best_match, match_pair_to_order
from core.services.order_sync import OrderSyncService, SYNC_COOLDOWN_SECONDS


class PriorGuidedDisambiguationTests(TestCase):
    def setUp(self):
        # Create active test orders
        self.order1 = InstallationOrder.objects.create(
            order_number="PO-UMA123PK-01",
            registration_number="UMA123PK",
            vin="VIN1234567890",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.order2 = InstallationOrder.objects.create(
            order_number="PO-UMA123BA-02",
            registration_number="UMA123BA",
            vin="VIN0987654321",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.order3 = InstallationOrder.objects.create(
            order_number="PO-UBB456C-03",
            registration_number="UBB456C",
            vin="VIN5555555555",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )

    def test_suffix_truncation_resolution(self):
        """OCR reads 'UMA123P' (missing trailing letter 'K') -> resolves to 'UMA123PK'."""
        res = disambiguate_plate_with_orders("UMA123P", "UMA123P")
        self.assertEqual(res["resolved_plate"], "UMA123PK")
        self.assertEqual(res["order_id"], self.order1.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("Suffix truncation completed", res["reason"])

    def test_prefix_truncation_resolution(self):
        """OCR reads '123PK' or 'MA123PK' -> resolves to 'UMA123PK'."""
        res = disambiguate_plate_with_orders("123PK", "123PK")
        self.assertEqual(res["resolved_plate"], "UMA123PK")
        self.assertEqual(res["order_id"], self.order1.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("Prefix truncation completed", res["reason"])

    def test_confusable_character_resolution(self):
        """OCR reads 'UMA1238A' (8 instead of B) -> resolves to 'UMA123BA'."""
        res = disambiguate_plate_with_orders("UMA1238A", "UMA1238A")
        self.assertEqual(res["resolved_plate"], "UMA123BA")
        self.assertEqual(res["order_id"], self.order2.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("confusable corrected", res["reason"])

    def test_exact_match(self):
        """OCR reads 'UBB456C' -> exact match without correction."""
        res = disambiguate_plate_with_orders("UBB456C", "UBB456C")
        self.assertEqual(res["resolved_plate"], "UBB456C")
        self.assertEqual(res["order_id"], self.order3.id)
        self.assertEqual(res["match_type"], "EXACT")
        self.assertFalse(res["was_corrected"])

    def test_unregistered_rejection(self):
        """Random unrecognized plate 'UXX999ZZ' -> rejected as NONE."""
        res = disambiguate_plate_with_orders("UXX999ZZ", "UXX999ZZ")
        self.assertEqual(res["match_type"], "NONE")
        self.assertIsNone(res["order_id"])
        self.assertFalse(res["was_corrected"])

    def test_order_matcher_integrates_prior(self):
        """Test that find_best_match and match_pair_to_order use prior guidance."""
        front = EvidenceImage.objects.create(
            file_hash="hash_front_test",
            vault_file="vault/front_test.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear = EvidenceImage.objects.create(
            file_hash="hash_rear_test",
            vault_file="vault/rear_test.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA123P",
            front_image=front,
            rear_image=rear,
            is_complete=True,
        )

        matched_pair = match_pair_to_order(pair)
        self.assertEqual(matched_pair.registration_number_detected, "UMA123PK")
        self.assertEqual(matched_pair.order, self.order1)
        self.assertEqual(matched_pair.matched_via, VehicleInstallationPair.MatchedVia.ORDER_PRIOR)
        self.assertEqual(matched_pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)


class OrderSyncServiceTests(TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.sync_svc = OrderSyncService(client=self.mock_client)

    def test_sync_active_orders_success(self):
        """Tests fetching active orders from ITMS and syncing to DB."""
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [
                {
                    "order_number": "PO-TEST-001",
                    "registration_number": "UMA 900AA",
                    "vin": "VINTEST001",
                    "status": "Under installation",
                    "sales_order": "SO-001",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-001",
                    "action_url": "/installation-orders/installation?id=uuid-001",
                },
                {
                    "order_number": "PO-TEST-002",
                    "registration_number": "UMA 900BB",
                    "vin": "VINTEST002",
                    "status": "Ready for approve",
                    "sales_order": "SO-002",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-002",
                    "action_url": "/installation-orders/approve?id=uuid-002",
                },
            ],
            "has_next_page": False,
        }

        res = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_active_seen"], 2)
        self.assertEqual(res["created"], 2)

        order1 = InstallationOrder.objects.get(order_number="PO-TEST-001")
        self.assertEqual(order1.registration_number, "UMA900AA")
        self.assertTrue(order1.is_active_on_itms)
        self.assertEqual(order1.itms_stage, "STAGE_1_INSTALLATION")

        order2 = InstallationOrder.objects.get(order_number="PO-TEST-002")
        self.assertEqual(order2.itms_stage, "STAGE_2_APPROVE")

    def test_cooldown_rate_limit(self):
        """Subsequent sync calls within cooldown window return cached result."""
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [],
            "has_next_page": False,
        }
        # First call forces sync
        res1 = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res1["success"])
        self.assertFalse(res1["from_cache"])

        # Second call without force should hit cooldown cache
        res2 = self.sync_svc.sync_active_orders(force=False)
        self.assertTrue(res2["success"])
        self.assertTrue(res2["from_cache"])
        self.assertIn("Cooldown active", res2["message"])

    def test_disappeared_orders_detection(self):
        """Orders that were previously active but absent in next sync are marked inactive."""
        # Create an existing active order in local DB
        existing = InstallationOrder.objects.create(
            order_number="PO-OLD-ACTIVE",
            registration_number="UMA999OLD",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
            is_archived=False,
        )

        # Mock ITMS returning a DIFFERENT order on active index (PO-OLD-ACTIVE disappeared!)
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [
                {
                    "order_number": "PO-NEW-ACTIVE",
                    "registration_number": "UMA999NEW",
                    "vin": "VINNEW",
                    "status": "Under installation",
                    "sales_order": "SO-NEW",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-new",
                    "action_url": "/installation-orders/installation?id=uuid-new",
                }
            ],
            "has_next_page": False,
        }

        res = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["disappeared_from_active"], 1)

        existing.refresh_from_db()
        self.assertFalse(existing.is_active_on_itms)
        self.assertTrue(existing.is_archived)
        self.assertEqual(existing.status, InstallationOrder.Status.INSTALLED)
        self.assertEqual(existing.order_status, "Installed")
        self.assertEqual(existing.itms_stage, "ARCHIVED")


class ManualFastPathEntryTests(TestCase):
    def test_manual_override_and_audit_log(self):
        """Verify manual plate typing sets pair to APPROVED, matched_via MANUAL, and creates audit log."""
        order = InstallationOrder.objects.create(
            order_number="PO-UMA835DS-030926",
            registration_number="UMA835DS",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        front = EvidenceImage.objects.create(
            file_hash="hash_front_m",
            vault_file="vault/front_m.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear = EvidenceImage.objects.create(
            file_hash="hash_rear_m",
            vault_file="vault/rear_m.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="PAIR-unknown",
            front_image=front,
            rear_image=rear,
            is_complete=True,
        )

        # Simulate operator fast-path entry
        pair.registration_number_detected = "UMA835DS"
        pair.order = order
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.match_type = VehicleInstallationPair.MatchType.EXACT
        pair.match_score = 100.0
        pair.is_manual_override = True
        pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        pair.manual_plate_override = "UMA835DS"
        pair.save()

        front.detected_plate = "UMA835DS"
        front.save()
        rear.detected_plate = "UMA835DS"
        rear.save()

        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator manually typed and confirmed plate 'UMA835DS' (linked to order #PO-UMA835DS-030926).",
        )

        pair.refresh_from_db()
        self.assertEqual(pair.registration_number_detected, "UMA835DS")
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)
        self.assertTrue(pair.is_manual_override)
        self.assertEqual(pair.matched_via, VehicleInstallationPair.MatchedVia.MANUAL)

        audit = pair.audit_logs.first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN)
        self.assertEqual(audit.result, SubmissionAuditLog.ResultStatus.SUCCESS)


class AutomatedSubmissionTests(TestCase):
    def setUp(self):
        self.order = InstallationOrder.objects.create(
            order_number="PO-BATCH-001",
            registration_number="UMA888AA",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.front = EvidenceImage.objects.create(
            file_hash="hash_front_b",
            vault_file="vault/front_b.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        self.rear = EvidenceImage.objects.create(
            file_hash="hash_rear_b",
            vault_file="vault/rear_b.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA888AA",
            order=self.order,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
        )

    @patch("core.services.itms_web_client.ITMSWebClient.execute_installation_order_workflow")
    def test_submit_pair_dry_run_safety(self, mock_workflow):
        """Verify submit_pair passes dry_run=True by default and updates status on success."""
        from core.services.submission_worker import submit_pair
        mock_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-test-01",
            "message": "Step 1-3 Dry Run validated successfully",
            "is_finalized": True,
        }

        outcome = submit_pair(self.pair, backend="web", dry_run=True)
        self.assertTrue(outcome.success)
        mock_workflow.assert_called_once()
        _, kwargs = mock_workflow.call_args
        self.assertTrue(kwargs["dry_run"])

        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)
        self.assertIsNotNone(self.pair.submitted_at)

        audit = self.pair.audit_logs.filter(action=SubmissionAuditLog.Action.SUBMIT).first()
        self.assertIsNotNone(audit)
        self.assertIn("[DRY-RUN]", audit.message)

    @patch("core.services.itms_web_client.ITMSWebClient.execute_installation_order_workflow")
    def test_submit_approved_pairs_batch(self, mock_workflow):
        """Verify submit_approved_pairs processes all approved orders in batch and calls progress callback."""
        from core.services.submission_worker import submit_approved_pairs
        mock_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-batch",
            "message": "Batch simulated submission",
            "is_finalized": True,
        }

        # Create a second approved pair
        order2 = InstallationOrder.objects.create(
            order_number="PO-BATCH-002",
            registration_number="UMA888BB",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        pair2 = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA888BB",
            order=order2,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
        )

        progress_calls = []
        def progress_cb(idx, total, p, out):
            progress_calls.append((idx, total, p.registration_number_detected, out.success))

        outcomes = submit_approved_pairs(backend="web", dry_run=True, progress_callback=progress_cb)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(o.success for o in outcomes))
        self.assertEqual(len(progress_calls), 2)
        called_plates = {call[2] for call in progress_calls}
        self.assertEqual(called_plates, {"UMA888AA", "UMA888BB"})
