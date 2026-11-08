"""
Unit tests for Queue Tab order linking, refined search matching (e.g. numeric query precision),
and conflict pair promotion.
"""
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    OperatorITMSSession,
    VehicleInstallationPair,
)


class QueueOrderLinkingSearchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_op",
            password="password123",
            first_name="Test",
            last_name="Operator",
        )
        self.session = OperatorITMSSession.objects.create(
            user=self.user,
            itms_email="operator@spiro.ug",
            active_bond_code="AGM",
            is_authenticated=True,
            expires_at=timezone.now() + timedelta(days=1),
            cookies={"_identity-frontend": "mock-token-abc-123"},
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-LINK-001",
            source_type=IngestionBatch.SourceType.WEB,
        )
        self.front_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_front_692",
            original_source_path="/test/front_692.jpg",
            vault_file="vault/front_692.jpg",
            detected_plate="UMA692QP",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        self.rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_rear_692",
            original_source_path="/test/rear_692.jpg",
            vault_file="vault/rear_692.jpg",
            detected_plate="UMA692QP",
            orientation=EvidenceImage.Orientation.REAR,
        )

    def test_short_numeric_search_matches_only_plate_or_order_not_vin(self):
        """Short numeric query '600' must match plate or order number, not random VIN substring."""
        # Order A: plate has 600
        order_match = InstallationOrder.objects.create(
            order_number="ORD-10600",
            registration_number="UMA 600AA",
            vin="L6XRZS1A4T0090111",
            status=InstallationOrder.Status.PENDING,
        )
        # Order B: VIN has 600, but plate and order do NOT have 600
        order_false = InstallationOrder.objects.create(
            order_number="ORD-99999",
            registration_number="UMA 123ZZ",
            vin="L6XRZS1A4T0090600",  # Contains 600 in 17-char VIN
            status=InstallationOrder.Status.PENDING,
        )

        res = self.client.get(reverse("core:api_orders_list") + "?search=600")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        order_ids = [o["id"] for o in data["orders"]]
        self.assertIn(order_match.id, order_ids)
        self.assertNotIn(order_false.id, order_ids)

    def test_nonexistent_search_returns_empty_orders_list(self):
        """When an order does not exist, return an empty list without returning unrelated records."""
        res = self.client.get(reverse("core:api_orders_list") + "?search=UMA999NONEXISTENT")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["orders"]), 0)

    def test_order_with_ready_for_approve_can_be_manually_linked_and_approved(self):
        """
        Orders synced from ITMS with status 'Ready for approve' must be eligible for manual linking
        and auto-promotion to APPROVED, resolving CONFLICT pairs without ORDER_NOT_AVAILABLE errors.
        """
        order = InstallationOrder.objects.create(
            order_number="PO-UMA692QP-001",
            registration_number="UMA 692QP",
            vin="L6XRZS1A4T0090404",
            order_status="Ready for approve",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA692QP",
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        )

        # Manually link the order to the conflict pair
        res = self.client.post(
            reverse("core:api_pair_action", args=[pair.id]),
            {"action": "link_order", "order_id": order.id},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

        pair.refresh_from_db()
        self.assertEqual(pair.order_id, order.id)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)

    def test_approve_action_auto_binds_matching_pending_order(self):
        """
        When operator approves a complete pair whose order was missing at capture time,
        the approve action auto-binds the matching open order and approves the pair.
        """
        order = InstallationOrder.objects.create(
            order_number="PO-UMA692QP-002",
            registration_number="UMA 692QP",
            order_status="Ready for approve",
            status=InstallationOrder.Status.PENDING,
        )
        pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA 692QP",
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        )

        res = self.client.post(
            reverse("core:api_pair_action", args=[pair.id]),
            {"action": "approve"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

        pair.refresh_from_db()
        self.assertEqual(pair.order_id, order.id)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)

    def test_pairs_list_orders_chronologically_by_created_at_not_scrambled_by_updates(self):
        """
        Pairs in the queue must follow a stable chronological order (created_at, id),
        so operators advance predictably top-to-bottom even after updating later pairs.
        """
        pair1 = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA 101AA",
            front_image=self.front_img,
            is_complete=True,
        )
        pair2 = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA 102BB",
            front_image=self.front_img,
            is_complete=True,
        )
        # Update pair1 to bump updated_at
        VehicleInstallationPair.objects.filter(id=pair1.id).update(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)

        res = self.client.get(reverse("core:api_pairs_list"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        pair_ids = [p["id"] for p in data["pairs"]]
        # pair1 was created before pair2, so pair1 must precede pair2 despite pair1 having a newer updated_at
        self.assertLess(pair_ids.index(pair1.id), pair_ids.index(pair2.id))

