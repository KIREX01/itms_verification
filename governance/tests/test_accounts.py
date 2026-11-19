"""Suspend / reinstate / burn take effect immediately and stick."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings

from core.models import AuthorizedDevice, EmailAccessRequest, OperatorITMSSession
from core.services import auth_service, device_access_service
from governance.capabilities import Role
from governance.models import (
    AccountState, AdminAuditLog, BondMembership, BurnedIdentity, CompanyEmailLink,
)
from governance.services import accounts


@override_settings(GOVERNANCE_ENFORCE=True)
class AccountLifecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("bo_admin", "bo@example.com", "Adm1n-pass!")
        self.tl = User.objects.create_user("tl_user", "tl.private@gmail.com", "Tl-pass-123!")
        BondMembership.objects.create(user=self.tl, bond_code="AGM", role=Role.TEAM_LEADER)
        CompanyEmailLink.objects.create(user=self.tl, email="tl@itms-ug.com", status="VERIFIED")
        self.device = AuthorizedDevice.objects.create(
            device_id="tl-laptop", user=self.tl, authorized_email="tl.private@gmail.com",
            status=AuthorizedDevice.Status.APPROVED,
        )
        OperatorITMSSession.objects.create(user=self.tl, itms_email="tl@itms-ug.com",
                                           cookies={"PHPSESSID": "x"}, is_authenticated=True)
        self.client_a = Client()
        self.client_a.force_login(self.tl)
        self.client_b = Client()
        self.client_b.force_login(self.tl)

    def _sessions(self):
        return [s for s in Session.objects.all() if s.get_decoded().get("_auth_user_id") == str(self.tl.pk)]

    def test_suspend_kills_all_sessions_and_blocks_access(self):
        self.assertEqual(len(self._sessions()), 2)
        accounts.suspend(self.tl, "Investigation", actor=self.admin)
        self.assertEqual(self._sessions(), [])
        self.assertIn(self.client_a.get("/api/pairs/").status_code, (401, 302))
        ok, reason, _ = device_access_service.check_operator_session_access(self.tl)
        self.assertFalse(ok)
        self.assertIn("suspended", reason)

    def test_reinstate_restores_access_but_not_old_sessions(self):
        accounts.suspend(self.tl, "check", actor=self.admin)
        accounts.reinstate(self.tl, "cleared", actor=self.admin)
        self.assertEqual(AccountState.objects.get(user=self.tl).status, "ACTIVE")
        c = Client()
        c.force_login(self.tl)
        self.assertEqual(c.get("/api/pairs/").status_code, 200)

    def test_burn_is_total_and_permanent(self):
        accounts.burn(self.tl, "Fraudulent submissions", actor=self.admin)
        self.tl.refresh_from_db()
        self.assertFalse(self.tl.is_active)
        self.assertEqual(AccountState.objects.get(user=self.tl).status, "BURNED")
        self.assertEqual(self._sessions(), [])
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, AuthorizedDevice.Status.REVOKED)
        sess = OperatorITMSSession.objects.get(user=self.tl)
        self.assertEqual(sess.cookies, {})
        self.assertFalse(sess.is_authenticated)
        self.assertFalse(BondMembership.objects.filter(user=self.tl, status="ACTIVE").exists())
        self.assertEqual(CompanyEmailLink.objects.get(user=self.tl).status, "REVOKED")
        self.assertTrue(BurnedIdentity.objects.filter(email="tl.private@gmail.com").exists())
        self.assertTrue(BurnedIdentity.objects.filter(email="tl@itms-ug.com").exists())
        self.assertTrue(AdminAuditLog.objects.filter(action="account.burn").exists())

        with self.assertRaises(accounts.AccountLifecycleError):
            accounts.reinstate(self.tl, actor=self.admin)

    def test_burned_emails_cannot_come_back(self):
        accounts.burn(self.tl, "Fraud", actor=self.admin)
        user, err = auth_service.create_operator_account("again", "Again-pass-1", email="TL.Private@gmail.com")
        self.assertIsNone(user)
        self.assertIn("no longer", err)
        req = device_access_service.preapprove_operator_email("tl@itms-ug.com", send_invite_email=False)
        self.assertEqual(req.status, EmailAccessRequest.Status.BLOCKED)

    def test_burn_requires_reason_and_cannot_target_self(self):
        with self.assertRaises(accounts.AccountLifecycleError):
            accounts.burn(self.tl, "  ", actor=self.admin)
        with self.assertRaises(accounts.AccountLifecycleError):
            accounts.burn(self.admin, "oops", actor=self.admin)

    def test_qr_token_dies_when_account_is_suspended(self):
        token = device_access_service.generate_qr_pairing_token(user=self.tl)
        self.assertEqual(device_access_service.verify_qr_pairing_token(token), self.tl)
        accounts.suspend(self.tl, "lost phone", actor=self.admin)
        self.assertIsNone(device_access_service.verify_qr_pairing_token(token))
        accounts.reinstate(self.tl, actor=self.admin)
        self.assertIsNone(device_access_service.verify_qr_pairing_token(token))  # stamp rotated

    def test_qr_token_requires_a_user(self):
        self.assertEqual(device_access_service.generate_qr_pairing_token(user=None), "")

    def test_remembered_tui_session_dies_on_suspend(self):
        with patch("core.services.auth_service.get_session_file_path") as mock_path:
            import tempfile
            from pathlib import Path
            mock_path.return_value = Path(tempfile.mkdtemp()) / "session.json"
            with patch("core.services.config_service.ensure_operator_accounts_synced"):
                auth_service.save_remembered_session(self.tl, remember=True)
                self.assertEqual(auth_service.get_remembered_session(), self.tl)
                accounts.suspend(self.tl, "check", actor=self.admin)
                self.assertIsNone(auth_service.get_remembered_session())

    def test_audit_log_is_append_only(self):
        accounts.suspend(self.tl, "check", actor=self.admin)
        entry = AdminAuditLog.objects.filter(action="account.suspend").first()
        entry.reason = "tampered"
        with self.assertRaises(ValueError):
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()
