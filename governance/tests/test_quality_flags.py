"""Quality Inspector flags: raise, resolve, apply suggestion, and the submission gate."""
import uuid

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import (
    EvidenceImage, IngestionBatch, InstallationKit, InstallationOrder, QualityFlag,
    SubmissionAuditLog, VehicleInstallationPair,
)
from core.services import quality_flag_service as qfs
from core.services.submission_worker import submit_pair
from governance.capabilities import Role
from governance.models import BondMembership, CompanyEmailLink
from governance.services.authz import CapabilityDenied


def _member(username, role, bond="AGM"):
    user = User.objects.create_user(username, f"{username}@gmail.com", "Str0ng-pass!")
    BondMembership.objects.create(user=user, bond_code=bond, role=role)
    CompanyEmailLink.objects.create(user=user, email=f"{username}@itms-ug.com", status="VERIFIED")
    return user


@override_settings(GOVERNANCE_ENFORCE=True)
class QualityFlagTests(TestCase):
    def setUp(self):
        self.qi = _member("qi", Role.QUALITY_INSPECTOR)
        self.tl = _member("tl", Role.TEAM_LEADER)
        self.helper = User.objects.create_user("helper", "h@gmail.com", "Str0ng-pass!")
        BondMembership.objects.create(user=self.helper, bond_code="AGM", role=Role.HELPER)
        batch = IngestionBatch.objects.create(batch_id="B-FLAG-1", operator=self.tl, facility_code="AGM")
        mk = lambda o: EvidenceImage.objects.create(batch=batch, orientation=o, file_hash=uuid.uuid4().hex,
                                                    captured_at=timezone.now())
        self.order = InstallationOrder.objects.create(order_number="PO-FLAG-1", registration_number="UMA100AA",
                                                      status=InstallationOrder.Status.PENDING, facility_code="AGM")
        self.pair = VehicleInstallationPair.objects.create(
            front_image=mk(EvidenceImage.Orientation.FRONT), rear_image=mk(EvidenceImage.Orientation.REAR),
            registration_number_detected="UMA100AA", is_complete=True, order=self.order, facility_code="AGM",
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )
        self.kit = InstallationKit.objects.create(kit_code="IK-UMA100AA", registration_number="UMA 100AA",
                                                  facility_code="AGM")

    def test_qi_raises_flag_and_it_blocks_submission(self):
        flag = qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "WRONG_PLATE", "Last letter is B", "UMA100AB")
        self.assertEqual(flag.status, QualityFlag.Status.OPEN)
        self.assertEqual(flag.bond_code, "AGM")
        self.assertIn("Blocked by open quality flag", qfs.submission_block_message(self.pair))
        outcome = submit_pair(self.pair, backend="mock", dry_run=True, operator=self.tl)
        self.assertFalse(outcome.success)
        self.assertIn("quality flag", outcome.error)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)
        self.assertTrue(SubmissionAuditLog.objects.filter(pair=self.pair, action="FLAG_RAISED").exists())

    def test_order_and_kit_flags_also_block_the_pair(self):
        qfs.raise_flag(self.qi, "ORDER", self.order.pk, "ORDER_DATA_ERROR", "VIN wrong")
        self.assertTrue(qfs.submission_block_message(self.pair))
        QualityFlag.objects.update(status=QualityFlag.Status.RESOLVED)
        qfs.raise_flag(self.qi, "KIT", self.kit.kit_code, "WRONG_KIT", "Serials swapped")
        self.assertTrue(qfs.submission_block_message(self.pair))

    def test_helper_and_supervisor_cannot_raise(self):
        with self.assertRaises(CapabilityDenied):
            qfs.raise_flag(self.helper, "PAIR", self.pair.pk, "WRONG_PLATE", "x")

    def test_qi_cannot_resolve_but_tl_can(self):
        flag = qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "BAD_PHOTO", "blurry")
        with self.assertRaises(CapabilityDenied):
            qfs.resolve_flag(self.qi, flag, "resolve")
        qfs.resolve_flag(self.tl, flag, "resolve", "retook photo")
        self.assertEqual(qfs.submission_block_message(self.pair), "")

    def test_apply_suggestion_corrects_plate_and_unlinks_mismatched_order(self):
        flag = qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "WRONG_PLATE", "", "UMA 100AB")
        self.assertEqual(flag.suggested_plate, "UMA100AB")
        qfs.resolve_flag(self.tl, flag, "apply")
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.registration_number_detected, "UMA100AB")
        self.assertTrue(self.pair.is_manual_override)
        self.assertIsNone(self.pair.order_id)
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)

    def test_invalid_suggestion_and_duplicates_rejected(self):
        with self.assertRaises(qfs.QualityFlagError):
            qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "WRONG_PLATE", "", "NOT-A-PLATE")
        qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "BAD_PHOTO", "blurry")
        with self.assertRaises(qfs.QualityFlagError):
            qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "BAD_PHOTO", "again")

    def test_flag_at_other_bond_is_denied(self):
        self.pair.facility_code = "EAGLE"
        self.pair.save()
        with self.assertRaises(CapabilityDenied):
            qfs.raise_flag(self.qi, "PAIR", self.pair.pk, "WRONG_PLATE", "x")

    def test_api_round_trip_and_queue_badge(self):
        qi_client = Client()
        qi_client.force_login(self.qi)
        r = qi_client.post("/api/flags/raise/", data={
            "target_type": "PAIR", "target_id": self.pair.pk, "reason_code": "WRONG_PLATE",
            "note": "check", "suggested_plate": "UMA100AB"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        flag_id = r.json()["flag"]["id"]

        listing = qi_client.get(f"/api/flags/?pair_id={self.pair.pk}").json()
        self.assertEqual(listing["count"], 1)
        self.assertFalse(listing["can_resolve"])
        self.assertEqual(qi_client.post(f"/api/flags/{flag_id}/resolve/", data={"outcome": "reject"},
                                        content_type="application/json").status_code, 403)

        tl_client = Client()
        tl_client.force_login(self.tl)
        pairs = tl_client.get("/api/pairs/?date=ALL&batch=ALL").json()
        row = next((p for p in pairs.get("pairs", []) if p["id"] == self.pair.pk), None)
        if row is not None:
            self.assertEqual(row["open_flags"], 1)
        r = tl_client.post(f"/api/flags/{flag_id}/resolve/", data={"outcome": "reject", "note": "plate is right"},
                           content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["flag"]["status"], "REJECTED")
