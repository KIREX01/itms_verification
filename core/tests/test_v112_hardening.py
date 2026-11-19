"""
v1.1.2 security hardening regression tests.

Covers:
  1. Security/access admin APIs require a superuser.
  2. /api/settings/ only lets non-admins change operator-level keys; updates are admin-only.
  3. The emailed access-review link needs an admin login and a POST (no GET side effects).
  4. (maintenance middleware - see test_lifecycle_maintenance.py)
  5. The device access guard fails closed.
  6. Extending access never lifts a block; approvals never revive admin-deactivated accounts;
     activation codes are throttled and work from the TUI (request=None).
  8. Anonymous /api/itms/status/ exposes no operator identity.
 10. Signup never stores the plaintext password, never puts the code in a URL, and never
     promotes the first registrant to superuser.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import AuthorizedDevice, EmailAccessRequest
from core.services import auth_service, config_service, device_access_service


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("v112_admin", "admin@example.com", "Adm1n-Strong-pw")
        self.operator = User.objects.create_user("v112_operator", "op@example.com", "Op3rator-Strong-pw")
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)
        self.op_client = Client()
        self.op_client.force_login(self.operator)
        self.anon = Client()

    def tearDown(self):
        config_service.set_maintenance_mode(enabled=False)
        config_service.set_itms_enabled(True) if hasattr(config_service, "set_itms_enabled") else None
        cache.clear()


class SecurityApiAdminOnlyTests(_Base):
    def test_operator_cannot_read_security_overview(self):
        res = self.op_client.get("/api/security/overview/")
        self.assertEqual(res.status_code, 403)

    def test_operator_cannot_run_security_actions(self):
        res = self.op_client.post(
            "/api/security/action/",
            data='{"action": "preapprove_email", "email": "x@example.com"}',
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertFalse(EmailAccessRequest.objects.filter(email="x@example.com").exists())

    def test_admin_can_read_security_overview(self):
        res = self.admin_client.get("/api/security/overview/")
        self.assertEqual(res.status_code, 200)


class SettingsAllowlistTests(_Base):
    def _post(self, client, key, value):
        return client.post(
            "/api/settings/",
            data={"key": key, "value": value},
            content_type="application/json",
        )

    def test_operator_cannot_flip_kill_switches_or_security(self):
        for key, value in [
            ("itms.enabled", False),
            ("maintenance.enabled", True),
            ("security.tui_master_pin_hash", "x"),
            ("security_access.require_email_approval", False),
            ("submission.submit_step3", False),
            ("outbox.enabled", False),
            ("bond.strict_bond_scoping", False),
            ("totally.unknown_key", 1),
        ]:
            res = self._post(self.op_client, key, value)
            self.assertEqual(res.status_code, 403, key)
        self.assertTrue(config_service.get_setting("itms.enabled", True))

    def test_operator_can_change_operator_level_settings(self):
        res = self._post(self.op_client, "system.theme", "dark")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(config_service.get_setting("system.theme"), "dark")

    def test_mixed_batch_is_rejected_atomically(self):
        res = self.op_client.post(
            "/api/settings/",
            data={"settings": {"system.theme": "light", "itms.enabled": False}},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertNotEqual(config_service.get_setting("system.theme"), "light")

    def test_admin_can_change_policy_settings(self):
        res = self._post(self.admin_client, "outbox.enabled", False)
        self.assertEqual(res.status_code, 200)

    def test_operator_cannot_apply_updates(self):
        res = self.op_client.post("/api/updates/apply/")
        self.assertEqual(res.status_code, 403)


class AccessReviewLinkTests(_Base):
    def setUp(self):
        super().setUp()
        self.req = device_access_service.create_or_update_access_request(
            request=None,
            email="newcomer@example.com",
            username="newcomer",
            status=EmailAccessRequest.Status.PENDING,
        )
        self.url = f"/access/review/{self.req.activation_token}/"

    def test_anonymous_get_redirects_to_login_without_side_effects(self):
        res = self.anon.get(self.url + "?action=approve")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login/", res["Location"])
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, EmailAccessRequest.Status.PENDING)

    def test_operator_is_forbidden(self):
        self.assertEqual(self.op_client.get(self.url).status_code, 403)
        self.assertEqual(self.op_client.post(self.url, {"action": "approve"}).status_code, 403)

    def test_admin_get_only_shows_confirmation(self):
        res = self.admin_client.get(self.url + "?action=approve")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "newcomer@example.com")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, EmailAccessRequest.Status.PENDING)

    def test_admin_post_approves(self):
        res = self.admin_client.post(self.url, {"action": "approve", "hours": "8"})
        self.assertEqual(res.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, EmailAccessRequest.Status.APPROVED)
        self.assertEqual(self.req.reviewed_by, self.admin)


class FailClosedGuardTests(_Base):
    def test_guard_crash_denies_protected_routes_but_keeps_login(self):
        with patch(
            "core.services.device_access_service.evaluate_request_access",
            side_effect=RuntimeError("db down"),
        ):
            self.assertEqual(self.op_client.get("/api/stats/").status_code, 503)
            self.assertEqual(self.anon.get("/login/").status_code, 200)


class RevocationStickinessTests(_Base):
    def test_extend_does_not_unblock_device(self):
        dev = AuthorizedDevice.objects.create(
            device_id="dev-blocked-1",
            user=self.operator,
            authorized_email="op@example.com",
            status=AuthorizedDevice.Status.BLOCKED,
            block_reason="admin block",
        )
        device_access_service.extend_or_set_expiry("op@example.com", duration_hours=4)
        device_access_service.extend_or_set_expiry("dev-blocked-1", duration_hours=4)
        dev.refresh_from_db()
        self.assertEqual(dev.status, AuthorizedDevice.Status.BLOCKED)
        self.assertEqual(dev.block_reason, "admin block")

    def test_reinvite_does_not_reactivate_admin_deactivated_account(self):
        self.operator.last_login = timezone.now()
        self.operator.is_active = False
        self.operator.save()
        device_access_service.preapprove_operator_email("op@example.com", send_invite_email=False)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)

    def test_approval_activates_pending_signup(self):
        pending = User.objects.create_user("pending_user", "pending@example.com", "Pend1ng-pw", is_active=False)
        req = device_access_service.create_or_update_access_request(
            request=None, email="pending@example.com", username="pending_user",
            status=EmailAccessRequest.Status.PENDING,
        )
        ok, _msg, _ = device_access_service.approve_access_request(req, admin_user=self.admin)
        self.assertTrue(ok)
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)


class ActivationCodeTests(_Base):
    def setUp(self):
        super().setUp()
        self.req = device_access_service.create_or_update_access_request(
            request=None, email="op@example.com", username="v112_operator",
            status=EmailAccessRequest.Status.APPROVED,
        )
        self.req.expires_at = timezone.now() + timedelta(hours=2)
        self.req.save()

    def test_tui_renewal_with_request_none_does_not_crash(self):
        ok, msg, _dev = device_access_service.activate_with_code(
            request=None, email="op@example.com", activation_code=self.req.activation_code
        )
        self.assertTrue(ok, msg)

    def test_wrong_codes_are_throttled(self):
        for _ in range(device_access_service.ACTIVATION_CODE_MAX_FAILURES):
            ok, _msg, _ = device_access_service.activate_with_code(None, "op@example.com", "000000")
            self.assertFalse(ok)
        ok, msg, _ = device_access_service.activate_with_code(None, "op@example.com", self.req.activation_code)
        self.assertFalse(ok)
        self.assertIn("Too many", msg)

    def test_code_cannot_revive_admin_deactivated_account(self):
        self.operator.last_login = timezone.now()
        self.operator.is_active = False
        self.operator.save()
        ok, _msg, _ = device_access_service.activate_with_code(None, "op@example.com", self.req.activation_code)
        self.assertFalse(ok)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)


class PublicItmsStatusTests(_Base):
    @patch("core.services.auth_service.get_itms_status")
    def test_anonymous_status_hides_identity_and_cannot_force_ping(self, mock_status):
        mock_status.return_value = {
            "url": "https://stock.itms.ug", "online": True, "authenticated": True,
            "user_email": "someone@itms-ug.com", "user_uuid": "abc", "latency_ms": 10,
        }
        res = self.anon.get("/api/itms/status/?force=1")
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertNotIn("someone@itms-ug.com", body)
        self.assertNotIn("abc", body)
        self.assertNotIn("active_bond", res.json())
        self.assertFalse(mock_status.call_args.kwargs.get("force_ping"))

    @patch("core.services.auth_service.get_itms_status")
    def test_authenticated_status_keeps_full_detail(self, mock_status):
        mock_status.return_value = {"online": True, "user_email": "someone@itms-ug.com"}
        res = self.op_client.get("/api/itms/status/")
        self.assertIn("someone@itms-ug.com", res.content.decode())


class SignupHygieneTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_first_registrant_is_not_superuser(self):
        User.objects.all().delete()
        user, err = auth_service.create_operator_account("first_person", "Str0ng-pass!", email="f@example.com")
        self.assertIsNotNone(user, err)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_create_account_from_password_hash(self):
        from django.contrib.auth.hashers import make_password
        user, err = auth_service.create_operator_account(
            "hash_user", password_hash=make_password("Hashed-pass-1"), email="h@example.com"
        )
        self.assertIsNotNone(user, err)
        self.assertTrue(user.check_password("Hashed-pass-1"))

    @patch("core.services.email_service.send_2fa_verification_code", return_value=(True, "sent"))
    def test_pending_signup_never_stores_plaintext_password_or_code_url(self, mock_send):
        client = Client()
        res = client.post("/signup/", {
            "username": "brand_new",
            "full_name": "Brand New",
            "email": "brand@example.com",
            "password": "Pla1ntext-Secret!",
            "password_confirm": "Pla1ntext-Secret!",
        })
        self.assertEqual(res.status_code, 302)
        pending = client.session.get("pending_signup", {})
        self.assertNotIn("password", pending)
        self.assertNotIn("Pla1ntext-Secret!", str(pending))
        self.assertTrue(pending.get("password_hash"))
        verify_url = mock_send.call_args.kwargs.get("verify_url", "")
        self.assertNotIn("code=", verify_url)

        # Completing verification creates the account with the chosen password.
        res2 = client.post("/signup/verify/", {"code": pending["code"]})
        self.assertIn(res2.status_code, (200, 302))
        user = User.objects.get(username="brand_new")
        self.assertTrue(user.check_password("Pla1ntext-Secret!"))

    @override_settings(SIGNUP_2FA_ENABLED=False)
    def test_non_2fa_signup_path_does_not_crash(self):
        device_access_service.preapprove_operator_email("direct@example.com", send_invite_email=False)
        res = Client().post("/signup/", {
            "username": "direct_user",
            "full_name": "Direct User",
            "email": "direct@example.com",
            "password": "Dir3ct-pass!",
            "password_confirm": "Dir3ct-pass!",
        })
        self.assertIn(res.status_code, (200, 302))
        self.assertTrue(User.objects.filter(username="direct_user").exists())
