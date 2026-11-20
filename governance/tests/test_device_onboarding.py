"""v1.3.1: device access code -> sign-up / sign-in, roles from the administrator, bonds from ITMS."""
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import AuthorizedDevice
from governance.models import AccessRequest, BondMembership, CompanyEmailLink


def _sent_code(send):
    return send.call_args.kwargs["code"]


@override_settings(DEPLOYMENT_ROLE="central")
class SignupApprovesDeviceTests(TestCase):
    def test_invite_signup_approves_the_device(self):
        from governance.services import onboarding
        cache.clear()
        admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        req = AccessRequest.objects.create(full_name="Akello Grace", email="akello@gmail.com",
                                           requested_role="HELPER", bond_code="AGM")
        _req, token, code = onboarding.grant(req, admin)
        c = Client()
        r = c.post("/signup/", {"invite": token, "code": code, "username": "akello",
                                "password": "Str0ng-pass!x", "password_confirm": "Str0ng-pass!x"})
        self.assertEqual(r.status_code, 302)
        dev = AuthorizedDevice.objects.get(user__username="akello")
        self.assertEqual(dev.status, "APPROVED")
        from core.services.device_access_service import DEVICE_TRUST_COOKIE_NAME
        self.assertIn(DEVICE_TRUST_COOKIE_NAME, r.cookies)


class EmailSignInTests(TestCase):
    def test_sign_in_with_email(self):
        from governance.auth_backend import LockoutModelBackend
        cache.clear()
        user = User.objects.create_user("okello", "okello@gmail.com", "Str0ng-pass!x")
        self.assertEqual(LockoutModelBackend().authenticate(None, username="OKELLO@gmail.com",
                                                            password="Str0ng-pass!x"), user)
        CompanyEmailLink.objects.create(user=user, email="okello@itms-ug.com", status="VERIFIED")
        self.assertEqual(LockoutModelBackend().authenticate(None, username="okello@itms-ug.com",
                                                            password="Str0ng-pass!x"), user)

    @override_settings(DEPLOYMENT_ROLE="central")
    def test_central_login_never_prefills_last_username(self):
        with mock.patch("core.services.auth_service.get_operator_preferences",
                        return_value={"last_username": "someone_else"}):
            User.objects.create_user("x_user", "x@example.com", "x")
            r = Client().get("/login/")
        self.assertNotContains(r, "someone_else")
        self.assertContains(r, "android-chrome-192x192.png")


@override_settings(DEPLOYMENT_ROLE="central")
class SignupIdentityConsistencyTests(TestCase):
    def setUp(self):
        from governance.services import onboarding
        cache.clear()
        self.onboarding = onboarding
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        req = AccessRequest.objects.create(full_name="okello  samuel", email="Okello.S@Gmail.com",
                                           requested_role="HELPER", bond_code="AGM")
        self.req, self.token, self.code = onboarding.grant(req, self.admin)

    def test_signup_page_suggests_username_and_name(self):
        page = Client().get(f"/signup/?invite={self.token}").content.decode()
        self.assertIn('value="okello.samuel"', page)
        self.assertIn('name="full_name"', page)

    def test_name_email_username_are_consistent_and_devices_linked(self):
        # A phone unlocked with the invite email before the account existed.
        early = AuthorizedDevice.objects.create(device_id="DEV-EARLY1", status="APPROVED",
                                                authorized_email="okello.s@gmail.com", request_name="okello")
        user = self.onboarding.redeem_invite(self.token, self.code, "Okello.Samuel", "Str0ng-pass!x",
                                             "Str0ng-pass!x", full_name="Okello Samuel")
        self.assertEqual(user.username, "okello.samuel")
        self.assertEqual(user.email, "okello.s@gmail.com")
        self.assertEqual(user.get_full_name(), "Okello Samuel")
        early.refresh_from_db()
        self.assertEqual((early.user_id, early.request_name), (user.pk, "Okello Samuel"))
        self.assertEqual(AccessRequest.objects.get(pk=self.req.pk).full_name, "Okello Samuel")
        from governance.auth_backend import LockoutModelBackend
        self.assertEqual(LockoutModelBackend().authenticate(None, username="OKELLO.SAMUEL", password="Str0ng-pass!x"), user)

    def test_bad_usernames_rejected(self):
        for bad in ("ok", "okello samuel", "okello@x", "-okello"):
            with self.assertRaises(self.onboarding.OnboardingError):
                self.onboarding.redeem_invite(self.token, self.code, bad, "Str0ng-pass!x", "Str0ng-pass!x")

    def test_username_unique_ignoring_case(self):
        User.objects.create_user("okello.samuel", "other@example.com", "x")
        with self.assertRaises(self.onboarding.OnboardingError):
            self.onboarding.redeem_invite(self.token, self.code, "OKELLO.SAMUEL", "Str0ng-pass!x", "Str0ng-pass!x")


