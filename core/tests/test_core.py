from django.test import TestCase

from core.models import EvidenceImage, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.vision import normalizer


class NormalizerTests(TestCase):
    def test_canonicalize_strips_spacing_and_uppercases(self):
        self.assertEqual(normalizer.canonicalize("uma 123-aa"), "UMA123AA")

    def test_canonicalize_drops_punctuation_noise(self):
        self.assertEqual(normalizer.canonicalize("U.M.A 123_AA!"), "UMA123AA")

    def test_normalize_plate_valid_already_correct(self):
        result = normalizer.normalize_plate("UBB456C")
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["canonical"], "UBB456C")

    def test_normalize_plate_corrects_digit_in_letter_slot(self):
        # '8' in the first three (letter) slots should become 'B'
        result = normalizer.normalize_plate("UM8123AA")
        self.assertEqual(result["canonical"], "UMB123AA")
        self.assertTrue(result["is_valid"])

    def test_normalize_plate_corrects_letter_in_digit_slot(self):
        # 'O' in the middle three (digit) slots should become '0'
        result = normalizer.normalize_plate("UMA1O3AA")
        self.assertEqual(result["canonical"], "UMA103AA")
        self.assertTrue(result["is_valid"])

    def test_normalize_plate_invalid_length_left_uncorrected(self):
        result = normalizer.normalize_plate("UMA1")
        self.assertFalse(result["is_valid"])

    def test_is_valid_plate_regex(self):
        self.assertTrue(normalizer.is_valid_plate("UMA123AA"))
        self.assertTrue(normalizer.is_valid_plate("UBB456C"))
        self.assertFalse(normalizer.is_valid_plate("123UMAAA"))


class ModelTests(TestCase):
    def test_installation_order_str(self):
        order = InstallationOrder.objects.create(
            order_number="ORD-TEST-1", registration_number="UMA123AA",
        )
        self.assertIn("ORD-TEST-1", str(order))

    def test_evidence_image_defaults(self):
        image = EvidenceImage.objects.create(
            file_hash="a" * 64, original_source_path="/tmp/x.jpg", vault_file="vault/x.jpg",
        )
        self.assertEqual(image.status, EvidenceImage.Status.NEW)
        self.assertEqual(image.orientation, EvidenceImage.Orientation.UNKNOWN)

    def test_pair_refresh_completeness(self):
        front = EvidenceImage.objects.create(file_hash="f" * 64, original_source_path="/tmp/f.jpg", vault_file="vault/f.jpg")
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UMA123AA")
        self.assertFalse(pair.refresh_completeness())

        pair.front_image = front
        self.assertFalse(pair.refresh_completeness())  # rear still missing

        rear = EvidenceImage.objects.create(file_hash="r" * 64, original_source_path="/tmp/r.jpg", vault_file="vault/r.jpg")
        pair.rear_image = rear
        self.assertTrue(pair.refresh_completeness())

    def test_audit_log_append_only_creation(self):
        pair = VehicleInstallationPair.objects.create(registration_number_detected="UBB456C")
        log = SubmissionAuditLog.objects.create(
            pair=pair, action=SubmissionAuditLog.Action.ORDER_LOOKUP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS, message="ok",
        )
        self.assertEqual(pair.audit_logs.count(), 1)
        self.assertEqual(log.result, "SUCCESS")
