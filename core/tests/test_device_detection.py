"""
Tests for Device Detection, Smart Routing, and Cloud Pairing.
"""
from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from core.services import device_service, network_service


class DeviceDetectionTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.client = Client()
        self.mobile_ua = (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
        )
        self.iphone_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        )
        self.desktop_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )

        self.user = User.objects.create_user(
            username="operator_test",
            password="StrongPassword123!",
            first_name="Test",
        )

    def test_device_detection_service_mobile_and_desktop(self):
        req_mobile = self.factory.get("/", HTTP_USER_AGENT=self.mobile_ua)
        req_iphone = self.factory.get("/", HTTP_USER_AGENT=self.iphone_ua)
        req_desktop = self.factory.get("/", HTTP_USER_AGENT=self.desktop_ua)
        req_none = self.factory.get("/")

        self.assertTrue(device_service.is_mobile_device(req_mobile))
        self.assertEqual(device_service.get_client_device_type(req_mobile), "mobile")

        self.assertTrue(device_service.is_mobile_device(req_iphone))
        self.assertEqual(device_service.get_client_device_type(req_iphone), "mobile")

        self.assertFalse(device_service.is_mobile_device(req_desktop))
        self.assertEqual(device_service.get_client_device_type(req_desktop), "desktop")

        self.assertFalse(device_service.is_mobile_device(req_none))
        self.assertEqual(device_service.get_client_device_type(req_none), "desktop")

    def test_get_effective_view_mode_query_and_session(self):
        # Desktop UA requesting mobile view explicitly
        req = self.factory.get("/?view=mobile", HTTP_USER_AGENT=self.desktop_ua)
        req.session = {}
        mode = device_service.get_effective_view_mode(req)
        self.assertEqual(mode, "mobile")
        self.assertEqual(req.session.get("preferred_view"), "mobile")

        # Next request without query param remembers session
        req2 = self.factory.get("/", HTTP_USER_AGENT=self.desktop_ua)
        req2.session = req.session
        self.assertEqual(device_service.get_effective_view_mode(req2), "mobile")

        # Resetting preference
        req3 = self.factory.get("/?view=auto", HTTP_USER_AGENT=self.desktop_ua)
        req3.session = req.session
        self.assertEqual(device_service.get_effective_view_mode(req3), "desktop")
        self.assertNotIn("preferred_view", req3.session)

    def test_dashboard_routes_mobile_device_to_mobile_companion(self):
        self.client.force_login(self.user)

        # Mobile client visits / -> redirects to /mobile/
        res_mobile = self.client.get("/", HTTP_USER_AGENT=self.mobile_ua)
        self.assertEqual(res_mobile.status_code, 302)
        self.assertEqual(res_mobile.url, reverse("core:mobile_companion"))

        # Desktop client visits / -> 200 OK dashboard
        res_desktop = self.client.get("/", HTTP_USER_AGENT=self.desktop_ua)
        self.assertEqual(res_desktop.status_code, 200)

        # Mobile client explicitly requesting desktop view
        res_override = self.client.get("/?view=desktop", HTTP_USER_AGENT=self.mobile_ua)
        self.assertEqual(res_override.status_code, 200)

    def test_mobile_companion_view_allows_switching_to_desktop(self):
        # Visiting /mobile/?view=desktop switches preference and redirects to /
        res = self.client.get(reverse("core:mobile_companion") + "?view=desktop")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("core:dashboard"))

    def test_login_routes_based_on_device(self):
        # Mobile login redirects to /mobile/
        res_mobile = self.client.post(reverse("core:login"), {
            "username": "operator_test",
            "password": "StrongPassword123!",
        }, HTTP_USER_AGENT=self.mobile_ua)
        self.assertEqual(res_mobile.status_code, 302)
        self.assertEqual(res_mobile.url, reverse("core:mobile_companion"))

        self.client.logout()

        # Desktop login redirects to /
        res_desktop = self.client.post(reverse("core:login"), {
            "username": "operator_test",
            "password": "StrongPassword123!",
        }, HTTP_USER_AGENT=self.desktop_ua)
        self.assertEqual(res_desktop.status_code, 302)
        self.assertEqual(res_desktop.url, reverse("core:dashboard"))

    def test_network_service_cloud_domain_detection(self):
        # Test with close.kirex.online
        req_cloud = self.factory.get("/", HTTP_HOST="close.kirex.online", HTTP_X_FORWARDED_PROTO="https")
        info = network_service.get_mobile_connection_info(request=req_cloud)

        self.assertTrue(info["is_cloud"])
        self.assertEqual(info["connection_mode"], "CLOUD_VPS")
        self.assertEqual(info["primary_url"], "https://close.kirex.online/mobile/")
        self.assertTrue(info["candidate_urls"][0]["is_primary"])
        self.assertEqual(info["candidate_urls"][0]["url"], "https://close.kirex.online/mobile/")

        # Test with local host fallback
        req_local = self.factory.get("/", HTTP_HOST="127.0.0.1:8000")
        info_local = network_service.get_mobile_connection_info(request=req_local)
        self.assertFalse(info_local["is_cloud"])
