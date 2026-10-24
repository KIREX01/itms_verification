"""
Unit tests for ITMS WebApp reachability heartbeat caching & cooldown:
- Verifies test_connection() caches results for 120s across heartbeats.
- Verifies force=True forces a live probe.
- Verifies ITMS Master Switch pause disables live probes.
- Verifies GET /api/itms/status/ returns cached status and respects ?force=1.
"""
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.services import auth_service, config_service
from core.services.itms_web_client import ITMSWebClient


class HeartbeatReachabilityCacheTests(TestCase):
    def setUp(self):
        # Reset class-level cache before each test
        ITMSWebClient._connection_cache.clear()
        ITMSWebClient._connection_cache_time = 0.0

        self.client = ITMSWebClient()
        self.http_client = Client()
        self.user = User.objects.create_user(username="heartbeatop", password="password123")
        self.http_client.login(username="heartbeatop", password="password123")

    def tearDown(self):
        ITMSWebClient._connection_cache.clear()
        ITMSWebClient._connection_cache_time = 0.0

    def test_test_connection_cooldown_caching(self):
        # Mock session.get to track call count
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Server": "nginx"}

        with patch.object(self.client, "get_session") as mock_sess:
            mock_sess.return_value.get.return_value = mock_resp

            # First ping: live network call
            res1 = self.client.test_connection(force=False, max_age_seconds=60)
            self.assertTrue(res1.get("success"))
            self.assertFalse(res1.get("cached"))
            self.assertEqual(mock_sess.return_value.get.call_count, 1)

            # Second ping within 60s window: returns cached result immediately!
            res2 = self.client.test_connection(force=False, max_age_seconds=60)
            self.assertTrue(res2.get("success"))
            self.assertTrue(res2.get("cached"))
            self.assertIn("cache_age_seconds", res2)
            # ZERO additional HTTP calls were fired!
            self.assertEqual(mock_sess.return_value.get.call_count, 1)

    def test_test_connection_force_bypasses_cache(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Server": "nginx"}

        with patch.object(self.client, "get_session") as mock_sess:
            mock_sess.return_value.get.return_value = mock_resp

            res1 = self.client.test_connection(force=False, max_age_seconds=60)
            self.assertEqual(mock_sess.return_value.get.call_count, 1)

            # Force live probe
            res2 = self.client.test_connection(force=True, max_age_seconds=60)
            self.assertFalse(res2.get("cached"))
            self.assertEqual(mock_sess.return_value.get.call_count, 2)

    def test_test_connection_paused_when_master_switch_disabled(self):
        with patch("core.services.config_service.is_itms_enabled", return_value=False), \
             patch.object(self.client, "get_session") as mock_sess:
            res = self.client.test_connection()
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("paused"))
            self.assertTrue(res.get("cached"))
            # Absolutely no network calls when paused
            mock_sess.assert_not_called()

    def test_auth_service_get_itms_status_heartbeat_caching(self):
        with patch("core.services.itms_web_client.ITMSWebClient.test_connection") as mock_conn:
            mock_conn.return_value = {"success": True, "cached": True, "cache_age_seconds": 15.2, "latency_ms": 25.0}

            status = auth_service.get_itms_status(user=self.user, force_ping=False)
            self.assertTrue(status.get("online"))
            self.assertTrue(status.get("cached"))
            self.assertEqual(status.get("cache_age_seconds"), 15.2)

            # Verify force_ping passed
            mock_conn.assert_called_once_with(force=False, max_age_seconds=120)

    def test_api_itms_status_endpoint_respects_cache_and_force(self):
        url = reverse("core:api_itms_status")

        with patch("core.services.auth_service.get_itms_status") as mock_get_status:
            mock_get_status.return_value = {
                "url": "https://stock.itms.ug",
                "online": True,
                "authenticated": True,
                "user_email": "test@itms.ug",
                "cached": True,
            }

            # Normal periodic heartbeat request
            resp = self.http_client.get(url)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))
            self.assertTrue(data["status"].get("cached"))
            mock_get_status.assert_called_with(user=self.user, force_ping=False)

            # Explicit forced test request
            resp_force = self.http_client.get(f"{url}?force=1")
            self.assertEqual(resp_force.status_code, 200)
            mock_get_status.assert_called_with(user=self.user, force_ping=True)
