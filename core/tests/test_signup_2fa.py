"""
Email code helpers. (Self-service signup was replaced by request-access onboarding;
see governance/tests/test_onboarding.py.)
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.services import email_service


class Signup2FATestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.signup_url = reverse("core:signup")
        self.verify_url = reverse("core:signup_verify")

    def test_generate_2fa_code(self):
        code = email_service.generate_2fa_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())






    def test_get_resend_from_email_formatting(self):
        """Verify sender address adheres to CLOSE Copilot standards."""
        from django.test import override_settings
        from core.services.email_service import get_resend_from_email

        # Default empty string should return standard CLOSE Copilot mailbox
        with override_settings(RESEND_FROM_EMAIL=""):
            self.assertEqual(get_resend_from_email(), "CLOSE Copilot <notifications@info.kirex.online>")

        # Bare email address without angle brackets should be wrapped with brand display name
        with override_settings(RESEND_FROM_EMAIL="notifications@info.kirex.online"):
            self.assertEqual(get_resend_from_email(), "CLOSE Copilot <notifications@info.kirex.online>")

        # Legacy auth address should be upgraded automatically
        with override_settings(RESEND_FROM_EMAIL="auth@info.kirex.online"):
            self.assertEqual(get_resend_from_email(), "CLOSE Copilot <notifications@info.kirex.online>")

        # Full RFC 5322 string should be preserved with brand
        with override_settings(RESEND_FROM_EMAIL="Security <security@info.kirex.online>"):
            self.assertEqual(get_resend_from_email(), "CLOSE Copilot <security@info.kirex.online>")
