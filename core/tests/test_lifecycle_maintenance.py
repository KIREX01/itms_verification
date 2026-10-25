"""
Unit tests for ITMS Verification Copilot lifecycle control & maintenance mode.
- Verifies config_service maintenance mode toggling and HTML compilation.
- Verifies MaintenanceModeMiddleware response behavior (HTML vs JSON vs bypass).
- Verifies itms_ctl management command subactions (status, maintenance, switch).
"""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from core.services import config_service


class LifecycleMaintenanceTests(TestCase):
    def setUp(self):
        self.http_client = Client()
        self.user = User.objects.create_user(username="testop", password="password123")
        self.http_client.login(username="testop", password="password123")
        # Ensure maintenance mode is off initially
        config_service.set_maintenance_mode(False)

    def tearDown(self):
        config_service.set_maintenance_mode(False)
        flag = config_service.get_maintenance_flag_path()
        if flag.is_file():
            flag.unlink()

    def test_set_maintenance_mode_compiles_html_and_sets_flag(self):
        config_service.set_maintenance_mode(
            enabled=True,
            title="Custom Maintenance Title",
            message="We are updating database indices.",
            estimated_duration="20 minutes",
            contact_support="help@kirex.online",
            retry_after_seconds=45,
        )

        self.assertTrue(config_service.is_maintenance_enabled())
        self.assertTrue(config_service.get_maintenance_flag_path().is_file())

        m_cfg = config_service.get_maintenance_config()
        self.assertTrue(m_cfg["enabled"])
        self.assertEqual(m_cfg["title"], "Custom Maintenance Title")
        self.assertEqual(m_cfg["message"], "We are updating database indices.")
        self.assertEqual(m_cfg["estimated_duration"], "20 minutes")
        self.assertEqual(m_cfg["contact_support"], "help@kirex.online")
        self.assertEqual(m_cfg["retry_after_seconds"], 45)

        # Verify static file contains custom title
        tpl_path = config_service._get_project_root() / "templates" / "maintenance.html"
        self.assertTrue(tpl_path.is_file())
        content = tpl_path.read_text(encoding="utf-8")
        self.assertIn("Custom Maintenance Title", content)
        self.assertIn("We are updating database indices.", content)
        self.assertIn("20 minutes", content)

    def test_maintenance_middleware_returns_503_html(self):
        config_service.set_maintenance_mode(
            enabled=True,
            title="Scheduled Maintenance Notice",
            message="Server undergoing migration.",
        )

        resp = self.http_client.get("/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("text/html", resp["Content-Type"])
        self.assertIn(b"Scheduled Maintenance Notice", resp.content)
        self.assertIn(b"Server undergoing migration", resp.content)

    def test_maintenance_middleware_returns_503_json_for_api(self):
        config_service.set_maintenance_mode(enabled=True)

        resp = self.http_client.get("/api/stock/reconciliation/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp["Content-Type"])
        data = resp.json()
        self.assertTrue(data.get("maintenance"))
        self.assertIn("scheduled maintenance", data.get("error").lower())

    def test_maintenance_middleware_bypass(self):
        config_service.set_maintenance_mode(enabled=True)

        # Bypass via query parameter
        resp = self.http_client.get("/?bypass_maintenance=1")
        # Should not be 503; either 200 or 302 redirect to login/dashboard
        self.assertNotEqual(resp.status_code, 503)

    def test_itms_ctl_maintenance_subaction(self):
        out = StringIO()
        call_command("itms_ctl", "maintenance", "on", stdout=out)
        self.assertTrue(config_service.is_maintenance_enabled())
        self.assertIn("Maintenance Mode ENABLED", out.getvalue())

        out2 = StringIO()
        call_command("itms_ctl", "maintenance", "off", stdout=out2)
        self.assertFalse(config_service.is_maintenance_enabled())
        self.assertIn("Maintenance Mode DISABLED", out2.getvalue())

    def test_itms_ctl_switch_subaction(self):
        out = StringIO()
        call_command("itms_ctl", "switch", "off", stdout=out)
        self.assertFalse(config_service.is_itms_enabled())
        self.assertIn("ITMS Master Switch PAUSED", out.getvalue())

        out2 = StringIO()
        call_command("itms_ctl", "switch", "on", stdout=out2)
        self.assertTrue(config_service.is_itms_enabled())
        self.assertIn("ITMS Master Switch ONLINE", out2.getvalue())

    def test_itms_ctl_status(self):
        out = StringIO()
        call_command("itms_ctl", "status", stdout=out)
        output = out.getvalue()
        self.assertIn("ITMS Verification Copilot — System Status", output)
        self.assertIn("Maintenance Mode:", output)
        self.assertIn("ITMS Master Switch:", output)
        self.assertIn("Database Engine:", output)
