"""Fleet: install links (tokens), gated installer, lifecycle ledger, release policy, directives, security ledger."""
import hashlib
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import AuthorizedDevice
from governance.models import (
    EnrollmentToken, InstallCommand, InstallLifecycleEvent, InstallRegistration, SecurityEvent,
)
from governance.services import fleet, policy_sync, service_control, telemetry_outbox


def _admin():
    return User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")


@override_settings(DEPLOYMENT_ROLE="central")
class TokenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _admin()

    def test_issue_and_validate(self):
        token, code = fleet.issue_token(self.admin, bond_code="agm", max_seats=2, hours=1)
        self.assertRegex(code, r"^CLOSE-AGM-\d{4}-[A-Z0-9]{8}$")
        self.assertNotIn(code, token.code_hash)  # only the hash is stored
        self.assertEqual(fleet.check_token(code).pk, token.pk)

    def test_refusals_are_logged(self):
        _t, expired = fleet.issue_token(self.admin, hours=1)
        EnrollmentToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        t2, revoked = fleet.issue_token(self.admin)
        fleet.revoke_token(t2, self.admin)
        t3, full = fleet.issue_token(self.admin, max_seats=1)
        EnrollmentToken.objects.filter(pk=t3.pk).update(used_seats=1)
        _t4, ranged = fleet.issue_token(self.admin, allowed_ip_cidr="41.210.0.0/16")
        for code, ip in ((expired, ""), (revoked, ""), (full, ""), (ranged, "8.8.8.8"), ("CLOSE-X-2026-AAAAAAAA", "")):
            with self.assertRaises(fleet.FleetError):
                fleet.check_token(code, ip=ip)
        self.assertEqual(SecurityEvent.objects.filter(event_type="INSTALL_TOKEN_REFUSED").count(), 5)
        self.assertEqual(fleet.check_token(ranged, ip="41.210.4.5").allowed_ip_cidr, "41.210.0.0/16")

    def test_bad_inputs(self):
        for kwargs in ({"max_seats": 0}, {"hours": 0}, {"allowed_ip_cidr": "not-an-ip"}):
            with self.assertRaises(fleet.FleetError):
                fleet.issue_token(self.admin, **kwargs)


@override_settings(DEPLOYMENT_ROLE="central")
class GatedInstallerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _admin()
        _t, self.code = fleet.issue_token(self.admin, bond_code="AGM", max_seats=3)

    def test_script_has_token_and_url_and_is_logged(self):
        for name, marker in (("install.ps1", f'$EnrollmentToken = "{self.code}"'),
                             ("install.sh", f'CLOSE_ENROLL_TOKEN="{self.code}"')):
            r = Client().get(f"/backoffice/api/v1/installs/script/{self.code}/{name}", HTTP_USER_AGENT="curl/8")
            self.assertEqual(r.status_code, 200, name)
            body = r.content.decode()
            self.assertIn(marker, body)
            from django.conf import settings
            self.assertIn(settings.CLOSE_CENTRAL_URL.rstrip("/"), body)
        self.assertEqual(InstallLifecycleEvent.objects.filter(event_type="SCRIPT_DOWNLOADED").count(), 2)
        # Download does not consume a seat; enrollment does.
        self.assertEqual(EnrollmentToken.objects.get().used_seats, 0)

    def test_powershell_injection_sits_after_param_block(self):
        body = Client().get(f"/backoffice/api/v1/installs/script/{self.code}/install.ps1").content.decode()
        self.assertLess(body.index("param ("), body.index("$EnrollmentToken ="))

    def test_bad_token_refused_and_throttled(self):
        url = "/backoffice/api/v1/installs/script/CLOSE-AGM-2026-ZZZZZZZZ/install.sh"
        self.assertEqual(Client().get(url).status_code, 403)
        cache.set("close:installer-fail:127.0.0.1", 20, 60)
        self.assertEqual(Client().get(f"/backoffice/api/v1/installs/script/{self.code}/install.sh").status_code, 429)

    def test_unknown_filename_404(self):
        self.assertEqual(Client().get(f"/backoffice/api/v1/installs/script/{self.code}/evil.exe").status_code, 404)

    @override_settings(DEPLOYMENT_ROLE="edge")
    def test_not_served_by_laptops(self):
        self.assertEqual(Client().get(f"/backoffice/api/v1/installs/script/{self.code}/install.sh").status_code, 404)

    def test_manifest(self):
        cache.clear()
        fleet.set_release_policy(self.admin, "1.3.0", "a" * 64)
        data = Client().get("/backoffice/api/v1/installs/manifest/").json()
        self.assertEqual((data["min_allowed_version"], data["zip_sha256"]), ("1.3.0", "a" * 64))


