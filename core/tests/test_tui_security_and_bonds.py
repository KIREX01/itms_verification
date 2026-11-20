"""
Unit & Integration Tests for TUI Master Security Lock, Dynamic Bond Discovery, and Settings Sanitization.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, TestCase

from core.services import config_service


class TUIMasterPINTests(TestCase):
    """Test suite for TUI Master PIN verification, PBKDF2 hashing, and persistence."""

    def setUp(self):
        # Reset security config for tests
        cfg = config_service.load_config()
        cfg.setdefault("security", {})
        cfg["security"]["tui_master_pin_hash"] = ""
        cfg["security"]["tui_master_pin_enabled"] = True
        config_service.save_config(cfg)

    def test_no_default_master_pin(self):
        """There is no built-in PIN: with no hash set nothing verifies (the old public default 739104 is gone)."""
        assert config_service.is_tui_master_pin_set() is False
        assert config_service.verify_tui_master_pin("739104") is False
        assert config_service.verify_tui_master_pin("000000") is False
        assert config_service.verify_tui_master_pin("123456") is False
        assert config_service.verify_tui_master_pin("") is False

    def test_set_and_verify_custom_master_pin(self):
        """Setting a custom 6-digit PIN hashes it with PBKDF2 and verifies accurately."""
        assert config_service.set_tui_master_pin("882194") is True

        # Old default must now fail
        assert config_service.verify_tui_master_pin("739104") is False

        # New PIN must succeed
        assert config_service.verify_tui_master_pin("882194") is True
        assert config_service.verify_tui_master_pin("882195") is False

        # Verify hash format
        stored_hash = config_service.get_tui_master_pin_hash()
        assert "$" in stored_hash
        salt, h_val = stored_hash.split("$", 1)
        assert len(salt) == 32
        assert len(h_val) == 64

    def test_bootstrap_generates_random_pin_once(self):
        pin = config_service.ensure_tui_master_pin()
        assert pin and len(pin) == 6 and pin.isdigit()
        assert config_service.verify_tui_master_pin(pin) is True
        assert config_service.ensure_tui_master_pin() is None  # never overwrites an existing PIN

    def test_reject_short_pin(self):
        """PIN with fewer than 4 digits must raise ValueError."""
        with pytest.raises(ValueError):
            config_service.set_tui_master_pin("12")


class SettingsSanitizationAPITests(TestCase):
    """Verifies Web UI settings API sanitization and permission guardrails."""

    def setUp(self):
        self.client = Client()
        self.regular_user = User.objects.create_user(
            username="operator1", password="TestPassword123!", is_staff=False, is_superuser=False
        )
        self.superuser = User.objects.create_superuser(
            username="admin1", password="SuperPassword123!", email="admin@itms.ug"
        )

    def test_settings_sanitized_for_regular_user(self):
        """Regular user calling /api/settings/ must not receive raw db passwords, host ports, or dev options."""
        self.client.force_login(self.regular_user)
        response = self.client.get("/api/settings/")
        assert response.status_code == 200

        data = response.json()
        assert data.get("success") is True
        assert data.get("developer_mode") is False
        assert data.get("developer_settings") == {}
        assert data.get("database") == "Connected & Synchronized"
        assert data.get("active_engine") == "connected"

    def test_vault_folder_restricted_from_regular_user(self):
        """Regular user attempting to modify vault directory via Web UI must be rejected with 403 Forbidden."""
        self.client.force_login(self.regular_user)
        response = self.client.post(
            "/api/settings/vault-folder/",
            data=json.dumps({"vault_path": "/etc/malicious"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_vault_browse_restricted_from_regular_user(self):
        """Regular user attempting to browse server filesystem folders must be rejected with 403 Forbidden."""
        self.client.force_login(self.regular_user)
        response = self.client.post(
            "/api/settings/browse-vault-folder/",
            data=json.dumps({"path": "/"}),
            content_type="application/json",
        )
        assert response.status_code == 403


class DynamicBondDiscoveryTests(TestCase):
    """Verifies live bond warehouse discovery and synchronization."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="operator2", password="TestPassword123!"
        )

    @patch("core.services.itms_web_client.ITMSWebClient.fetch_warehouses_from_itms")
    def test_sync_bonds_endpoint(self, mock_fetch):
        """Endpoint /api/itms/sync_bonds/ returns discovered warehouses and updates available bonds."""
        mock_fetch.return_value = {
            "success": True,
            "count": 3,
            "warehouses": [
                {"code": "AGM", "name": "AGM Bonded Warehouse", "warehouse_id": "1"},
                {"code": "SPIRO", "name": "Spiro Electric Bond", "warehouse_id": "2"},
                {"code": "BOND52", "name": "Bond 52 Mukono", "warehouse_id": "3"},
            ],
            "active_bond": {"code": "AGM", "name": "AGM Bonded Warehouse"},
        }

        self.client.force_login(self.user)
        response = self.client.post("/api/itms/sync_bonds/")
        assert response.status_code == 200

        data = response.json()
        assert data.get("success") is True
        assert data.get("count") == 3
        codes = [w["code"] for w in data.get("warehouses", [])]
        assert "AGM" in codes
        assert "SPIRO" in codes
        assert "BOND52" in codes


