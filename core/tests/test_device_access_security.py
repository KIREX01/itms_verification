from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuthorizedDevice, EmailAccessRequest
from core.services import device_access_service

User = get_user_model()


class DeviceAndEmailAccessControlTests(TestCase):
    def setUp(self):
        device_access_service.update_security_access_config({
            "mode": "STANDARD",
            "require_email_approval": False,
            "max_devices_per_account": 4,
            "default_duration_hours": 24,
        })
        self.user = User.objects.create_user(
            username="team_leader_ug",
            email="leader@info.kirex.online",
            password="SecurePassword123!",
            first_name="Samuel",
            last_name="Okello",
            is_active=True,
        )

    def test_existing_user_and_multi_device_account_sharing_never_blocked(self):
        """
        One operator account shared between Team Leader (Desktop) and Fitter (Mobile)
        must automatically authorize both devices without blocking either.
        """
        desktop_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0",
            REMOTE_ADDR="192.168.1.10",
            HTTP_X_DEVICE_MAC="AA:BB:CC:11:22:33",
        )
        mobile_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36",
            REMOTE_ADDR="192.168.1.25",
            HTTP_X_DEVICE_MAC="DD:EE:FF:44:55:66",
        )

        resp1 = desktop_client.post(reverse("core:login"), {
            "username": "team_leader_ug",
            "password": "SecurePassword123!",
        })
        self.assertEqual(resp1.status_code, 302)

        resp2 = mobile_client.post(reverse("core:login"), {
            "username": "team_leader_ug",
            "password": "SecurePassword123!",
        })
        self.assertEqual(resp2.status_code, 302)

        devices = AuthorizedDevice.objects.filter(
            user=self.user,
            status=AuthorizedDevice.Status.APPROVED,
        )
        self.assertEqual(devices.count(), 2)
        macs = set(devices.values_list("mac_address", flat=True))
        self.assertIn("AA:BB:CC:11:22:33", macs)
        self.assertIn("DD:EE:FF:44:55:66", macs)

        self.assertEqual(desktop_client.get(reverse("core:api_stats")).status_code, 200)
        self.assertEqual(mobile_client.get(reverse("core:mobile_companion")).status_code, 200)

    def test_auto_sync_existing_active_accounts_and_auto_fill_mac(self):
        """
        Already-active operator accounts are automatically registered into AuthorizedDevice
        with their host/ARP IP and MAC address auto-populated.
        """
        self.assertEqual(AuthorizedDevice.objects.filter(user=self.user).count(), 0)
        touched = device_access_service.sync_existing_active_accounts_and_devices()
        self.assertGreaterEqual(touched, 1)

        dev = AuthorizedDevice.objects.filter(user=self.user).first()
        self.assertIsNotNone(dev)
        self.assertEqual(dev.status, AuthorizedDevice.Status.APPROVED)
        self.assertTrue(bool(dev.ip_address))
        # Local host physical NIC / UUID MAC is automatically resolved and filled
        expected_local_mac = device_access_service.resolve_local_host_mac_address()
        if expected_local_mac:
            self.assertEqual(dev.mac_address, expected_local_mac)

    def test_qr_code_pairing_token_auto_authorizes_mobile_companion(self):
        """
        Scanning the QR code on the Team Leader's Desktop (?pair_token=...)
        automatically authorizes the Fitter's mobile device and logs into the shared account.
        """
        pair_token = device_access_service.generate_qr_pairing_token(
            user=self.user,
            bond_code="AGM",
        )
        qr_mobile_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile",
            REMOTE_ADDR="10.42.0.88",
            HTTP_X_DEVICE_MAC="12:34:56:78:9A:BC",
        )
        resp = qr_mobile_client.get(f"{reverse('core:mobile_companion')}?pair_token={pair_token}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("itms_device_trust", resp.cookies)

        dev = AuthorizedDevice.objects.filter(mac_address="12:34:56:78:9A:BC").first()
        self.assertIsNotNone(dev)
        self.assertEqual(dev.status, AuthorizedDevice.Status.APPROVED)
        self.assertEqual(dev.linked_via, AuthorizedDevice.LinkMethod.QR_PAIRING)
        self.assertEqual(dev.user, self.user)

    def test_ip_cidr_and_mac_block_enforcement_and_unblock(self):
        """
        Blocking an IP, CIDR subnet, or MAC address blocks API and browser access (403),
        while still allowing access to /access/request/ so the operator can request activation.
        """
        bad_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (X11; Linux x86_64)",
            REMOTE_ADDR="203.0.113.55",
            HTTP_X_DEVICE_MAC="DE:AD:BE:EF:00:01",
        )
        bad_client.force_login(self.user)

        self.assertEqual(bad_client.get(reverse("core:api_stats")).status_code, 200)

        ok, _, _ = device_access_service.block_device_or_network_target(
            "de-ad-be-ef-00-01",
            reason="Unauthorized external laptop",
        )
        self.assertTrue(ok)

        api_resp = bad_client.get(reverse("core:api_stats"))
        self.assertEqual(api_resp.status_code, 403)
        api_data = api_resp.json()
        err_code = api_data.get("code") or (api_data.get("errors") or [{}])[0].get("code")
        self.assertIn(err_code, ("DEVICE_BLOCKED", "DEVICE_ACCESS_BLOCKED"))

        web_resp = bad_client.get(reverse("core:dashboard"))
        self.assertEqual(web_resp.status_code, 403)
        self.assertContains(web_resp, "VERIFICATION", status_code=403)

        req_page = bad_client.get(reverse("core:access_request"))
        self.assertEqual(req_page.status_code, 200)

        device_access_service.unblock_device_or_target("DE:AD:BE:EF:00:01")
        self.assertEqual(bad_client.get(reverse("core:api_stats")).status_code, 200)

        device_access_service.block_device_or_network_target("203.0.113.55")
        self.assertEqual(bad_client.get(reverse("core:api_stats")).status_code, 403)
        device_access_service.unblock_device_or_target("203.0.113.55")
        self.assertEqual(bad_client.get(reverse("core:api_stats")).status_code, 200)

    @patch("core.services.email_service.send_access_activated_email")
    def test_time_bound_email_invitation_expiration_and_renewal(self, mock_invite_email):
        """
        Every email granted access is time-bound (`duration_hours` + `expires_at`),
        automatically blocks with 403 ACCESS_EXPIRED once elapsed, and can be renewed or expired via TUI.
        """
        mock_invite_email.return_value = (True, "Sent")

        # 1. Invite email via Web UI / API for 12 hours
        admin_client = Client()
        admin_client.force_login(self.user)
        invite_resp = admin_client.post(
            reverse("core:api_security_action"),
            data='{"action": "preapprove_email", "email": "shift_op@info.kirex.online", "duration_hours": 12}',
            content_type="application/json",
        )
        self.assertEqual(invite_resp.status_code, 200)
        payload = invite_resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["duration_hours"], 12)
        mock_invite_email.assert_called_once()

        shift_user = User.objects.create_user(
            username="shift_op",
            email="shift_op@info.kirex.online",
            password="ShiftPassword123!",
            is_active=True,
        )
        op_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            REMOTE_ADDR="192.168.10.44",
            HTTP_X_DEVICE_MAC="AA:22:33:44:55:66",
        )
        op_client.force_login(shift_user)

        # Within the 12h window -> 200 OK
        self.assertEqual(op_client.get(reverse("core:api_stats")).status_code, 200)

        # Simulate window elapsing in the past -> automatic 403 ACCESS_EXPIRED
        past_dt = timezone.now() - timedelta(minutes=5)
        EmailAccessRequest.objects.filter(email="shift_op@info.kirex.online").update(expires_at=past_dt)
        AuthorizedDevice.objects.filter(user=shift_user).update(expires_at=past_dt)

        exp_resp = op_client.get(reverse("core:api_stats"))
        self.assertEqual(exp_resp.status_code, 403)
        self.assertEqual(exp_resp.json().get("code"), "ACCESS_EXPIRED")

        # Extend window from TUI service ("7d" -> 168h) -> access immediately restored!
        ok, _ = device_access_service.extend_or_set_expiry("shift_op@info.kirex.online", duration_hours="7d")
        self.assertTrue(ok)
        self.assertEqual(op_client.get(reverse("core:api_stats")).status_code, 200)

        # Expire immediately from TUI -> blocked again with ACCESS_EXPIRED
        ok_exp, _ = device_access_service.expire_immediately("shift_op@info.kirex.online")
        self.assertTrue(ok_exp)
        self.assertEqual(op_client.get(reverse("core:api_stats")).status_code, 403)

    @patch("core.services.email_service.send_2fa_verification_code")
    @patch("core.services.email_service.send_access_request_admin_alert")
    @patch("core.services.email_service.send_access_activated_email")
    def test_strict_email_approval_and_activation_code_unlock(
        self, mock_activated_email, mock_admin_alert, mock_2fa_email
    ):
        mock_2fa_email.return_value = (True, "Sent")
        mock_admin_alert.return_value = (True, "Sent")
        mock_activated_email.return_value = (True, "Sent")

        device_access_service.update_security_access_config({
            "mode": "STRICT",
            "require_email_approval": True,
        })

        new_client = Client(
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            REMOTE_ADDR="192.168.50.77",
            HTTP_X_DEVICE_MAC="AA:11:22:33:44:55",
        )

        resp = new_client.post(reverse("core:signup"), {
            "username": "new_fitter",
            "full_name": "Grace Auma",
            "email": "grace@info.kirex.online",
            "password": "Password123!",
            "password_confirm": "Password123!",
        })
        self.assertEqual(resp.status_code, 302)
        pending = new_client.session["pending_signup"]

        verify_resp = new_client.post(reverse("core:signup_verify"), {
            "action": "verify",
            "code": pending["code"],
        })
        self.assertIn(verify_resp.status_code, (200, 302))

        created_user = User.objects.get(username="new_fitter")
        self.assertFalse(created_user.is_active)

        access_req = EmailAccessRequest.objects.get(email="grace@info.kirex.online")
        self.assertEqual(access_req.status, EmailAccessRequest.Status.PENDING)

        admin_client = Client()
        admin_client.force_login(self.user)
        action_resp = admin_client.post(
            reverse("core:api_security_action"),
            data='{"action": "approve_request", "request_id": %d, "duration_hours": 24}' % access_req.id,
            content_type="application/json",
        )
        self.assertEqual(action_resp.status_code, 200)
        self.assertTrue(action_resp.json()["success"])

        access_req.refresh_from_db()
        created_user.refresh_from_db()
        self.assertTrue(created_user.is_active)
        self.assertEqual(access_req.status, EmailAccessRequest.Status.APPROVED)
        self.assertIsNotNone(access_req.expires_at)

        unlock_resp = new_client.post(reverse("core:access_activate"), {
            "email": "grace@info.kirex.online",
            "activation_code": access_req.activation_code,
        })
        self.assertEqual(unlock_resp.status_code, 302)
        self.assertEqual(unlock_resp["Location"], reverse("core:dashboard"))
        self.assertIn("itms_device_trust", unlock_resp.cookies)

    def test_web_settings_minimal_and_tui_access_pane_instantiates(self):
        """
        Ensures Web UI Settings tab keeps ONLY Time-Bound Email Invitation (no firewall/device tables)
        and TUI AccessControlPane instantiates cleanly.
        """
        tab_settings_path = Path(settings.BASE_DIR) / "core" / "templates" / "core" / "tabs" / "tab_settings.html"
        html = tab_settings_path.read_text(encoding="utf-8")
        self.assertIn("sendTimeBoundEmailInvite()", html)
        self.assertIn("security-invite-duration", html)
        self.assertNotIn("security-devices-tbody", html)
        self.assertNotIn("security-requests-tbody", html)
        self.assertNotIn("security-block-target", html)

        from core.tui.access_pane import AccessControlPane
        from core.tui.app import ITMSOperatorApp
        pane = AccessControlPane(id="access-pane")
        self.assertIsNotNone(pane)
        app = ITMSOperatorApp()
        self.assertTrue(hasattr(app, "action_tab_access"))
