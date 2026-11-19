"""Central policy sync: enrollment, signed check-ins, snapshot apply, lock, offline grace."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.services import config_service
from governance.capabilities import Role
from governance.models import AccountState, BondMembership, InstallRegistration, PolicyState
from governance.services import policy_sync


def _client_transport(client: Client):
    """Routes the edge's HTTP POSTs to the central views through the Django test client."""
    def _post(url, body, headers, timeout=15.0):
        path = url.split("://", 1)[-1]
        path = path[path.index("/"):]
        extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items() if k != "Content-Type"}
        resp = client.post(path, data=body, content_type="application/json", **extra)
        return resp.status_code, resp.content, {k.lower(): v for k, v in resp.items()}
    return _post


@override_settings(DEPLOYMENT_ROLE="central")
class CentralEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        self.inst, self.code = policy_sync.create_install("AGM laptop", self.admin)

    def test_enrollment_code_is_single_use(self):
        c = Client()
        r = c.post("/backoffice/api/v1/installs/enroll/", data=json.dumps({"code": self.code}),
                   content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["secret"])
        r2 = c.post("/backoffice/api/v1/installs/enroll/", data=json.dumps({"code": self.code}),
                    content_type="application/json")
        self.assertEqual(r2.status_code, 400)

    def test_expired_code_refused(self):
        InstallRegistration.objects.filter(pk=self.inst.pk).update(enrollment_expires_at=timezone.now() - timedelta(minutes=1))
        r = Client().post("/backoffice/api/v1/installs/enroll/", data=json.dumps({"code": self.code}),
                          content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_heartbeat_requires_valid_signature(self):
        data = policy_sync.enroll(self.code)
        body = json.dumps({"policy_version": 0}).encode()
        ts, sig = policy_sync.sign("wrong-secret", body)
        r = Client().post("/backoffice/api/v1/installs/heartbeat/", data=body, content_type="application/json",
                          HTTP_X_CLOSE_INSTALL=data["install_id"], HTTP_X_CLOSE_TIMESTAMP=ts, HTTP_X_CLOSE_SIGNATURE=sig)
        self.assertEqual(r.status_code, 403)
        ts, sig = policy_sync.sign(data["secret"], body)
        r = Client().post("/backoffice/api/v1/installs/heartbeat/", data=body, content_type="application/json",
                          HTTP_X_CLOSE_INSTALL=data["install_id"], HTTP_X_CLOSE_TIMESTAMP=ts, HTTP_X_CLOSE_SIGNATURE=sig)
        self.assertEqual(r.status_code, 200)
        self.assertIn("snapshot", r.json())
        self.assertTrue(policy_sync.verify(data["secret"], r.content, r["X-Close-Timestamp"], r["X-Close-Signature"]))

    def test_replayed_heartbeat_refused(self):
        data = policy_sync.enroll(self.code)
        body = json.dumps({"policy_version": 0}).encode()
        old_ts = str(int(timezone.now().timestamp()) - 3600)
        _, sig = policy_sync.sign(data["secret"], body, old_ts)
        r = Client().post("/backoffice/api/v1/installs/heartbeat/", data=body, content_type="application/json",
                          HTTP_X_CLOSE_INSTALL=data["install_id"], HTTP_X_CLOSE_TIMESTAMP=old_ts, HTTP_X_CLOSE_SIGNATURE=sig)
        self.assertEqual(r.status_code, 403)

    def test_revoked_install_refused(self):
        data = policy_sync.enroll(self.code)
        InstallRegistration.objects.filter(install_id=data["install_id"]).update(status="REVOKED")
        body = b"{}"
        ts, sig = policy_sync.sign(data["secret"], body)
        r = Client().post("/backoffice/api/v1/installs/heartbeat/", data=body, content_type="application/json",
                          HTTP_X_CLOSE_INSTALL=data["install_id"], HTTP_X_CLOSE_TIMESTAMP=ts, HTTP_X_CLOSE_SIGNATURE=sig)
        self.assertEqual(r.status_code, 403)

    @override_settings(DEPLOYMENT_ROLE="edge")
    def test_endpoints_disabled_on_edge(self):
        r = Client().post("/backoffice/api/v1/installs/enroll/", data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 404)


@override_settings(DEPLOYMENT_ROLE="central")
class EdgeRoundTripTests(TestCase):
    """Edge code paths exercised against the central views (same test DB)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        _, self.code = policy_sync.create_install("Edge", self.admin)
        self.transport = patch.object(policy_sync, "_http_post", side_effect=_client_transport(Client()))
        self.transport.start()

    def tearDown(self):
        self.transport.stop()
        config_service.set_maintenance_mode(enabled=False)
        try:
            policy_sync._state_path().unlink()
        except FileNotFoundError:
            pass

    def test_enroll_sync_lock_and_staleness(self):
        state = policy_sync.enroll_with_central("https://central.example", self.code, name="Edge")
        self.assertTrue(policy_sync.is_enrolled())
        self.assertTrue(state["last_sync_ok_at"])
        # (Same test DB: applying the snapshot bumps the "central" version again, so just check it was recorded.)
        self.assertGreater(state["policy_version"], 0)
        self.assertFalse(policy_sync.is_policy_stale())

        inst = InstallRegistration.objects.get(install_id=state["install_id"])
        inst.locked, inst.lock_reason = True, "Stolen laptop"
        inst.save()
        policy_sync.heartbeat_once()
        self.assertTrue(config_service.is_maintenance_enabled())
        inst.locked = False
        inst.save()
        policy_sync.heartbeat_once()
        self.assertFalse(config_service.is_maintenance_enabled())

        st = policy_sync.load_enrollment()
        st["last_sync_ok_at"] = (timezone.now() - timedelta(hours=25)).isoformat()
        policy_sync.save_enrollment(st)
        with override_settings(DEPLOYMENT_ROLE="edge"):
            self.assertTrue(policy_sync.is_policy_stale())

    def test_policy_version_bumps_on_role_change(self):
        before = PolicyState.current().version
        user = User.objects.create_user("bump", "b@gmail.com", "Str0ng-pass!")
        BondMembership.objects.create(user=user, bond_code="AGM", role=Role.HELPER)
        self.assertGreater(PolicyState.current().version, before)


class ApplySnapshotTests(TestCase):
    def test_burned_user_is_locked_out_locally(self):
        local = User.objects.create_user("tl_edge", "tl.edge@gmail.com", "Str0ng-pass!")
        BondMembership.objects.create(user=local, bond_code="AGM", role=Role.TEAM_LEADER)
        c = Client()
        c.force_login(local)
        self.assertTrue(any(s.get_decoded().get("_auth_user_id") == str(local.pk) for s in Session.objects.all()))

        snapshot = {
            "policy_version": 99,
            "users": [{"username": "tl_edge", "email": "tl.edge@gmail.com", "password": local.password,
                       "is_active": False, "is_superuser": False, "is_staff": False, "status": "BURNED",
                       "security_stamp": "new-stamp", "reason": "fraud"},
                      {"username": "new_helper", "email": "nh@gmail.com", "password": "!", "is_active": True,
                       "is_superuser": False, "is_staff": False, "status": "ACTIVE", "security_stamp": "s2"}],
            "memberships": [{"username": "new_helper", "bond_code": "AGM", "role": "HELPER", "status": "ACTIVE"}],
            "company_emails": [], "itms_access": [], "burned": ["tl.edge@gmail.com"],
            "flags": [{"key": "identity.company_domains", "bond_code": "", "value": ["itms-ug.com"]}],
        }
        counts = policy_sync.apply_snapshot(snapshot)
        local.refresh_from_db()
        self.assertFalse(local.is_active)
        self.assertEqual(AccountState.objects.get(user=local).status, "BURNED")
        self.assertFalse(BondMembership.objects.filter(user=local).exists())
        self.assertTrue(BondMembership.objects.filter(user__username="new_helper", role="HELPER").exists())
        self.assertGreaterEqual(counts["killed"], 1)
        self.assertFalse(any(s.get_decoded().get("_auth_user_id") == str(local.pk) for s in Session.objects.all()))

    def test_central_switch_applies_locally(self):
        snapshot = {"users": [], "memberships": [], "company_emails": [], "itms_access": [], "burned": [],
                    "flags": [{"key": "switch.itms.enabled", "bond_code": "", "value": False}]}
        policy_sync.apply_snapshot(snapshot)
        self.assertFalse(config_service.is_itms_enabled())
        config_service.set_itms_enabled(True)
