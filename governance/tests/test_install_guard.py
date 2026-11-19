"""Install control: machine binding, locked/moved/revoked installs, clone detection, LAN access gate."""
import json
import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings

from core.models import AuthorizedDevice, OperatorITMSSession
from core.services import device_access_service
from governance.models import InstallRegistration
from governance.services import install_guard, policy_sync

from governance.tests.test_policy_sync import _client_transport


class _MachineMixin:
    def set_machine(self, value: str):
        os.environ["CLOSE_MACHINE_ID"] = value

    def tearDown(self):
        os.environ.pop("CLOSE_MACHINE_ID", None)
        try:
            policy_sync._state_path().unlink()
        except FileNotFoundError:
            pass
        super().tearDown()


class FingerprintAndSealTests(_MachineMixin, TestCase):
    def test_fingerprint_is_stable_and_machine_specific(self):
        self.set_machine("machine-A")
        a1, a2 = install_guard.machine_fingerprint(), install_guard.machine_fingerprint()
        self.set_machine("machine-B")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, install_guard.machine_fingerprint())

    def test_sealed_secret_cannot_be_read_on_another_machine(self):
        self.set_machine("machine-A")
        sealed = install_guard.seal_secret("top-secret", "install-1")
        self.assertEqual(install_guard.unseal_secret(sealed, "install-1"), "top-secret")
        self.set_machine("machine-B")
        self.assertIsNone(install_guard.unseal_secret(sealed, "install-1"))

    def test_enrollment_file_never_holds_plaintext_secret(self):
        self.set_machine("machine-A")
        policy_sync.save_enrollment({"install_id": "i1", "secret": "s3cr3t", "central_url": "https://c"})
        raw = policy_sync._state_path().read_text(encoding="utf-8")
        self.assertNotIn("s3cr3t", raw)
        self.assertEqual(policy_sync.load_enrollment()["secret"], "s3cr3t")


@override_settings(DEPLOYMENT_ROLE="edge", CLOSE_REQUIRE_ENROLLMENT=True)
class InstallStateTests(_MachineMixin, TestCase):
    def test_states(self):
        self.set_machine("machine-A")
        self.assertEqual(install_guard.install_state(), install_guard.UNENROLLED)
        self.assertTrue(install_guard.is_locked())
        policy_sync.save_enrollment({"install_id": "i1", "secret": "s", "central_url": "https://c",
                                    "machine_fingerprint": install_guard.machine_fingerprint()})
        self.assertEqual(install_guard.install_state(), install_guard.ENROLLED)
        self.assertFalse(install_guard.is_locked())
        self.set_machine("machine-B")  # the folder was copied to another computer
        self.assertEqual(install_guard.install_state(), install_guard.MOVED)
        self.assertTrue(install_guard.is_locked())
        self.assertFalse(policy_sync.load_enrollment().get("secret"))

    @override_settings(CLOSE_REQUIRE_ENROLLMENT=False)
    def test_developer_mode_is_open_but_moved_still_locks(self):
        self.set_machine("machine-A")
        self.assertEqual(install_guard.install_state(), install_guard.OPEN)
        policy_sync.save_enrollment({"install_id": "i1", "secret": "s", "central_url": "https://c",
                                    "machine_fingerprint": install_guard.machine_fingerprint()})
        self.set_machine("machine-B")
        self.assertTrue(install_guard.is_locked())

    @override_settings(DEPLOYMENT_ROLE="central")
    def test_central_is_never_locked(self):
        self.assertEqual(install_guard.install_state(), install_guard.CENTRAL)
        self.assertFalse(install_guard.is_locked())

    def test_moved_copy_wipes_itms_sessions_and_web_sessions(self):
        self.set_machine("machine-A")
        user = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")
        OperatorITMSSession.objects.create(user=user, itms_email="op@itms-ug.com",
                                           cookies={"PHPSESSID": "stolen"}, is_authenticated=True)
        Client().force_login(user)
        policy_sync.save_enrollment({"install_id": "i1", "secret": "s", "central_url": "https://c",
                                    "machine_fingerprint": install_guard.machine_fingerprint()})
        self.set_machine("machine-B")
        wiped = install_guard.quarantine_moved_copy()
        sess = OperatorITMSSession.objects.get(user=user)
        self.assertEqual(sess.cookies, {})
        self.assertFalse(sess.is_authenticated)
        self.assertEqual(Session.objects.count(), 0)
        self.assertGreaterEqual(wiped["itms_sessions"], 1)
        self.assertEqual(install_guard.quarantine_moved_copy(), {"already": True})


