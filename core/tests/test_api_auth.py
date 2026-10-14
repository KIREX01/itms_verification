"""
Unit tests for Global API and Media Authentication Enforcement.
Verifies that all internal API and media endpoints reject unauthenticated callers
with HTTP 401 Unauthorized, while permitting whitelisted public onboarding routes.
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.services.api_envelope import HEADER_ENVELOPE


class ApiGlobalAuthenticationTestCase(TestCase):
    """Verifies that all API and media endpoints strictly require authentication."""

    def setUp(self):
        self.anonymous_client = Client()
        self.user = User.objects.create_user(
            username="test_auth_operator",
            password="SecurePassword123!",
            email="auth_op@example.com",
        )
        self.auth_client = Client()
        self.auth_client.force_login(self.user)

    def test_unauthenticated_api_endpoints_return_401(self):
        """Unauthenticated requests to data-bearing endpoints must return 401."""
        endpoints = [
            "/api/orders/",
            "/api/pairs/",
            "/api/batches/",
            "/api/history/",
            "/api/stats/",
            "/api/reports/totals/",
            "/api/settings/",
            "/api/pipeline/status/",
            "/api/network/info/",
            "/api/mobile/status/",
        ]
        for url in endpoints:
            res = self.anonymous_client.get(url)
            self.assertEqual(
                res.status_code,
                401,
                f"Expected 401 for unauthenticated GET {url}, got {res.status_code}",
            )
            data = res.json()
            self.assertFalse(data.get("success", True))
            self.assertEqual(data.get("code"), "AUTHENTICATION_REQUIRED")

    def test_unauthenticated_api_envelope_negotiation_401(self):
        """Unauthenticated request with OHAS header returns 401 in OHAS envelope."""
        res = self.anonymous_client.get(
            "/api/pairs/",
            HTTP_ACCEPT=HEADER_ENVELOPE,
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("application/vnd.ohas.envelope+json", res.get("Content-Type", ""))
        payload = res.json()
        self.assertIn("errors", payload)
        self.assertGreater(len(payload["errors"]), 0)
        self.assertEqual(payload["errors"][0]["code"], "AUTHENTICATION_REQUIRED")
        self.assertTrue(res.has_header("X-Trace-ID"))

    def test_unauthenticated_media_returns_401(self):
        """Unauthenticated request to /media/ must be blocked with HTTP 401."""
        res = self.anonymous_client.get("/media/vault/2026-10-10/sample.jpg")
        self.assertEqual(res.status_code, 401)

    def test_unauthenticated_page_views_redirect_to_login(self):
        """Unauthenticated requests to private HTML views must redirect to /login/."""
        pages = ["/", "/upload/", "/mobile/"]
        for url in pages:
            res = self.anonymous_client.get(url)
            self.assertEqual(
                res.status_code,
                302,
                f"Expected 302 redirect for unauthenticated {url}, got {res.status_code}",
            )
            self.assertTrue(res.url.startswith("/login/"))

    def test_public_whitelisted_endpoints_allow_anonymous(self):
        """Public onboarding and readiness routes must be accessible without login."""
        res_login = self.anonymous_client.get(reverse("core:login"))
        self.assertEqual(res_login.status_code, 200)

        res_signup = self.anonymous_client.get(reverse("core:signup"))
        self.assertEqual(res_signup.status_code, 200)

        res_status = self.anonymous_client.get(reverse("core:api_itms_status"))
        self.assertEqual(res_status.status_code, 200)
        status_data = res_status.json()
        self.assertTrue(status_data.get("success"))

    def test_authenticated_user_accesses_api_endpoints(self):
        """Authenticated operators must be able to access general API endpoints."""
        res = self.auth_client.get(reverse("core:api_stats"))
        self.assertEqual(res.status_code, 200)
        stats = res.json()
        self.assertTrue(stats.get("success"))

        res_settings = self.auth_client.get(reverse("core:api_settings"))
        self.assertEqual(res_settings.status_code, 200)
        settings_data = res_settings.json()
        self.assertTrue(settings_data.get("success"))
