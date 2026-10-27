from django.test import TestCase

from core.matcher import association, order_matcher
from core.models import EvidenceImage, InstallationOrder, VehicleInstallationPair


def _make_image(plate, orientation, status=EvidenceImage.Status.PLATE_DETECTED, **kwargs):
    n = _make_image._counter = getattr(_make_image, "_counter", 0) + 1
    return EvidenceImage.objects.create(
        file_hash=f"{n:064d}",
        original_source_path=f"/tmp/{n}.jpg",
        vault_file=f"vault/{n}.jpg",
        detected_plate=plate,
        orientation=orientation,
        status=status,
        **kwargs,
    )


class AssociationEngineTests(TestCase):
    def setUp(self):
        InstallationOrder.objects.create(
            order_number="PO-UMA123AA-01",
            registration_number="UMA123AA",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )

    def test_front_and_rear_pair_marks_complete(self):
        _make_image("UMA123AA", EvidenceImage.Orientation.FRONT)
        _make_image("UMA123AA", EvidenceImage.Orientation.REAR)

        summary = association.run_association()

        self.assertEqual(summary.complete_pairs, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UMA123AA")
        self.assertTrue(pair.is_complete)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)
        self.assertIsNotNone(pair.order)
        self.assertEqual(pair.order.order_number, "PO-UMA123AA-01")

    def test_front_and_rear_without_pending_order_marks_conflict(self):
        # When photos are complete but NO pending order exists -> CONFLICT
        _make_image("UXZ999ZZ", EvidenceImage.Orientation.FRONT)
        _make_image("UXZ999ZZ", EvidenceImage.Orientation.REAR)

        summary = association.run_association()

        self.assertEqual(summary.conflicts, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UXZ999ZZ")
        self.assertTrue(pair.is_complete)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)
        self.assertIsNone(pair.order)
        self.assertIn("Order Conflict", pair.operator_note)

    def test_photos_matching_closed_order_flagged_conflict_and_not_linked(self):
        # Order exists but is INSTALLED (closed)
        InstallationOrder.objects.create(
            order_number="PO-CLOSED-99",
            registration_number="UBA999C",
            status=InstallationOrder.Status.INSTALLED,
            order_status="Installed",
            is_archived=True,
            is_active_on_itms=False,
        )
        _make_image("UBA999C", EvidenceImage.Orientation.FRONT)
        _make_image("UBA999C", EvidenceImage.Orientation.REAR)

        summary = association.run_association()

        self.assertEqual(summary.conflicts, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UBA999C")
        self.assertTrue(pair.is_complete)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)
        self.assertIsNone(pair.order)
        self.assertIn("already Installed", pair.operator_note)

    def test_single_image_marks_incomplete(self):
        _make_image("UBB456C", EvidenceImage.Orientation.FRONT)

        summary = association.run_association()

        self.assertEqual(summary.incomplete, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UBB456C")
        self.assertFalse(pair.is_complete)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.INCOMPLETE)

    def test_two_fronts_marks_conflict(self):
        _make_image("UAB111K", EvidenceImage.Orientation.FRONT)
        _make_image("UAB111K", EvidenceImage.Orientation.FRONT)

        summary = association.run_association()

        self.assertEqual(summary.conflicts, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UAB111K")
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)

    def test_duplicate_skipped_images_are_excluded(self):
        _make_image("UCD222LM", EvidenceImage.Orientation.FRONT, status=EvidenceImage.Status.DUPLICATE_SKIPPED)
        _make_image("UCD222LM", EvidenceImage.Orientation.REAR, status=EvidenceImage.Status.DUPLICATE_SKIPPED)

        summary = association.run_association()

        self.assertEqual(summary.groups_processed, 0)

    def test_batch_sequence_association_with_surplus_and_uturn(self):
        from core.models import IngestionBatch
        batch = IngestionBatch.objects.create(
            batch_id="BATCH-UTURN-TEST-01",
            source_label="U-Turn Yard Walk",
        )
        InstallationOrder.objects.create(
            order_number="PO-UMA101A-01",
            registration_number="UMA101A",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        img_r1 = _make_image("UMA101A", EvidenceImage.Orientation.REAR, batch=batch)
        img_r2 = _make_image("UMA102B", EvidenceImage.Orientation.REAR, batch=batch)
        img_f1 = _make_image("UMA101A", EvidenceImage.Orientation.FRONT, batch=batch)

        summary = association.run_association(batch_id=batch.batch_id)
        self.assertGreaterEqual(summary.complete_pairs, 1)

    def test_uturn_single_orientation_skips_premature_incomplete_pairs(self):
        from core.models import IngestionBatch
        batch = IngestionBatch.objects.create(
            batch_id="BATCH-UTURN-SINGLE-01",
            source_label="Mobile Companion U-Turn Walk",
        )
        # Only rears taken so far (front walk leg not completed)
        _make_image("UMA201X", EvidenceImage.Orientation.REAR, batch=batch)
        _make_image("UMA202Y", EvidenceImage.Orientation.REAR, batch=batch)

        summary = association.run_association(batch_id=batch.batch_id)
        self.assertEqual(summary.complete_pairs, 0)


class OrderMatcherTests(TestCase):
    def setUp(self):
        InstallationOrder.objects.create(
            order_number="ORD-1",
            registration_number="UMA123AA",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        InstallationOrder.objects.create(
            order_number="ORD-2",
            registration_number="UBB456C",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        InstallationOrder.objects.create(
            order_number="ORD-CLOSED",
            registration_number="UCC789D",
            status=InstallationOrder.Status.INSTALLED,
            order_status="Installed",
            is_archived=True,
            is_active_on_itms=False,
        )

    def test_exact_match(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UMA123AA", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertEqual(pair.match_type, VehicleInstallationPair.MatchType.EXACT)
        self.assertEqual(pair.order.order_number, "ORD-1")
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)

    def test_fuzzy_match_on_minor_ocr_typo(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UMA1238A", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertIn(pair.match_type, (VehicleInstallationPair.MatchType.FUZZY, VehicleInstallationPair.MatchType.EXACT))
        if pair.order:
            self.assertEqual(pair.order.order_number, "ORD-1")
            self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)

    def test_order_not_available_flagged_conflict(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="ZZZ999ZZ", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)
        self.assertIsNone(pair.order)
        self.assertIn("Order Conflict", pair.operator_note)

    def test_closed_order_flagged_conflict(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UCC789D", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.CONFLICT)
        self.assertIsNone(pair.order)
        self.assertIn("already Installed", pair.operator_note)
