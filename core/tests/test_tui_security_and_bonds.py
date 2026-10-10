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
