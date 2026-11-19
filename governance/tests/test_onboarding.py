"""Request access -> grant -> signup with invite."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from governance.capabilities import Role
from governance.models import AccessRequest, AccountState, BondMembership, BurnedIdentity, CompanyEmailLink
from governance.services import onboarding
from governance.services.authz import CapabilityDenied


@override_settings(GOVERNANCE_ENFORCE=True)
@patch("core.services.email_service.send_2fa_verification_code", return_value=(True, "sent"))
@patch("core.services.email_service.send_access_request_admin_alert", return_value=(True, "sent"))
class OnboardingFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")

    def _request(self, **over):
        data = dict(full_name="Akena Eric", email="eric.private@gmail.com", requested_role=Role.TEAM_LEADER,
                    bond_code="AGM", company_email="e.akena@itms-ug.com")
        data.update(over)
        return onboarding.submit_request(**data)

    def test_request_creates_no_account(self, *_):
        users_before = User.objects.count()
        req = self._request()
        self.assertEqual(req.status, AccessRequest.Status.PENDING)
        self.assertEqual(User.objects.count(), users_before)

    def test_company_roles_need_company_email(self, *_):
        with self.assertRaises(onboarding.OnboardingError):
            self._request(company_email="")
        with self.assertRaises(onboarding.OnboardingError):
            self._request(company_email="eric@gmail.com")
        helper = self._request(email="helper@gmail.com", requested_role=Role.HELPER, company_email="")
        self.assertIsNotNone(helper)

    def test_full_flow_creates_active_account_with_role_and_company_email(self, mock_alert, mock_invite):
        req = self._request()
        req, token, code = onboarding.grant(req, self.admin)
        self.assertEqual(req.status, AccessRequest.Status.GRANTED)
        mock_invite.assert_called_once()
        self.assertNotIn(code, req.invite_code_hash)  # stored hashed

        user = onboarding.redeem_invite(token, code, "eric_tl", "Strong-pass-9", "Strong-pass-9")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_superuser)
        m = BondMembership.objects.get(user=user)
        self.assertEqual((m.bond_code, m.role, m.status), ("AGM", Role.TEAM_LEADER, "ACTIVE"))
        link = CompanyEmailLink.objects.get(user=user)
        self.assertEqual((link.email, link.status, link.verified_via), ("e.akena@itms-ug.com", "VERIFIED", "ADMIN_GRANT"))
        req.refresh_from_db()
        self.assertEqual(req.status, AccessRequest.Status.USED)
        self.assertEqual(req.created_user, user)
        # Single use.
        with self.assertRaises(onboarding.OnboardingError):
            onboarding.redeem_invite(token, code, "eric_again", "Strong-pass-9", "Strong-pass-9")

    def test_signup_with_company_email_is_code_verified(self, *_):
        req = self._request(email="e.akena@itms-ug.com")
        _, token, code = onboarding.grant(req, self.admin)
        user = onboarding.redeem_invite(token, code, "eric2", "Strong-pass-9", "Strong-pass-9")
        self.assertEqual(CompanyEmailLink.objects.get(user=user).verified_via, "SIGNUP_CODE")

    def test_wrong_code_locks_after_max_attempts(self, *_):
        _, token, code = onboarding.grant(self._request(), self.admin)
        wrong = "000000" if code != "000000" else "111111"
        for _i in range(4):
            with self.assertRaises(onboarding.OnboardingError):
                onboarding.redeem_invite(token, wrong, "x_user", "Strong-pass-9", "Strong-pass-9")
        with self.assertRaises(onboarding.OnboardingError) as ctx:
            onboarding.redeem_invite(token, wrong, "x_user", "Strong-pass-9", "Strong-pass-9")
        self.assertIn("locked", str(ctx.exception))
        with self.assertRaises(onboarding.OnboardingError):
            onboarding.redeem_invite(token, code, "x_user", "Strong-pass-9", "Strong-pass-9")
        self.assertFalse(User.objects.filter(username="x_user").exists())

    def test_expired_invite_is_refused_and_can_be_reissued(self, *_):
        req, token, code = onboarding.grant(self._request(), self.admin)
        AccessRequest.objects.filter(pk=req.pk).update(invite_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertIsNone(onboarding.find_live_invite(token))
        req.refresh_from_db()
        _, token2, code2 = onboarding.reissue_invite(req, self.admin)
        self.assertIsNone(onboarding.find_live_invite(token))
        user = onboarding.redeem_invite(token2, code2, "late_user", "Strong-pass-9", "Strong-pass-9")
        self.assertTrue(user.is_active)

    def test_only_admin_can_grant(self, *_):
        tl = User.objects.create_user("some_tl", "tl@gmail.com", "Strong-pass-9")
        with self.assertRaises(CapabilityDenied):
            onboarding.grant(self._request(), tl)

    def test_burned_identity_is_swallowed_and_cannot_be_granted(self, *_):
        BurnedIdentity.objects.create(email="eric.private@gmail.com")
        self.assertIsNone(self._request())
        self.assertFalse(AccessRequest.objects.exists())

    def test_rejected_request(self, *_):
        req = onboarding.reject(self._request(), self.admin, "Not on the roster")
        self.assertEqual(req.status, AccessRequest.Status.REJECTED)

    def test_team_leader_invite_needs_admin_grant(self, *_):
        tl = User.objects.create_user("tl_inv", "tl.inv@gmail.com", "Strong-pass-9")
        BondMembership.objects.create(user=tl, bond_code="AGM", role=Role.TEAM_LEADER)
        CompanyEmailLink.objects.create(user=tl, email="tl.inv@itms-ug.com", status="VERIFIED")
        req = onboarding.invite_by_team_leader(tl, "New Helper", "newhelper@gmail.com", Role.HELPER)
        self.assertEqual((req.status, req.source, req.bond_code), ("PENDING", "TEAM_LEADER", "AGM"))
        with self.assertRaises(onboarding.OnboardingError):
            onboarding.invite_by_team_leader(tl, "Boss", "boss@gmail.com", Role.SUPERVISOR,
                                             company_email="boss@itms-ug.com")

    def test_web_pages(self, *_):
        c = Client()
        self.assertEqual(c.get("/signup/").status_code, 302)          # no invite -> request access
        self.assertEqual(c.get("/access/request/").status_code, 200)
        r = c.post("/access/request/", {
            "full_name": "Web Person", "email": "web@gmail.com", "requested_role": Role.HELPER,
            "bond_code": "AGM"})
        self.assertContains(r, "Request received")
        req = AccessRequest.objects.get(email="web@gmail.com")
        _, token, code = onboarding.grant(req, self.admin)
        self.assertContains(c.get(f"/signup/?invite={token}"), "web@gmail.com")
        r = c.post("/signup/", {"invite": token, "code": code, "username": "web_helper",
                                "password": "Strong-pass-9", "password_confirm": "Strong-pass-9"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(User.objects.filter(username="web_helper", is_active=True).exists())
        self.assertEqual(Client().get(f"/signup/?invite={token}").status_code, 400)  # used

    def test_honeypot_submission_is_ignored(self, *_):
        r = Client().post("/access/request/", {"full_name": "Bot", "email": "bot@spam.com",
                                               "requested_role": Role.HELPER, "bond_code": "AGM",
                                               "website": "http://spam"})
        self.assertContains(r, "Request received")
        self.assertFalse(AccessRequest.objects.exists())
