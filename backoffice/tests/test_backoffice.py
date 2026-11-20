"""Backoffice: admin-only, every page renders, key actions work end to end."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from core.services import config_service
from governance.capabilities import Role
from governance.models import AccessRequest, AccountState, AdminAuditLog, BondMembership, CompanyEmailLink
from governance.services import onboarding

def admin_client_with_2fa(admin):
    """A signed-in administrator client that has completed 2-step sign-in for this session."""
    from governance.models import AdminTOTP
    from governance.services import totp
    AdminTOTP.objects.update_or_create(user=admin, defaults={"secret_sealed": totp._seal("JBSWY3DPEHPK3PXP"),
                                                             "confirmed": True})
    c = Client()
    c.force_login(admin)
    session = c.session
    session[totp.SESSION_KEY] = True
    session.save()
    return c


PAGES = ["/backoffice/", "/backoffice/access/", "/backoffice/people/", "/backoffice/bonds/",
         "/backoffice/controls/", "/backoffice/devices/", "/backoffice/reports/", "/backoffice/audit/",
         "/backoffice/technical/", "/backoffice/roles/", "/backoffice/signins/", "/backoffice/installs/"]


@override_settings(GOVERNANCE_ENFORCE=True, DEPLOYMENT_ROLE="central")
@patch("core.services.email_service.send_2fa_verification_code", return_value=(True, "sent"))
@patch("core.services.email_service.send_access_request_admin_alert", return_value=(True, "sent"))
class BackofficeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        self.c = admin_client_with_2fa(self.admin)
        self.tl = User.objects.create_user("tl_bo", "tl.bo@gmail.com", "Tl-pass-123!")
        BondMembership.objects.create(user=self.tl, bond_code="AGM", role=Role.TEAM_LEADER)
        CompanyEmailLink.objects.create(user=self.tl, email="tl.bo@itms-ug.com", status="VERIFIED")

    def tearDown(self):
        config_service.set_maintenance_mode(enabled=False)
        config_service.set_itms_enabled(True)

    def test_every_page_renders_for_admin(self, *_):
        for url in PAGES + [f"/backoffice/people/{self.tl.pk}/"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_non_admins_are_refused(self, *_):
        tl_client = Client()
        tl_client.force_login(self.tl)
        for url in PAGES:
            self.assertEqual(tl_client.get(url).status_code, 403, url)
        anon = Client().get("/backoffice/")
        self.assertEqual(anon.status_code, 302)
        self.assertIn("/login/", anon["Location"])

    def test_suspend_requires_password_and_then_works(self, *_):
        url = f"/backoffice/people/{self.tl.pk}/"
        self.c.post(url, {"action": "suspend", "reason": "check", "confirm_password": "wrong"})
        self.assertEqual(AccountState.objects.get(user=self.tl).status, "ACTIVE")
        self.c.post(url, {"action": "suspend", "reason": "check", "confirm_password": "Owner-pass-1"})
        self.assertEqual(AccountState.objects.get(user=self.tl).status, "SUSPENDED")

    def test_burn_requires_typed_username(self, *_):
        url = f"/backoffice/people/{self.tl.pk}/"
        self.c.post(url, {"action": "burn", "reason": "fraud", "confirm_password": "Owner-pass-1",
                          "confirm_username": "nope"})
        self.assertNotEqual(AccountState.objects.get(user=self.tl).status, "BURNED")
        self.c.post(url, {"action": "burn", "reason": "fraud", "confirm_password": "Owner-pass-1",
                          "confirm_username": "tl_bo"})
        self.assertEqual(AccountState.objects.get(user=self.tl).status, "BURNED")

    def test_assign_and_remove_role(self, *_):
        url = f"/backoffice/people/{self.tl.pk}/"
        self.c.post(url, {"action": "assign_role", "bond_code": "EAGLE", "role": Role.SUPERVISOR,
                          "confirm_password": "Owner-pass-1"})
        m = BondMembership.objects.get(user=self.tl, bond_code="EAGLE")
        self.assertEqual(m.status, "ACTIVE")
        self.c.post(url, {"action": "remove_role", "membership_id": m.pk, "confirm_password": "Owner-pass-1"})
        m.refresh_from_db()
        self.assertEqual(m.status, "REMOVED")
        self.assertTrue(AdminAuditLog.objects.filter(action="role.remove").exists())

    def test_grant_access_request_from_page(self, mock_alert, mock_invite):
        req = onboarding.submit_request("New QI", "qi.new@gmail.com", Role.QUALITY_INSPECTOR, "AGM",
                                        company_email="qi.new@itms-ug.com")
        self.c.post(f"/backoffice/access/{req.pk}/", {"action": "grant", "role": Role.QUALITY_INSPECTOR,
                                                       "bond_code": "AGM", "company_email": "qi.new@itms-ug.com"})
        req.refresh_from_db()
        self.assertEqual(req.status, AccessRequest.Status.GRANTED)
        mock_invite.assert_called_once()

    def test_kill_switch_requires_password_and_applies(self, *_):
        self.c.post("/backoffice/controls/", {"action": "switch", "key": "itms.enabled", "value": "off",
                                              "reason": "portal outage", "confirm_password": "bad"})
        self.assertTrue(config_service.is_itms_enabled())
        self.c.post("/backoffice/controls/", {"action": "switch", "key": "itms.enabled", "value": "off",
                                              "reason": "portal outage", "confirm_password": "Owner-pass-1"})
        self.assertFalse(config_service.is_itms_enabled())
        self.assertTrue(AdminAuditLog.objects.filter(action="flag.set", target_id="switch.itms.enabled").exists())

    def test_policy_edit(self, *_):
        from governance.services.identity import allowed_company_domains
        self.c.post("/backoffice/controls/", {"action": "policy", "key": "identity.company_domains",
                                              "value": "itms-ug.com, @close.ug", "confirm_password": "Owner-pass-1"})
        self.assertEqual(allowed_company_domains(), ["itms-ug.com", "close.ug"])



@override_settings(DEPLOYMENT_ROLE="edge")
class EdgeHasNoBackofficeTests(TestCase):
    def test_backoffice_is_not_found_on_installed_apps(self):
        admin = User.objects.create_superuser("edge_owner", "eo@example.com", "Owner-pass-1")
        c = Client()
        c.force_login(admin)
        for url in PAGES:
            self.assertEqual(c.get(url).status_code, 404, url)

    def test_onboarding_redirects_to_central(self):
        r = Client().get("/access/request/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].endswith("/access/request/"))
        self.assertTrue(r["Location"].startswith("http"))
        r = Client().get("/signup/?invite=abc")
        self.assertTrue(r["Location"].endswith("/signup/?invite=abc"))