DETAILS = {"action": "send", "first_name": "akello", "last_name": "Grace", "email": "Akello.Grace@Gmail.com"}


@override_settings(DEPLOYMENT_ROLE="central", CLOSE_DEVICE_ADMISSION=True)
class DeviceCodeFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = Client()

    def _send(self, data=None):
        with mock.patch("core.services.email_service.send_device_code_email", return_value=(True, "")) as send:
            r = self.c.post("/device/", data or DETAILS)
        return r, send

    def test_blocked_page_asks_for_names_and_email(self):
        body = self.c.get("/").content.decode()
        for text in ("isn't authorised to use CLOSE Copilot", "First name", "Last name", "Email",
                     "Send access code", "android-chrome-192x192.png"):
            self.assertIn(text, body)

    def test_validation(self):
        for bad in ({**DETAILS, "email": "not-an-email"}, {**DETAILS, "first_name": ""}, {**DETAILS, "last_name": "1"}):
            r, send = self._send(bad)
            self.assertEqual(r.status_code, 403)
            send.assert_not_called()
        self.assertFalse(AuthorizedDevice.objects.exists())

    def test_new_person_code_then_signup_then_no_role_landing(self):
        r, send = self._send()
        self.assertRedirects(r, "/device/?step=code", fetch_redirect_response=False)
        self.assertEqual(send.call_args.kwargs["to_email"], "akello.grace@gmail.com")
        r = self.c.post("/device/", {"action": "verify", "code": _sent_code(send)})
        self.assertRedirects(r, "/signup/", fetch_redirect_response=False)
        page = self.c.get("/signup/").content.decode()
        self.assertIn("akello.grace@gmail.com", page)
        self.assertIn('value="Akello"', page)
        self.assertIn('value="Grace"', page)
        self.assertIn('value="akello.grace"', page)  # suggested username
        r = self.c.post("/signup/", {"first_name": "Akello", "last_name": "Grace", "username": "akello.grace",
                                     "password": "12345678", "password_confirm": "12345678"})
        self.assertEqual(r.status_code, 400)  # password rules
        User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        with mock.patch("core.services.email_service.send_new_signup_alert", return_value=(True, "")) as alert:
            r = self.c.post("/signup/", {"first_name": "Akello", "last_name": "Grace", "username": "akello.grace",
                                         "password": "Kampala-Kits-2026", "password_confirm": "Kampala-Kits-2026"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        alert.assert_called_once()
        user = User.objects.get(username="akello.grace")
        self.assertEqual((user.first_name, user.last_name, user.email), ("Akello", "Grace", "akello.grace@gmail.com"))
        dev = AuthorizedDevice.objects.get()
        self.assertEqual((dev.status, dev.user_id), ("APPROVED", user.pk))
        with override_settings(GOVERNANCE_ENFORCE=True):
            self.assertContains(self.c.get("/"), "will assign your role", status_code=403)

    def test_existing_person_code_then_sign_in(self):
        user = User.objects.create_user("akello", "akello.grace@gmail.com", "Str0ng-pass!x")
        _r, send = self._send()
        r = self.c.post("/device/", {"action": "verify", "code": _sent_code(send)})
        self.assertRedirects(r, "/login/?unlocked=1", fetch_redirect_response=False)
        self.assertEqual(AuthorizedDevice.objects.get().user, user)
        self.assertContains(self.c.get("/login/?unlocked=1"), "Device unlocked")

    def test_company_email_signup_is_attached(self):
        _r, send = self._send({**DETAILS, "email": "akello@itms-ug.com"})
        self.c.post("/device/", {"action": "verify", "code": _sent_code(send)})
        self.c.post("/signup/", {"first_name": "Akello", "last_name": "Grace", "username": "akello",
                                 "password": "Kampala-Kits-2026", "password_confirm": "Kampala-Kits-2026"})
        link = CompanyEmailLink.objects.get(user__username="akello")
        self.assertEqual((link.email, link.status), ("akello@itms-ug.com", "VERIFIED"))

    def test_signup_needs_a_verified_email(self):
        self.assertRedirects(self.c.get("/signup/"), "/access/request/", fetch_redirect_response=False)
        self.c.post("/signup/", {"username": "x_user", "password": "Kampala-Kits-2026",
                                 "password_confirm": "Kampala-Kits-2026"})
        self.assertFalse(User.objects.filter(username="x_user").exists())

    def test_wrong_code_limit_and_expiry(self):
        _r, send = self._send()
        good = _sent_code(send)
        bad = "000000" if good != "000000" else "111111"
        for _ in range(5):
            self.c.post("/device/", {"action": "verify", "code": bad})
        self.assertContains(self.c.post("/device/", {"action": "verify", "code": good}), "Too many wrong codes",
                            status_code=403)
        _r, send = self._send()
        AuthorizedDevice.objects.update(access_code_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.c.post("/device/", {"action": "verify", "code": _sent_code(send)}), "expired",
                            status_code=403)

    def test_burned_and_suspended_get_no_code(self):
        from governance.models import BurnedIdentity
        from governance.services import accounts
        BurnedIdentity.objects.create(email="akello.grace@gmail.com")
        _r, send = self._send()
        send.assert_not_called()
        admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        susp = User.objects.create_user("susp", "susp@gmail.com", "x")
        accounts.suspend(susp, "check", actor=admin)
        _r, send = self._send({**DETAILS, "email": "susp@gmail.com"})
        send.assert_not_called()

    def test_without_email_sending_the_administrator_approves(self):
        with mock.patch("governance.services.device_admission.email_codes_available", return_value=False), \
                mock.patch("governance.services.device_admission._notify_admins") as alert:
            r = self.c.post("/device/", DETAILS)
        self.assertRedirects(r, "/device/?step=waiting", fetch_redirect_response=False)
        alert.assert_called_once()


@override_settings(DEPLOYMENT_ROLE="central", GOVERNANCE_ENFORCE=True)
class RolesAndItmsBondsTests(TestCase):
    def setUp(self):
        cache.clear()
        from backoffice.tests.test_backoffice import admin_client_with_2fa
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        self.c = admin_client_with_2fa(self.admin)
        self.p = User.objects.create_user("worker", "worker@gmail.com", "x")

    def _post(self, data):
        return self.c.post(f"/backoffice/people/{self.p.pk}/", {**data, "confirm_password": "Owner-pass-1"})

    def _bonds(self):
        return set(BondMembership.objects.filter(user=self.p, status="ACTIVE").values_list("bond_code", "role"))

    def _groups(self):
        return set(self.p.groups.values_list("name", flat=True))

    def test_role_assigned_bonds_from_itms(self):
        from governance.services.identity import record_itms_warehouse_access
        self._post({"action": "set_person_roles", "roles": ["TEAM_LEADER"]})
        self.assertEqual(self._bonds(), set())  # no ITMS yet
        CompanyEmailLink.objects.create(user=self.p, email="worker@itms-ug.com", status="VERIFIED")
        record_itms_warehouse_access("worker@itms-ug.com", ["AGM", "SPIRO"])
        self.assertEqual(self._bonds(), {("AGM", "TEAM_LEADER"), ("SPIRO", "TEAM_LEADER")})
        self.assertEqual(self._groups(), {"Team Leader"})
        record_itms_warehouse_access("worker@itms-ug.com", ["AGM"])  # SPIRO removed in ITMS
        self.assertEqual(self._bonds(), {("AGM", "TEAM_LEADER")})
        self._post({"action": "set_person_roles", "roles": ["TEAM_LEADER", "SUPERVISOR"]})
        self.assertEqual(self._bonds(), {("AGM", "TEAM_LEADER"), ("AGM", "SUPERVISOR")})
        self._post({"action": "set_person_roles", "roles": []})
        self.assertEqual(self._bonds(), set())
        self.assertEqual(self._groups(), set())

    def test_helper_manual_bond(self):
        self._post({"action": "add_manual_bond", "bond_code": "AGM"})  # needs a role first
        self.assertEqual(self._bonds(), set())
        self._post({"action": "set_person_roles", "roles": ["HELPER"]})
        self._post({"action": "add_manual_bond", "bond_code": "agm"})
        self.assertEqual(self._bonds(), {("AGM", "HELPER")})
        page = self.c.get(f"/backoffice/people/{self.p.pk}/").content.decode()
        self.assertIn("added by you", page)
        self.assertTrue(re.search(r'value="HELPER"\s+checked', page))
        self._post({"action": "remove_manual_bond", "bond_code": "AGM"})
        self.assertEqual(self._bonds(), set())

    def test_needs_role_tile(self):
        self.assertContains(self.c.get("/backoffice/"), "New sign-ups waiting for a role")
        self.assertContains(self.c.get("/backoffice/people/?needs_role=1"), "worker")

    def test_team_leader_without_bonds_can_connect_itms(self):
        self._post({"action": "set_person_roles", "roles": ["TEAM_LEADER"]})
        tl = Client()
        tl.force_login(self.p)
        self.assertContains(tl.get("/"), "Connect your ITMS account", status_code=403)
        with mock.patch("core.services.auth_service.connect_itms_account", return_value=(False, "bad password")):
            r = tl.post("/api/itms/connect/", {"email": "worker@itms-ug.com", "password": "x"})
        self.assertNotEqual(r.status_code, 403)  # allowed through governance; ITMS itself said no