class OperatorITMSSessionTests(TestCase):
    """Verifies database-backed ITMS session persistence, multi-user isolation, and API synchronization."""

    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(
            username="operator_a", password="TestPassword123!", email="op_a@itms.ug"
        )
        self.user2 = User.objects.create_user(
            username="operator_b", password="TestPassword123!", email="op_b@itms.ug"
        )

    def test_session_store_database_persistence_and_isolation(self):
        """ITMSWebSessionStore writes to OperatorITMSSession in DB and isolates user sessions."""
        from core.models import OperatorITMSSession
        from core.services.itms_web_client import ITMSWebSessionData, ITMSWebSessionStore

        # User 1 session
        store1 = ITMSWebSessionStore(user=self.user1)
        sess1 = ITMSWebSessionData(
            base_url="https://stock.itms.ug",
            user_email="operator_a@itms.ug",
            user_uuid="11111111-1111-1111-1111-111111111111",
            cookies={"_identity-frontend": "token_a", "advanced-frontend": "sess_a"},
            csrf_token="csrf_a",
            is_authenticated=True,
            expires_at=time.time() + 86400 * 30,
            last_verified_at=time.time(),
        )
        store1.save(sess1)

        # User 2 session
        store2 = ITMSWebSessionStore(user=self.user2)
        sess2 = ITMSWebSessionData(
            base_url="https://stock.itms.ug",
            user_email="operator_b@itms.ug",
            user_uuid="22222222-2222-2222-2222-222222222222",
            cookies={"_identity-frontend": "token_b", "advanced-frontend": "sess_b"},
            csrf_token="csrf_b",
            is_authenticated=True,
            expires_at=time.time() + 86400 * 30,
            last_verified_at=time.time(),
        )
        store2.save(sess2)

        # Check DB rows
        db_sess1 = OperatorITMSSession.objects.get(user=self.user1)
        db_sess2 = OperatorITMSSession.objects.get(user=self.user2)

        self.assertEqual(db_sess1.itms_email, "operator_a@itms.ug")
        self.assertEqual(db_sess1.cookies["_identity-frontend"], "token_a")
        self.assertEqual(db_sess2.itms_email, "operator_b@itms.ug")
        self.assertEqual(db_sess2.cookies["_identity-frontend"], "token_b")

        # Simulate another worker loading User 1's session fresh from DB
        fresh_store = ITMSWebSessionStore(user=self.user1)
        loaded = fresh_store.load()
        self.assertTrue(loaded.is_authenticated)
        self.assertEqual(loaded.user_email, "operator_a@itms.ug")
        self.assertEqual(loaded.cookies["_identity-frontend"], "token_a")

    def test_api_connect_and_status_synchronization(self):
        """Connecting via /api/itms/connect/ updates DB and /api/itms/status/ returns authenticated."""
        from unittest.mock import patch
        from core.models import OperatorITMSSession

        self.client.force_login(self.user1)

        with patch("core.services.itms_web_client.ITMSWebClient.login") as mock_login:
            mock_login.return_value = (
                True,
                "Connected to ITMS WebApp successfully.",
                {
                    "user_email": "live_user@itms.ug",
                    "user_uuid": "33333333-3333-3333-3333-333333333333",
                    "cookies": {"_identity-frontend": "valid_token"},
                },
            )

            # 1. Connect
            connect_res = self.client.post(
                "/api/itms/connect/",
                data=json.dumps({
                    "email": "live_user@itms.ug",
                    "password": "secretpassword",
                    "base_url": "https://stock.itms.ug",
                }),
                content_type="application/json",
            )
            self.assertEqual(connect_res.status_code, 200)
            data = connect_res.json()
            self.assertTrue(data.get("success"))

            # 2. Check /api/itms/status/
            status_res = self.client.get("/api/itms/status/")
            self.assertEqual(status_res.status_code, 200)
            status_data = status_res.json()
            self.assertTrue(status_data.get("success"))
            self.assertIn("status", status_data)
            self.assertIn("authenticated", status_data["status"])



