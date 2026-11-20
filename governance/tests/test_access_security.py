"""v1.3.0 access security: device admission, sign-in lockout & history, admin 2-step sign-in, trusted proxy."""
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings

from core.models import AuthorizedDevice
from core.services import auth_service, device_access_service
from core.services.security_envelope import extract_client_ip
from governance.models import AdminTOTP, InstallRegistration, LoginAttempt
from governance.services import device_admission, login_guard, policy_sync, totp

NO_EMAIL = patch("core.services.email_service.send_access_request_admin_alert", return_value=(True, "sent"))


@override_settings(CLOSE_DEVICE_ADMISSION=True, DEPLOYMENT_ROLE="central")
class DeviceAdmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        NO_EMAIL.start()

    def tearDown(self):
        NO_EMAIL.stop()

    def test_unknown_device_sees_only_blocked_page(self):
        c = Client()
        r = c.get("/login/")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "authorised to use CLOSE Copilot", status_code=403)
        self.assertNotContains(r, 'name="password"', status_code=403)
        r = c.get("/api/pairs/", HTTP_ACCEPT="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "DEVICE_NOT_APPROVED")
        self.assertEqual(c.post("/login/", {"username": "op", "password": "Str0ng-pass!"}).status_code, 403)

    def test_request_then_admin_approval_then_sign_in(self):
        c = Client(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0")
        r = c.post("/device/", {"first_name": "Akena", "last_name": "Eric", "email": "eric@gmail.com",
                                "client_report": '{"screen": "1920x1080", "timezone": "Africa/Kampala"}'})
        self.assertEqual(r.status_code, 302)
        dev = AuthorizedDevice.objects.get(request_email="eric@gmail.com")
        self.assertEqual(dev.status, AuthorizedDevice.Status.PENDING)
        self.assertEqual(dev.os_name, "Windows")
        self.assertEqual(dev.device_info["client"]["timezone"], "Africa/Kampala")
        r = c.get("/")
        self.assertContains(r, device_admission.device_code(dev.device_id), status_code=403)

        device_admission.approve(dev, self.admin, user=self.user)
        self.assertEqual(c.get("/login/").status_code, 200)
        self.assertEqual(c.post("/login/", {"username": "op", "password": "Str0ng-pass!"}).status_code, 302)

    def test_forged_device_identifiers_do_not_admit(self):
        approved = AuthorizedDevice.objects.create(device_id="DEV-APPROVED1", status="APPROVED")
        c = Client(HTTP_X_DEVICE_ID=approved.device_id)
        self.assertEqual(c.get(f"/login/?device_id={approved.device_id}").status_code, 403)
        c.cookies["itms_device_trust"] = f"{approved.device_id}|x|1|forged"
        self.assertEqual(c.get("/login/").status_code, 403)

    def test_blocked_device_is_refused_and_sessions_end(self):
        c = Client()
        c.post("/device/", {"first_name": "Test", "last_name": "Person", "email": "someone@example.com"})
        dev = AuthorizedDevice.objects.latest("id")
        device_admission.approve(dev, self.admin, user=self.user)
        c.post("/login/", {"username": "op", "password": "Str0ng-pass!"})
        self.assertEqual(c.get("/api/me/capabilities/").status_code, 200)
        device_admission.block(dev, self.admin, reason="lost phone")
        r = c.get("/")
        self.assertContains(r, "blocked", status_code=403)

    def test_qr_scan_creates_pending_request_for_that_person(self):
        token = device_access_service.generate_qr_pairing_token(user=self.user)
        phone = Client(HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14; SM-A546E) Mobile")
        r = phone.get(f"/mobile/?pair_token={token}")
        self.assertEqual(r.status_code, 403)
        dev = AuthorizedDevice.objects.get(user=self.user)
        self.assertEqual((dev.status, dev.device_type), ("PENDING", "MOBILE"))
        self.assertIn("QR", dev.request_note)

    def test_cli_approve_by_code(self):
        Client().post("/device/", {"first_name": "Test", "last_name": "Person", "email": "owner-laptop@example.com"})
        dev = AuthorizedDevice.objects.latest("id")
        call_command("itms_device", "approve", device_admission.device_code(dev.device_id), "--user", "owner")
        dev.refresh_from_db()
        self.assertEqual((dev.status, dev.user), ("APPROVED", self.admin))

    def test_request_rate_limit_per_network(self):
        for _ in range(device_admission.REQUEST_RATE_PER_IP_PER_HOUR):
            Client().post("/device/", {"first_name": "Test", "last_name": "Person", "email": "spam@example.com"})
        r = Client().post("/device/", {"first_name": "Test", "last_name": "Person", "email": "spam@example.com"})
        self.assertContains(r, "Too many", status_code=403)

    @override_settings(DEPLOYMENT_ROLE="edge", CLOSE_REQUIRE_ENROLLMENT=False)
    def test_laptop_itself_is_always_admitted(self):
        self.assertEqual(Client(REMOTE_ADDR="127.0.0.1").get("/login/").status_code, 200)
        self.assertEqual(Client(REMOTE_ADDR="192.168.137.20").get("/login/").status_code, 403)


@override_settings(DEPLOYMENT_ROLE="central")
class DeviceSyncTests(TestCase):
    def test_install_requests_reach_central_and_decisions_come_back(self):
        admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        inst, code = policy_sync.create_install("AGM laptop", admin)
        policy_sync.enroll(code)
        inst.refresh_from_db()
        policy_sync._ingest_device_requests(inst, [{"device_id": "DEV-PHONE1", "request_name": "Helper",
                                                    "os_name": "Android", "device_model": "SM-A546E"}])
        dev = AuthorizedDevice.objects.get(device_id="DEV-PHONE1")
        self.assertEqual((dev.status, dev.install_id, dev.device_model), ("PENDING", inst.install_id, "SM-A546E"))
        device_admission.approve(dev, admin)
        decisions = policy_sync._device_decisions(inst)
        self.assertEqual(decisions[0]["status"], "APPROVED")
        dev.status = "PENDING"
        dev.save()
        policy_sync._apply_device_decisions(decisions)
        dev.refresh_from_db()
        self.assertEqual(dev.status, "APPROVED")

    def test_install_cannot_touch_another_installs_device(self):
        admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        other, _ = policy_sync.create_install("Other", admin)
        AuthorizedDevice.objects.create(device_id="DEV-X1", status="APPROVED", install_id="someone-else")
        policy_sync._ingest_device_requests(other, [{"device_id": "DEV-X1", "request_name": "hijack"}])
        self.assertEqual(AuthorizedDevice.objects.get(device_id="DEV-X1").request_name, "")


class LoginGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")

    def test_generic_error_never_reveals_usernames(self):
        _, err1 = auth_service.authenticate_operator("op", "wrong")
        _, err2 = auth_service.authenticate_operator("no_such_user", "wrong")
        self.assertEqual(err1, err2)

    def test_lockout_after_five_failures_even_with_right_password(self):
        for _ in range(login_guard.USER_FAIL_LIMIT):
            auth_service.authenticate_operator("op", "wrong")
        user, err = auth_service.authenticate_operator("op", "Str0ng-pass!")
        self.assertIsNone(user)
        self.assertIn("Too many failed", err)
        login_guard.clear_failures("op")
        user, _ = auth_service.authenticate_operator("op", "Str0ng-pass!")
        self.assertEqual(user, self.user)

    def test_every_attempt_is_recorded_for_web_logins(self):
        Client(REMOTE_ADDR="10.1.2.3").post("/login/", {"username": "op", "password": "bad"})
        Client(REMOTE_ADDR="10.1.2.3").post("/login/", {"username": "op", "password": "Str0ng-pass!"})
        attempts = list(LoginAttempt.objects.filter(username="op").order_by("created_at"))
        self.assertEqual([a.success for a in attempts], [False, True])
        self.assertEqual(attempts[0].ip_address, "10.1.2.3")
        self.assertEqual(attempts[0].channel, "web")


class TotpTests(TestCase):
    def test_rfc6238_test_vector(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
        self.assertEqual(totp.current_code(secret, at=59), "287082")
        self.assertEqual(totp.current_code(secret, at=1111111109), "081804")

    def test_enroll_verify_replay_and_recovery(self):
        admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        secret, uri = totp.start_enrollment(admin)
        self.assertIn("otpauth://totp/", uri)
        self.assertEqual(totp.start_enrollment(admin)[0], secret)  # reload keeps the same secret
        recovery = totp.confirm_enrollment(admin, totp.current_code(secret))
        self.assertEqual(len(recovery), 8)
        self.assertTrue(totp.is_enrolled(admin))
        code = totp.current_code(secret, at=time.time() + 30)
        self.assertTrue(totp.verify(admin, code))
        self.assertFalse(totp.verify(admin, code))  # replay refused
        self.assertTrue(totp.verify(admin, recovery[0]))
        self.assertFalse(totp.verify(admin, recovery[0]))  # recovery codes are single-use
        self.assertNotIn(secret, AdminTOTP.objects.get(user=admin).secret_sealed)


@override_settings(DEPLOYMENT_ROLE="central", GOVERNANCE_ENFORCE=True)
class AdminTwoStepTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "Owner-pass-1")
        self.c = Client()
        self.c.force_login(self.admin)

    def test_backoffice_requires_setup_then_code(self):
        r = self.c.get("/backoffice/")
        self.assertEqual(r["Location"], "/backoffice/2fa/setup/")
        page = self.c.get("/backoffice/2fa/setup/")
        self.assertContains(page, "<svg")
        secret = totp._unseal(AdminTOTP.objects.get(user=self.admin).secret_sealed)
        r = self.c.post("/backoffice/2fa/setup/", {"code": totp.current_code(secret)})
        self.assertContains(r, "recovery codes")
        self.assertEqual(self.c.get("/backoffice/").status_code, 200)

        fresh = Client()
        fresh.force_login(self.admin)
        r = fresh.get("/backoffice/people/")
        self.assertTrue(r["Location"].startswith("/backoffice/2fa/"))
        r = fresh.post("/backoffice/2fa/", {"code": "000000", "next": "/backoffice/people/"})
        self.assertEqual(r.status_code, 200)
        r = fresh.post("/backoffice/2fa/", {"code": totp.current_code(secret, at=time.time() + 30),
                                            "next": "/backoffice/people/"})
        self.assertEqual(r["Location"], "/backoffice/people/")

    def test_django_admin_needs_two_step_on_central(self):
        r = self.c.get("/admin/")
        self.assertIn("/backoffice/2fa", r["Location"])

    @override_settings(DEPLOYMENT_ROLE="edge")
    def test_django_admin_and_backoffice_off_on_laptops(self):
        self.assertEqual(self.c.get("/admin/").status_code, 404)
        r = self.c.get("/backoffice/")
        self.assertContains(r, "central server", status_code=404)


class TrustedProxyTests(TestCase):
    def test_forwarded_for_only_trusted_from_own_proxy(self):
        rf = RequestFactory()
        r = rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 41.210.1.2")
        self.assertEqual(extract_client_ip(r), "41.210.1.2")  # what nginx saw, not what the client claimed
        r = rf.get("/", REMOTE_ADDR="41.210.1.2", HTTP_X_FORWARDED_FOR="127.0.0.1")
        self.assertEqual(extract_client_ip(r), "41.210.1.2")
