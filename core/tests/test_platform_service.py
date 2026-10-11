"""
Unit tests for core.services.platform_service cross-platform abstraction.
"""
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from core.services import platform_service


class PlatformServiceTests(SimpleTestCase):
    def test_platform_identification(self):
        """Verifies boolean OS flags match sys.platform."""
        self.assertEqual(platform_service.is_windows(), sys.platform == "win32")
        self.assertEqual(platform_service.is_linux(), sys.platform.startswith("linux"))
        self.assertEqual(platform_service.is_posix(), os.name == "posix")

    def test_normalize_rel_path(self):
        """Verifies path normalization converts backslashes and strips dangerous sequences."""
        self.assertEqual(platform_service.normalize_rel_path("media\\vault\\sample.jpg"), "media/vault/sample.jpg")
        self.assertEqual(platform_service.normalize_rel_path("/media/vault/sample.jpg"), "media/vault/sample.jpg")
        self.assertEqual(platform_service.normalize_rel_path(""), "")

        # Directory traversal attempts must raise ValueError
        with self.assertRaises(ValueError):
            platform_service.normalize_rel_path("../etc/passwd")

        with self.assertRaises(ValueError):
            platform_service.normalize_rel_path("media/../../secret.txt")

    def test_safe_chmod_on_windows_is_noop(self):
        """Verifies safe_chmod safely no-ops on Windows without raising."""
        with patch.object(platform_service, "IS_POSIX", False):
            with patch("os.chmod") as mock_chmod:
                platform_service.safe_chmod(Path("some/path"), 0o755)
                mock_chmod.assert_not_called()

    def test_safe_chmod_on_posix_strips_world_writable(self):
        """Verifies safe_chmod on POSIX strips world-writable bits (S2612 security check)."""
        with patch.object(platform_service, "IS_POSIX", True):
            with patch.object(Path, "exists", return_value=True):
                with patch("os.chmod") as mock_chmod:
                    # Request world-writable 0777
                    platform_service.safe_chmod(Path("/opt/itms/sample"), 0o777)
                    # S_IWOTH is 0o002. 0o777 & ~0o002 = 0o775
                    expected_mode = 0o777 & ~stat.S_IWOTH
                    mock_chmod.assert_called_once_with(Path("/opt/itms/sample"), expected_mode)

    def test_platform_diagnostics(self):
        """Verifies platform diagnostics dictionary contains required metadata."""
        diag = platform_service.get_platform_diagnostics()
        self.assertIn("os_name", diag)
        self.assertIn("platform_system", diag)
        self.assertIn("python_version", diag)
        self.assertIn("is_windows", diag)
        self.assertIn("path_separator", diag)