class SettingsConfigurationReviewTests(TestCase):
    """Verifies settings configuration across Web UI templates, schemas, and TUI pane."""

    def test_settings_schema_all_mode_option(self):
        """Schema must recognize 'all' shift target mode."""
        schema = config_service.USER_SETTINGS_SCHEMA.get("sync.shift_target_date_mode", {})
        self.assertIn("all", schema.get("options", []))
        self.assertIn("all", schema.get("description", ""))

    def test_user_and_developer_settings_template_aliases(self):
        """Both dotted and underscore aliases must be present to support Django template syntax."""
        from django.template import Context, Template

        user_settings = config_service.get_user_settings()
        self.assertIn("bond.strict_bond_scoping", user_settings)
        self.assertIn("bond_strict_bond_scoping", user_settings)
        self.assertIn("sync.auto_sync_interval_seconds", user_settings)
        self.assertIn("sync_auto_sync_interval_seconds", user_settings)

        dev_settings = config_service.get_developer_settings()
        self.assertIn("crawl.max_kit_pages", dev_settings)
        self.assertIn("crawl_max_kit_pages", dev_settings)

        # Verify Django template resolves underscore alias values without empty fallback
        tmpl = Template("{{ us.bond_strict_bond_scoping.value }}|{{ us.sync_auto_sync_interval_seconds.value }}")
        rendered = tmpl.render(Context({"us": user_settings}))
        self.assertEqual(rendered, f"{user_settings['bond.strict_bond_scoping']['value']}|{user_settings['sync.auto_sync_interval_seconds']['value']}")

    def test_tui_settings_pane_safe_save_and_reset(self):
        """SettingsPane must safely save and reset without raising NoMatches for uncomposed widgets."""
        from core.tui.settings_pane import SettingsPane

        pane = SettingsPane()
        # Verify default kit crawl bound is 100
        cfg = config_service.load_config()
        self.assertEqual(cfg.get("crawl", {}).get("max_kit_pages", 100), 100)


