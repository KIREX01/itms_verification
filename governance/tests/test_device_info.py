"""Device information collected from browsers, phones and installs."""
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from core.models import AuthorizedDevice
from core.services import device_info_service as dis
from governance.services.policy_sync import _clean_host_info

WIN_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
ANDROID = ("Mozilla/5.0 (Linux; Android 14; SM-A546E Build/UP1A.231005.007) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.6778.81 Mobile Safari/537.36")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
EDGE = WIN_CHROME + " Edg/131.0.2903.70"


class UserAgentParsingTests(TestCase):
    def test_windows_chrome(self):
        p = dis.parse_user_agent(WIN_CHROME)
        self.assertEqual((p["os_name"], p["browser"], p["browser_version"]), ("Windows", "Chrome", "131.0.0.0"))

    def test_edge_detected_before_chrome(self):
        self.assertEqual(dis.parse_user_agent(EDGE)["browser"], "Edge")

    def test_android_model(self):
        p = dis.parse_user_agent(ANDROID)
        self.assertEqual((p["os_name"], p["os_version"], p["device_model"], p["is_mobile"]),
                         ("Android", "14", "SM-A546E", "1"))

    def test_iphone(self):
        p = dis.parse_user_agent(IPHONE)
        self.assertEqual((p["os_name"], p["os_version"], p["browser"], p["device_model"]),
                         ("iOS", "17.5", "Safari", "iPhone"))

    def test_windows_11_from_client_hints(self):
        self.assertEqual(dis._windows_from_hints({"platform_version": '"15.0.0"'.strip('"')}), "11")
        self.assertEqual(dis._windows_from_hints({"platform_version": "10.0.0"}), "10")


class DeviceRecordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", "op@gmail.com", "Str0ng-pass!")

    def test_device_records_os_browser_and_hints(self):
        c = Client(HTTP_USER_AGENT=WIN_CHROME, HTTP_SEC_CH_UA_PLATFORM='"Windows"',
                   HTTP_SEC_CH_UA_PLATFORM_VERSION='"15.0.0"', HTTP_SEC_CH_UA_ARCH='"x86"')
        c.force_login(self.user)
        r = c.get("/api/me/capabilities/")
        self.assertIn("Sec-CH-UA-Model", r["Accept-CH"])
        dev = AuthorizedDevice.objects.filter(user=self.user).first()
        self.assertIsNotNone(dev)
        self.assertEqual((dev.os_name, dev.os_version, dev.browser), ("Windows", "11", "Chrome"))
        self.assertEqual(dev.device_info["server"]["hints"]["arch"], "x86")

    def test_browser_report_is_sanitised_and_stored(self):
        c = Client(HTTP_USER_AGENT=ANDROID)
        c.force_login(self.user)
        c.get("/api/me/capabilities/")
        r = c.post("/api/me/device-info/", data=json.dumps({
            "screen": "1080x2340", "timezone": "Africa/Kampala", "hardware_concurrency": 8,
            "device_memory_gb": 4, "gpu": "Mali-G68", "ua_model": "SM-A546E", "evil_key": "<script>",
            "languages": "x" * 999}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        dev = AuthorizedDevice.objects.filter(user=self.user).first()
        client = dev.device_info["client"]
        self.assertEqual(client["timezone"], "Africa/Kampala")
        self.assertEqual(client["gpu"], "Mali-G68")
        self.assertNotIn("evil_key", client)
        self.assertEqual(len(client["languages"]), 120)
        self.assertEqual(dev.device_model, "SM-A546E")

    def test_report_requires_sign_in(self):
        r = Client().post("/api/me/device-info/", data="{}", content_type="application/json")
        self.assertIn(r.status_code, (401, 302))


class HostInfoTests(TestCase):
    def test_host_info_has_core_fields(self):
        info = dis.host_info()
        for key in ("hostname", "os_name", "arch", "cpu", "cpu_cores", "python", "app_version", "mac"):
            self.assertIn(key, info)
        self.assertTrue(info["hostname"])

    def test_central_keeps_only_known_bounded_keys(self):
        clean = _clean_host_info({"hostname": "x" * 500, "cpu_cores": 8, "local_ips": ["1.2.3.4"] * 20,
                                  "rogue": "drop me"})
        self.assertEqual(len(clean["hostname"]), 200)
        self.assertEqual(clean["cpu_cores"], 8)
        self.assertEqual(len(clean["local_ips"]), 8)
        self.assertNotIn("rogue", clean)
