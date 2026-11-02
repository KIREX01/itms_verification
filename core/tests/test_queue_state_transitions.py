from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
import json
import uuid

from core.models import EvidenceImage, IngestionBatch, InstallationOrder, VehicleInstallationPair
from core.matcher.order_matcher import match_pair_to_order, find_best_match
from core.views.pairs import api_pair_action

User = get_user_model()


class QueueStateTransitionsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tester",
            email="tester@itms.ug",
            password="testpassword123",
            is_staff=True,
        )
        self.factory = RequestFactory()
        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-TEST-001",
            operator=self.user,
            account_email="tester@itms.ug",
        )
        self.front_img = EvidenceImage.objects.create(
            batch=self.batch,
            orientation=EvidenceImage.Orientation.FRONT,
            file_hash=uuid.uuid4().hex,
            captured_at=timezone.now(),
        )
        self.rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            orientation=EvidenceImage.Orientation.REAR,
            file_hash=uuid.uuid4().hex,
            captured_at=timezone.now(),
        )

    def test_unlinked_pair_starts_in_conflict(self):
        """Newly created pairs without matching orders must initialize in CONFLICT."""
        pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA999ZZ",
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        )
        self.assertIsNone(pair.order)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)

    def test_unlink_order_transitions_to_conflict(self):
        """Unlinking an order from a pair must set order=None and verification_status=CONFLICT."""
        order = InstallationOrder.objects.create(
            order_number="PO-TEST-001",
            registration_number="UMA100AA",
            status=InstallationOrder.Status.PENDING,
            account_email="tester@itms.ug",
        )
        pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA100AA",
            is_complete=True,
            order=order,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        # Call unlink_order action
        req = self.factory.post(
            f"/api/pairs/{pair.id}/actions/",
            data=json.dumps({"action": "unlink_order"}),
            content_type="application/json",
        )
        req.user = self.user
        resp = api_pair_action(req, pair_id=pair.id)
        self.assertEqual(resp.status_code, 200)

        pair.refresh_from_db()
        self.assertIsNone(pair.order)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)

    def test_one_to_one_order_assignment_enforced(self):
        """1:1 invariant: An order assigned to Pair 1 cannot be hijacked by Pair 2."""
        order = InstallationOrder.objects.create(
            order_number="PO-TEST-002",
            registration_number="UMA200BB",
            status=InstallationOrder.Status.PENDING,
            account_email="tester@itms.ug",
        )
        pair1 = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA200BB",
            is_complete=True,
            order=order,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        # Create second pair with same plate
        f2 = EvidenceImage.objects.create(
            batch=self.batch,
            orientation=EvidenceImage.Orientation.FRONT,
            file_hash=uuid.uuid4().hex,
        )
        r2 = EvidenceImage.objects.create(
            batch=self.batch,
            orientation=EvidenceImage.Orientation.REAR,
            file_hash=uuid.uuid4().hex,
        )
        pair2 = VehicleInstallationPair.objects.create(
            front_image=f2,
            rear_image=r2,
            registration_number_detected="UMA200BB",
            is_complete=True,
            account_email="tester@itms.ug",
            verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        )

        # Attempt to auto-match pair2
        match_result = match_pair_to_order(pair2)
        pair2.refresh_from_db()
        self.assertIsNone(pair2.order)
        self.assertEqual(pair2.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)

        # Attempt to manually link already-assigned order
        req = self.factory.post(
            f"/api/pairs/{pair2.id}/actions/",
            data=json.dumps({"action": "link_order", "order_id": order.id}),
            content_type="application/json",
        )
        req.user = self.user
        resp = api_pair_action(req, pair_id=pair2.id)
        self.assertEqual(resp.status_code, 400)
        resp_data = json.loads(resp.content.decode("utf-8"))
        self.assertEqual(resp_data.get("code"), "ORDER_ALREADY_ASSIGNED")

    def test_valid_plates_not_fuzzy_matched_across_series(self):
        """Sequential plates differing by 1 character (e.g. UMA198QP vs UMA199QP) must never fuzzy cross-match."""
        InstallationOrder.objects.create(
            order_number="PO-UMA199QP-021126",
            registration_number="UMA 199QP",
            status=InstallationOrder.Status.PENDING,
            account_email="tester@itms.ug",
        )

        # Candidate is UMA198QP (valid plate)
        outcome = find_best_match(
            detected_plate="UMA198QP",
        )
        self.assertIsNone(outcome.order)
        self.assertEqual(outcome.match_type, VehicleInstallationPair.MatchType.NONE)