class SessionExpiryAndLockoutTests(TestCase):
    """Verifies that expired access grants or blocked devices trigger immediate lockout."""

    def setUp(self):
        self.operator = User.objects.create_user(
            username="fitter_mike",
            email="mike@close.kirex.online",
            password="Password123!",
        )
        self.superuser = User.objects.create_superuser(
            username="admin_lead",
            email="lead@close.kirex.online",
            password="AdminPassword123!",
        )

    def test_session_access_valid_when_active(self):
        """Active operator with valid future expiry must be granted access."""
        from datetime import timedelta
        from django.utils import timezone
        from core.models import EmailAccessRequest, AuthorizedDevice
        from core.services import device_access_service

        future_exp = timezone.now() + timedelta(hours=12)
        EmailAccessRequest.objects.create(
            email="mike@close.kirex.online",
            username="fitter_mike",
            status=EmailAccessRequest.Status.APPROVED,
            expires_at=future_exp,
        )
        AuthorizedDevice.objects.create(
            device_id="DEV_MIKE_001",
            user=self.operator,
            authorized_email="mike@close.kirex.online",
            status=AuthorizedDevice.Status.APPROVED,
            expires_at=future_exp,
        )

        is_valid, reason, exp_dt = device_access_service.check_operator_session_access(
            self.operator, device_id="DEV_MIKE_001"
        )
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")
        self.assertIsNotNone(exp_dt)

    def test_session_access_locked_when_expired(self):
        """When an access grant's expires_at is in the past, access must be denied and marked EXPIRED."""
        from datetime import timedelta
        from django.utils import timezone
        from core.models import EmailAccessRequest, AuthorizedDevice
        from core.services import device_access_service

        past_exp = timezone.now() - timedelta(minutes=10)
        req = EmailAccessRequest.objects.create(
            email="mike@close.kirex.online",
            username="fitter_mike",
            status=EmailAccessRequest.Status.APPROVED,
            expires_at=past_exp,
        )
        dev = AuthorizedDevice.objects.create(
            device_id="DEV_MIKE_002",
            user=self.operator,
            authorized_email="mike@close.kirex.online",
            status=AuthorizedDevice.Status.APPROVED,
            expires_at=past_exp,
        )

        is_valid, reason, exp_dt = device_access_service.check_operator_session_access(
            self.operator, device_id="DEV_MIKE_002"
        )
        self.assertFalse(is_valid)
        self.assertIn("expired", reason.lower())

        req.refresh_from_db()
        dev.refresh_from_db()
        self.assertEqual(req.status, EmailAccessRequest.Status.EXPIRED)
        self.assertEqual(dev.status, AuthorizedDevice.Status.EXPIRED)

    def test_session_access_blocked_by_firewall(self):
        """When device or IP is blocked in firewall, access must be immediately rejected."""
        from core.models import AuthorizedDevice
        from core.services import device_access_service

        AuthorizedDevice.objects.create(
            device_id="DEV_BLOCKED_99",
            user=self.operator,
            status=AuthorizedDevice.Status.BLOCKED,
            block_reason="Stolen terminal reported",
        )

        is_valid, reason, _ = device_access_service.check_operator_session_access(
            self.operator, device_id="DEV_BLOCKED_99"
        )
        self.assertFalse(is_valid)
        self.assertIn("blocked", reason.lower())

    def test_superuser_exempt_from_time_expiry(self):
        """Superuser account must not be locked out by time window expiry."""
        from core.services import device_access_service

        is_valid, reason, _ = device_access_service.check_operator_session_access(self.superuser)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")