@override_settings(DEPLOYMENT_ROLE="edge", CLOSE_REQUIRE_ENROLLMENT=True)
class InstallGateWebTests(_MachineMixin, TestCase):
    def setUp(self):
        self.set_machine("machine-A")
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        self.op = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")

    def test_unenrolled_install_is_locked_for_everyone(self):
        c = Client()
        c.force_login(self.op)
        r = c.get("/")
        self.assertEqual(r.status_code, 423)
        self.assertContains(r, "hasn", status_code=423)
        self.assertEqual(c.get("/api/pairs/").status_code, 423)
        self.assertEqual(Client().get("/login/").status_code, 200)
        self.assertEqual(Client().get("/install/").status_code, 200)

    def test_only_admin_can_activate_from_web(self):
        c = Client()
        c.force_login(self.op)
        r = c.post("/install/", {"central_url": "https://c.example", "code": "X"})
        self.assertEqual(r.status_code, 403)

    def test_enrolled_install_runs(self):
        policy_sync.save_enrollment({"install_id": "i1", "secret": "s", "central_url": "https://c",
                                    "machine_fingerprint": install_guard.machine_fingerprint()})
        c = Client()
        c.force_login(self.op)
        self.assertNotEqual(c.get("/api/pairs/").status_code, 423)


@override_settings(DEPLOYMENT_ROLE="central")
class CloneDetectionTests(_MachineMixin, TestCase):
    def setUp(self):
        self.set_machine("laptop-1")
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        _, self.code = policy_sync.create_install("AGM laptop", self.admin)
        self.transport = patch.object(policy_sync, "_http_post", side_effect=_client_transport(Client()))
        self.transport.start()

    def tearDown(self):
        self.transport.stop()
        super().tearDown()

    def test_copied_enrollment_is_detected_and_revoked(self):
        state = policy_sync.enroll_with_central("https://central.example", self.code)
        inst = InstallRegistration.objects.get(install_id=state["install_id"])
        self.assertEqual(inst.machine_fingerprint, install_guard.machine_fingerprint())

        # Attacker copies the folder AND edits the stored fingerprint to match the new laptop:
        self.set_machine("laptop-2")
        data = policy_sync.load_enrollment(decrypt=False)
        data["machine_fingerprint"] = install_guard.machine_fingerprint()
        policy_sync.save_enrollment(data, encrypt=False)
        # ...the sealed secret still can't be opened here, so it can't even sign a check-in.
        with self.assertRaises(policy_sync.PolicySyncError):
            policy_sync.heartbeat_once()

    def test_central_flags_clone_reporting_from_a_different_machine(self):
        state = policy_sync.enroll_with_central("https://central.example", self.code)
        raw = policy_sync.load_enrollment()
        # Simulate a copy that somehow kept a usable secret but reports a different machine.
        body = json.dumps({"policy_version": 0, "machine_fingerprint": "f" * 64}).encode()
        ts, sig = policy_sync.sign(raw["secret"], body)
        r = Client().post("/backoffice/api/v1/installs/heartbeat/", data=body, content_type="application/json",
                          HTTP_X_CLOSE_INSTALL=state["install_id"], HTTP_X_CLOSE_TIMESTAMP=ts, HTTP_X_CLOSE_SIGNATURE=sig)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "CLONE_DETECTED")
        inst = InstallRegistration.objects.get(install_id=state["install_id"])
        self.assertEqual(inst.status, "REVOKED")
        self.assertIsNotNone(inst.clone_detected_at)

        # The genuine laptop now learns it was revoked and locks.
        with self.assertRaises(policy_sync.PolicySyncError):
            policy_sync.heartbeat_once()
        self.assertTrue(policy_sync.load_enrollment(decrypt=False).get("revoked"))
        with override_settings(DEPLOYMENT_ROLE="edge"):
            self.assertEqual(install_guard.install_state(), install_guard.REVOKED)


@override_settings(DEPLOYMENT_ROLE="edge", CLOSE_LOCAL_ACCESS="pc_and_paired", CLOSE_REQUIRE_ENROLLMENT=False)
class LocalAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")

    def test_this_computer_is_allowed(self):
        self.assertEqual(Client(REMOTE_ADDR="127.0.0.1").get("/login/").status_code, 200)

    def test_other_lan_device_refused_even_with_forged_header(self):
        r = Client(REMOTE_ADDR="192.168.1.50").get("/login/")
        self.assertEqual(r.status_code, 403)
        r = Client(REMOTE_ADDR="192.168.1.50", HTTP_X_FORWARDED_FOR="127.0.0.1").get("/login/")
        self.assertEqual(r.status_code, 403)

    def test_qr_pairing_link_and_paired_phone_allowed(self):
        token = device_access_service.generate_qr_pairing_token(user=self.user)
        phone = Client(REMOTE_ADDR="192.168.137.20", HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Mobile")
        r = phone.get(f"/mobile/?pair_token={token}")
        self.assertNotEqual(r.status_code, 403)
        # The phone now holds a signed trust cookie for an approved QR-paired device.
        self.assertTrue(AuthorizedDevice.objects.filter(linked_via="QR_PAIRING", status="APPROVED").exists())
        self.assertNotEqual(phone.get("/mobile/").status_code, 403)

    def test_blocked_paired_phone_refused(self):
        token = device_access_service.generate_qr_pairing_token(user=self.user)
        phone = Client(REMOTE_ADDR="192.168.137.21", HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Mobile")
        phone.get(f"/mobile/?pair_token={token}")
        AuthorizedDevice.objects.update(status="BLOCKED")
        self.assertEqual(phone.get("/mobile/").status_code, 403)
