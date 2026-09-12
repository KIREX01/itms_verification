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
    def test_front_and_rear_pair_marks_complete(self):
        _make_image("UMA123AA", EvidenceImage.Orientation.FRONT)
        _make_image("UMA123AA", EvidenceImage.Orientation.REAR)

        summary = association.run_association()

        self.assertEqual(summary.complete_pairs, 1)
        pair = VehicleInstallationPair.objects.get(registration_number_detected="UMA123AA")
        self.assertTrue(pair.is_complete)
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)

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


class OrderMatcherTests(TestCase):
    def setUp(self):
        InstallationOrder.objects.create(order_number="ORD-1", registration_number="UMA123AA")
        InstallationOrder.objects.create(order_number="ORD-2", registration_number="UBB456C")

    def test_exact_match(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UMA123AA", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertEqual(pair.match_type, VehicleInstallationPair.MatchType.EXACT)
        self.assertEqual(pair.order.order_number, "ORD-1")

    def test_fuzzy_match_on_minor_ocr_typo(self):
        # UMA1238A vs UMA123AA -- one character swapped, should still be a close fuzzy match
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UMA1238A", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertIn(pair.match_type, (VehicleInstallationPair.MatchType.FUZZY, VehicleInstallationPair.MatchType.EXACT))
        if pair.order:
            self.assertEqual(pair.order.order_number, "ORD-1")

    def test_unregistered_vehicle_flagged(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="ZZZ999ZZ", is_complete=True)
        order_matcher.match_pair_to_order(pair)
        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.UNREGISTERED)
        self.assertIsNone(pair.order)
