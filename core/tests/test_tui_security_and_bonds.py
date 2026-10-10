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

    def test_default_master_pin_verification(self):
        """Default master PIN (739104) must verify correctly when no custom hash is set."""
        assert config_service.verify_tui_master_pin("739104") is True
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