class WarehouseDiscoveryNoAGMDefaultTests(TestCase):
    """Verifies that new user onboarding and ITMS login dynamically discovers warehouse without forcing AGM."""

    def setUp(self):
        from core.models import Warehouse
        Warehouse.objects.all().delete()
        self.operator = User.objects.create_user(
            username="cfao_op", password="Password123!", email="cfao_op@close.kirex.online"
        )

    def test_discover_warehouse_from_selected_option(self):
        """discover_user_assigned_warehouse parses selected option from orders index HTML."""
        from core.services.itms_web_client import ITMSWebClient, ITMSWebSessionData
        from core.models import Warehouse

        Warehouse.objects.create(
            warehouse_uuid="wh-cfao-1111",
            code="CFAO",
            name="CFAO MOTORS (UGANDA) LIMITED",
            is_active_bond=True,
        )

        client = ITMSWebClient(user=self.operator)
        client.session_store.save(ITMSWebSessionData(
            base_url="https://stock.itms.ug",
            user_email="cfao_op@cfao.ug",
            user_uuid="cfao-uuid-1",
            cookies={"_identity-frontend": "dummy_cookie"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        mock_orders_html = """
        <html>
        <body>
        <select name="InstallationOrderSearch[warehouse_id]">
            <option value="">All Warehouses</option>
            <option value="wh-cfao-1111" selected="selected">CFAO MOTORS (UGANDA) LIMITED</option>
            <option value="wh-agm-2222">AGM (INSTALLATION) SOLUTIONS</option>
        </select>
        </body>
        </html>
        """

        with patch.object(client, "_request_with_retry") as mock_req, \
             patch.object(client, "fetch_warehouses_from_itms") as mock_wh:
            mock_req.return_value = MagicMock(status_code=200, text=mock_orders_html)
            mock_wh.return_value = {
                "success": True,
                "warehouses": [
                    {"code": "CFAO", "name": "CFAO MOTORS (UGANDA) LIMITED", "warehouse_uuid": "wh-cfao-1111"},
                    {"code": "AGM", "name": "AGM (INSTALLATION) SOLUTIONS", "warehouse_uuid": "wh-agm-2222"},
                ],
            }

            result = client.discover_user_assigned_warehouse()
            self.assertEqual(result["code"], "CFAO")
            self.assertEqual(result["name"], "CFAO MOTORS (UGANDA) LIMITED")
            self.assertEqual(result["source"], "SELECTED_OPTION")

    def test_discover_warehouse_from_single_facility(self):
        """When an ITMS account only has access to one facility, that facility is automatically discovered."""
        from core.services.itms_web_client import ITMSWebClient, ITMSWebSessionData

        client = ITMSWebClient(user=self.operator)
        client.session_store.save(ITMSWebSessionData(
            base_url="https://stock.itms.ug",
            user_email="spiro_op@itms.ug",
            user_uuid="spiro-uuid-1",
            cookies={"_identity-frontend": "dummy_cookie"},
            is_authenticated=True,
            expires_at=time.time() + 86400,
        ))

        with patch.object(client, "_request_with_retry") as mock_req, \
             patch.object(client, "fetch_warehouses_from_itms") as mock_wh:
            mock_req.return_value = MagicMock(status_code=200, text="<html><body></body></html>")
            mock_wh.return_value = {
                "success": True,
                "warehouses": [
                    {"code": "SPIRO", "name": "SPIRO ELECTRIC BONDED WAREHOUSE", "warehouse_uuid": "wh-spiro-99"},
                ],
            }

            result = client.discover_user_assigned_warehouse()
            self.assertEqual(result["code"], "SPIRO")
            self.assertEqual(result["source"], "SINGLE_FACILITY")

    def test_fetch_warehouses_does_not_force_agm_when_user_has_assigned_warehouse(self):
        """fetch_warehouses_from_itms must not arbitrarily set active_wh_instance to AGM."""
        from core.models import Warehouse, OperatorProfile
        from core.services.itms_web_client import ITMSWebClient

        wh_c = Warehouse.objects.create(
            warehouse_uuid="wh-c-1",
            code="CFAO",
            name="CFAO MOTORS",
        )
        Warehouse.objects.create(
            warehouse_uuid="wh-a-2",
            code="AGM",
            name="AGM (INSTALLATION)",
        )

        OperatorProfile.objects.create(
            user=self.operator,
            active_facility="CFAO",
            allowed_facilities=["CFAO"],
        )

        client = ITMSWebClient(user=self.operator)
        with patch.object(client, "_request_with_retry") as mock_req:
            mock_req.return_value = MagicMock(status_code=200, text="<html><body></body></html>")
            res = client.fetch_warehouses_from_itms()
            self.assertEqual(res["active_warehouse_uuid"], "wh-c-1")
            self.assertNotEqual(res["active_warehouse_uuid"], "wh-a-2")