@override_settings(DEPLOYMENT_ROLE="central")
class TokenEnrollmentAndCheckInTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _admin()
        _t, self.code = fleet.issue_token(self.admin, bond_code="AGM", max_seats=1)

    def test_token_enrollment_creates_bound_install_and_uses_a_seat(self):
        res = policy_sync.enroll(self.code, app_version="1.3.0", ip="41.1.1.1", machine_fingerprint="m1",
                                 device_info={"hostname": "AGM-PC-1"})
        inst = InstallRegistration.objects.get(install_id=res["install_id"])
        self.assertEqual((inst.status, inst.bond_code, inst.name, res["bond_code"]), ("ACTIVE", "AGM", "AGM-PC-1", "AGM"))
        self.assertEqual(EnrollmentToken.objects.get().used_seats, 1)
        self.assertTrue(InstallLifecycleEvent.objects.filter(install=inst, event_type="ENROLLED").exists())
        with self.assertRaises(policy_sync.PolicySyncError):
            policy_sync.enroll(self.code, machine_fingerprint="m2")  # no seats left

    def _beat(self, inst, payload):
        body = json.dumps(payload).encode()
        ts, sig = policy_sync.sign(inst.secret, body)
        return policy_sync.handle_heartbeat(inst.install_id, body, ts, sig, ip="41.1.1.1")

    def test_check_in_ingests_events_and_returns_release_policy(self):
        res = policy_sync.enroll(self.code, app_version="1.2.1", machine_fingerprint="m1")
        inst = InstallRegistration.objects.get(install_id=res["install_id"])
        fleet.set_release_policy(self.admin, "1.3.0", auto_update=True)
        resp = self._beat(inst, {"app_version": "1.2.1", "events": [
            {"event_type": "UPDATE_FAILED", "ok": False, "from_version": "1.2.1", "detail": "disk full"},
            {"event_type": "NOT_A_TYPE"}]})
        self.assertEqual(resp["events_ack"], 1)
        self.assertEqual(resp["release"]["min_version"], "1.3.0")
        self.assertTrue(InstallLifecycleEvent.objects.filter(install=inst, event_type="UPDATE_FAILED", ok=False).exists())
        # Below the minimum with auto-update on: one update command, not one per check-in.
        self.assertEqual([c["command"] for c in resp["commands"]], ["update.apply"])
        self._beat(inst, {"app_version": "1.2.1"})
        self.assertEqual(InstallCommand.objects.filter(command="update.apply").count(), 1)
        self.assertTrue(fleet.is_outdated("1.2.9", fleet.release_policy()))
        self.assertFalse(fleet.is_outdated("1.10.0", fleet.release_policy()))


class OutboxAndUpdateTests(TestCase):
    def test_outbox_survives_and_clears(self):
        with tempfile.TemporaryDirectory() as d:
            telemetry_outbox.record_event("INSTALL_STARTED", base_dir=Path(d))
            telemetry_outbox.record_event("BOGUS", base_dir=Path(d))
            telemetry_outbox.record_event("INSTALL_COMPLETED", base_dir=Path(d))
            self.assertEqual([e["event_type"] for e in telemetry_outbox.pending(Path(d))],
                             ["INSTALL_STARTED", "INSTALL_COMPLETED"])
            telemetry_outbox.clear(1, base_dir=Path(d))
            self.assertEqual(len(telemetry_outbox.pending(Path(d))), 1)

    def test_archive_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.zip"
            p.write_bytes(b"release")
            good = hashlib.sha256(b"release").hexdigest()
            self.assertTrue(fleet.verify_update_archive(p, good)[0])
            ok, why = fleet.verify_update_archive(p, "0" * 64)
            self.assertFalse(ok)
            self.assertIn("mismatch", why)

    def test_bad_release_policy_values(self):
        admin = _admin()
        with self.assertRaises(fleet.FleetError):
            fleet.set_release_policy(admin, "latest")
        with self.assertRaises(fleet.FleetError):
            fleet.set_release_policy(admin, "1.3.0", "abc")


class DirectiveTests(TestCase):
    def test_wipe_itms_session(self):
        from core.models import OperatorITMSSession
        user = User.objects.create_user("tl", "tl@example.com", "x")
        OperatorITMSSession.objects.create(user=user, itms_email="tl@itms-ug.com")
        run = service_control.run_action("itms.wipe_session", triggered_by="central:1", wait=True)
        run.refresh_from_db()
        self.assertTrue(run.ok, run.summary)
        self.assertFalse(OperatorITMSSession.objects.exists())

    def test_rollback_directive(self):
        with mock.patch("core.services.update_service.UpdateService.rollback_version",
                        return_value={"success": True, "message": "rolled back", "target": "v1.2.1"}) as rb:
            run = service_control.run_action("update.rollback", triggered_by="central:2", wait=True)
        rb.assert_called_once()
        run.refresh_from_db()
        self.assertTrue(run.ok)


class SecurityLedgerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def test_append_only(self):
        fleet.security_event("QR_PAIRED", reason="x")
        e = SecurityEvent.objects.get()
        e.reason = "changed"
        with self.assertRaises(ValueError):
            e.save()
        with self.assertRaises(ValueError):
            e.delete()

    def test_device_decisions_are_logged(self):
        from governance.services import device_admission
        admin = _admin()
        dev = AuthorizedDevice.objects.create(device_id="DEV-ABC1", status="PENDING")
        device_admission.approve(dev, admin)
        device_admission.block(dev, admin, reason="lost phone")
        self.assertEqual(list(SecurityEvent.objects.order_by("created_at", "pk").values_list("event_type", flat=True)),
                         ["DEVICE_APPROVED", "DEVICE_BLOCKED"])

    def test_mac_mismatch_holds_device(self):
        dev = AuthorizedDevice.objects.create(device_id="DEV-LAN1", status="APPROVED", mac_address="aa:bb:cc:dd:ee:01")
        req = self.rf.get("/", REMOTE_ADDR="192.168.1.20")
        with mock.patch("core.services.device_access_service.resolve_client_mac_address",
                        return_value="aa:bb:cc:dd:ee:99") as res:
            self.assertFalse(fleet.check_device_anomalies(req, dev, "192.168.1.20"))
        self.assertEqual(res.call_args.args[1], None)  # ARP only, never client headers
        dev.refresh_from_db()
        self.assertEqual(dev.status, "PENDING")
        self.assertTrue(SecurityEvent.objects.filter(event_type="ANOMALY_MAC_MISMATCH").exists())

    def test_matching_mac_passes(self):
        dev = AuthorizedDevice.objects.create(device_id="DEV-LAN2", status="APPROVED", mac_address="aa:bb:cc:dd:ee:01")
        with mock.patch("core.services.device_access_service.resolve_client_mac_address", return_value="AA:BB:CC:DD:EE:01"):
            self.assertTrue(fleet.check_device_anomalies(self.rf.get("/"), dev, "192.168.1.20"))

    def test_ip_jump_logged_once(self):
        dev = AuthorizedDevice.objects.create(device_id="DEV-NET1", status="APPROVED")
        for ip in ("41.210.1.1", "41.210.9.9", "102.80.1.1", "102.80.1.1"):
            self.assertTrue(fleet.check_device_anomalies(self.rf.get("/"), dev, ip))
        self.assertEqual(SecurityEvent.objects.filter(event_type="ANOMALY_IP_JUMP").count(), 1)

    def test_login_lockout_logged(self):
        from governance.services import login_guard
        req = self.rf.post("/login/", REMOTE_ADDR="10.0.0.5")
        for _ in range(login_guard.USER_FAIL_LIMIT):
            login_guard.record("victim", False, req)
        self.assertEqual(SecurityEvent.objects.filter(event_type="LOGIN_LOCKED").count(), 1)

    def test_activation_failures_logged_and_ip_throttled(self):
        from core.services import device_access_service as das
        for i in range(das.ACTIVATION_CODE_MAX_FAILURES):
            req = self.rf.post("/access/activate/", REMOTE_ADDR="10.0.0.9")
            das.activate_with_code(req, email=f"u{i}@example.com", activation_code="000000")
        self.assertEqual(SecurityEvent.objects.filter(event_type="ACTIVATION_FAILED").count(),
                         das.ACTIVATION_CODE_MAX_FAILURES)
        ok, msg, _ = das.activate_with_code(self.rf.post("/", REMOTE_ADDR="10.0.0.9"),
                                            email="fresh@example.com", activation_code="123456")
        self.assertFalse(ok)
        self.assertIn("Too many", msg)


@override_settings(DEPLOYMENT_ROLE="central", GOVERNANCE_ENFORCE=True)
class FleetPagesAndCliTests(TestCase):
    def setUp(self):
        cache.clear()
        from backoffice.tests.test_backoffice import admin_client_with_2fa
        self.admin = _admin()
        self.c = admin_client_with_2fa(self.admin)

    def test_install_link_needs_password_and_shows_commands_once(self):
        self.c.post("/backoffice/installs/", {"action": "issue_token", "bond_code": "AGM"})
        self.assertFalse(EnrollmentToken.objects.exists())
        r = self.c.post("/backoffice/installs/", {"action": "issue_token", "bond_code": "AGM", "max_seats": "3",
                                                  "hours": "12", "confirm_password": "Owner-pass-1"})
        self.assertContains(r, "/install.ps1 | iex")
        self.assertContains(r, "install.sh | bash")
        self.assertNotContains(self.c.get("/backoffice/installs/"), "| iex")

    def test_signins_page_lists_security_events(self):
        fleet.security_event("ANOMALY_IP_JUMP", reason="41.1.1.1 → 102.1.1.1")
        self.assertContains(self.c.get("/backoffice/signins/"), "IP address jump")

    def test_cli(self):
        out = io.StringIO()
        call_command("itms_fleet", "token", "--bond", "AGM", "--seats", "2", stdout=out)
        self.assertIn("install.sh | bash", out.getvalue())
        inst = InstallRegistration.objects.create(name="AGM laptop", install_id="i-1", status="ACTIVE", secret="s")
        call_command("itms_fleet", "directive", "AGM laptop", "itms.wipe_session", stdout=out)
        self.assertEqual(InstallCommand.objects.get(install=inst).command, "itms.wipe_session")
        call_command("itms_fleet", "directive", "i-1", "lock", stdout=out)
        inst.refresh_from_db()
        self.assertTrue(inst.locked)
        call_command("itms_fleet", "status", stdout=out)
        self.assertIn("LOCKED", out.getvalue())
