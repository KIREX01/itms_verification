"""
Tests for 2FA Email Code Verification on User Signup.
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

    @patch("core.services.email_service.send_2fa_verification_code")
    def test_signup_requires_email(self, mock_send):
        res = self.client.post(self.signup_url, {
            "username": "newuser",
            "full_name": "New User",
            "email": "",
            "password": "Password123!",
            "password_confirm": "Password123!",
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "valid work email is required")
        mock_send.assert_not_called()

    @patch("core.services.email_service.send_2fa_verification_code")
    def test_signup_initiates_2fa_and_redirects(self, mock_send):
        mock_send.return_value = (True, "Sent")
        res = self.client.post(self.signup_url, {
            "username": "operator1",
            "full_name": "Test Operator",
            "email": "operator1@info.kirex.online",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, self.verify_url)
        mock_send.assert_called_once()

        # Verify session state
        session = self.client.session
        self.assertIn("pending_signup", session)
        self.assertEqual(session["pending_signup"]["username"], "operator1")
        self.assertEqual(len(session["pending_signup"]["code"]), 6)

    @patch("core.services.email_service.send_2fa_verification_code")
    def test_signup_verify_rejects_wrong_code(self, mock_send):
        mock_send.return_value = (True, "Sent")
        self.client.post(self.signup_url, {
            "username": "operator2",
            "email": "op2@info.kirex.online",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
        })

        # Submit wrong code
        res = self.client.post(self.verify_url, {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid verification code")
        self.assertFalse(User.objects.filter(username="operator2").exists())

    @patch("core.services.email_service.send_2fa_verification_code")
    def test_signup_verify_creates_user_on_correct_code(self, mock_send):
        mock_send.return_value = (True, "Sent")
        self.client.post(self.signup_url, {
            "username": "verified_op",
            "full_name": "Verified Operator",
            "email": "verified@info.kirex.online",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
        })

        session = self.client.session
        correct_code = session["pending_signup"]["code"]

        # Submit correct code
        res = self.client.post(self.verify_url, {"code": correct_code})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("core:dashboard"))

        # Verify user was created in database
        user = User.objects.get(username="verified_op")
        self.assertEqual(user.email, "verified@info.kirex.online")
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("SecurePassword123!"))

        # Verify session pending state cleared
        session = self.client.session
        self.assertNotIn("pending_signup", session)
