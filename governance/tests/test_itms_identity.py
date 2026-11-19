"""ITMS Connect only accepts the person's company email; a successful login attaches it."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.services import auth_service
from governance.capabilities import Role
from governance.models import BondMembership, CompanyEmailLink


def _fake_client(ok=True):
    client = MagicMock()
    client.login.return_value = (ok, "ok" if ok else "bad credentials", {})
    client.base_url = "https://stock.itms.ug"
    return client


@override_settings(GOVERNANCE_ENFORCE=True)
class ItmsIdentityTests(TestCase):
    def setUp(self):
        self.tl = User.objects.create_user("tl_conn", "tl.private@gmail.com", "Str0ng-pass!")
        BondMembership.objects.create(user=self.tl, bond_code="AGM", role=Role.TEAM_LEADER)

    @patch("core.services.auth_service.save_operator_preferences")
    @patch("core.services.itms_web_client.get_web_client")
    def test_attached_email_must_match(self, mock_get, _prefs):
        CompanyEmailLink.objects.create(user=self.tl, email="tl.conn@itms-ug.com", status="VERIFIED")
        mock_get.return_value = _fake_client()
        ok, msg = auth_service.connect_itms_account("someone.else@itms-ug.com", "pw", user=self.tl)
        self.assertFalse(ok)
        self.assertIn("tl.conn@itms-ug.com", msg)
        mock_get.return_value.login.assert_not_called()
        ok, _ = auth_service.connect_itms_account("TL.Conn@itms-ug.com", "pw", user=self.tl)
        self.assertTrue(ok)

    @patch("core.services.auth_service.save_operator_preferences")
    @patch("core.services.itms_web_client.get_web_client")
    def test_personal_email_refused_when_none_attached(self, mock_get, _prefs):
        mock_get.return_value = _fake_client()
        ok, msg = auth_service.connect_itms_account("tl.private@gmail.com", "pw", user=self.tl)
        self.assertFalse(ok)
        self.assertIn("company email", msg)

    @patch("core.services.auth_service.save_operator_preferences")
    @patch("core.services.itms_web_client.get_web_client")
    def test_successful_itms_login_attaches_company_email(self, mock_get, _prefs):
        mock_get.return_value = _fake_client()
        ok, _ = auth_service.connect_itms_account("tl.conn@itms-ug.com", "pw", user=self.tl)
        self.assertTrue(ok)
        link = CompanyEmailLink.objects.get(user=self.tl)
        self.assertEqual((link.email, link.status, link.verified_via), ("tl.conn@itms-ug.com", "VERIFIED", "ITMS_LOGIN"))

    @patch("core.services.auth_service.save_operator_preferences")
    @patch("core.services.itms_web_client.get_web_client")
    def test_failed_login_attaches_nothing(self, mock_get, _prefs):
        mock_get.return_value = _fake_client(ok=False)
        ok, _ = auth_service.connect_itms_account("tl.conn@itms-ug.com", "pw", user=self.tl)
        self.assertFalse(ok)
        self.assertFalse(CompanyEmailLink.objects.filter(user=self.tl).exists())

    @patch("core.services.auth_service.save_operator_preferences")
    @patch("core.services.itms_web_client.get_web_client")
    def test_cannot_take_someone_elses_company_email(self, mock_get, _prefs):
        other = User.objects.create_user("other", "o@gmail.com", "Str0ng-pass!")
        CompanyEmailLink.objects.create(user=other, email="shared@itms-ug.com", status="VERIFIED")
        mock_get.return_value = _fake_client()
        ok, msg = auth_service.connect_itms_account("shared@itms-ug.com", "pw", user=self.tl)
        self.assertFalse(ok)
        self.assertIn("another person", msg)
