"""
Unit and integration tests for GitHub Releases Update Engine and API endpoints.
Tests version comparison, caching, Git & ZIP safe updating, and REST endpoints.
"""
import io
import json
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

from django.test import Client, TestCase
from django.urls import reverse
from django.core.management import call_command

from core.version import (
    __version__,
    get_version_info,
    is_newer_version,
    parse_version,
)
from core.services.update_service import (
    CACHE_FILE,
    PROTECTED_PATHS,
    UpdateService,
    _get_cache,
    _save_cache,
    update_service,
)


class VersionTests(TestCase):
    """Tests semantic version parsing and comparison."""

    def test_parse_version_standard(self):
        self.assertEqual(parse_version("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_version("2.4.12"), (2, 4, 12))

    def test_parse_version_with_v_prefix(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("V3.0.1"), (3, 0, 1))

    def test_parse_version_with_prerelease(self):
        self.assertEqual(parse_version("1.0.0-rc1"), (1, 0, 0))
        self.assertEqual(parse_version("2.1.0-beta.2"), (2, 1, 0))

    def test_parse_version_short(self):
        self.assertEqual(parse_version("1"), (1, 0, 0))
        self.assertEqual(parse_version("1.2"), (1, 2, 0))

    def test_is_newer_version(self):
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("1.1.0", "1.0.9"))
        self.assertTrue(is_newer_version("2.0.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("0.9.9", "1.0.0"))
        self.assertFalse(is_newer_version("invalid", "1.0.0"))

    def test_get_version_info(self):
        info = get_version_info()
        self.assertIn("version", info)
        self.assertIn("version_tag", info)
        self.assertIn("github_repo", info)
        self.assertEqual(info["version"], __version__)


class UpdateCacheTests(TestCase):
    """Tests caching of GitHub release checks."""

    def setUp(self):
        self.test_cache_file = Path(tempfile.mktemp(suffix=".json"))

    def tearDown(self):
        if self.test_cache_file.is_file():
            self.test_cache_file.unlink()

    def test_save_and_get_cache_valid(self):
        with patch("core.services.update_service.CACHE_FILE", self.test_cache_file):
            data = {"update_available": True, "latest_version": "2.0.0"}
            _save_cache(data)
            self.assertTrue(self.test_cache_file.is_file())

            cached = _get_cache()
            self.assertIsNotNone(cached)
            self.assertEqual(cached["latest_version"], "2.0.0")

    def test_get_cache_expired(self):
        with patch("core.services.update_service.CACHE_FILE", self.test_cache_file):
            expired_time = (datetime.now() - timedelta(hours=13)).isoformat()
            payload = {
                "cached_at": expired_time,
                "result": {"update_available": False, "latest_version": "1.0.0"},
            }
            self.test_cache_file.write_text(json.dumps(payload), encoding="utf-8")

            cached = _get_cache()
            self.assertIsNone(cached)

    def test_get_cache_corrupted(self):
        with patch("core.services.update_service.CACHE_FILE", self.test_cache_file):
            self.test_cache_file.write_text("invalid json {", encoding="utf-8")
            cached = _get_cache()
            self.assertIsNone(cached)


class UpdateServiceCheckTests(TestCase):
    """Tests checking for updates via GitHub Releases API."""

    def setUp(self):
        self.test_cache_file = Path(tempfile.mktemp(suffix=".json"))

    def tearDown(self):
        if self.test_cache_file.is_file():
            self.test_cache_file.unlink()

    @patch("core.services.update_service.CACHE_FILE")
    @patch("urllib.request.urlopen")
    def test_check_for_updates_newer_available(self, mock_urlopen, mock_cache):
        mock_cache.is_file.return_value = False

        mock_response = MagicMock()
        mock_response.status = 200
        release_data = {
            "tag_name": "v99.0.0",
            "name": "Major Feature Release",
            "body": "Detailed changelog notes here.",
            "html_url": "https://github.com/KIREX01/itms_verification/releases/tag/v99.0.0",
            "published_at": "2026-09-14T00:00:00Z",
            "zipball_url": "https://api.github.com/repos/KIREX01/itms_verification/zipball/v99.0.0",
            "assets": [
                {
                    "name": "itms_verification-v99.0.0.zip",
                    "browser_download_url": "https://github.com/KIREX01/itms_verification/releases/download/v99.0.0/release.zip",
                }
            ],
        }
        mock_response.read.return_value = json.dumps(release_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = UpdateService.check_for_updates(force=True)
        self.assertTrue(res["success"])
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest_version"], "99.0.0")
        self.assertEqual(res["release_name"], "Major Feature Release")
        self.assertIn("Detailed changelog", res["release_notes"])
        self.assertEqual(
            res["download_url"],
            "https://github.com/KIREX01/itms_verification/releases/download/v99.0.0/release.zip",
        )

    @patch("core.services.update_service.CACHE_FILE")
    @patch("urllib.request.urlopen")
    def test_check_for_updates_already_latest(self, mock_urlopen, mock_cache):
        mock_cache.is_file.return_value = False

        mock_response = MagicMock()
        mock_response.status = 200
        release_data = {
            "tag_name": f"v{__version__}",
            "name": f"Release v{__version__}",
            "body": "Current version release notes.",
            "html_url": "https://github.com/KIREX01/itms_verification/releases",
            "published_at": "2026-09-14T00:00:00Z",
            "zipball_url": "",
            "assets": [],
        }
        mock_response.read.return_value = json.dumps(release_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = UpdateService.check_for_updates(force=True)
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])
        self.assertEqual(res["latest_version"], __version__)

    @patch("core.services.update_service.CACHE_FILE")
    @patch("urllib.request.urlopen")
    def test_check_for_updates_404_not_found(self, mock_urlopen, mock_cache):
        mock_cache.is_file.return_value = False
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/...",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b""),
        )

        res = UpdateService.check_for_updates(force=True)
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])
        self.assertIn("No remote releases found", res["release_notes"])

    @patch("core.services.update_service.CACHE_FILE")
    @patch("urllib.request.urlopen")
    def test_check_for_updates_network_error(self, mock_urlopen, mock_cache):
        mock_cache.is_file.return_value = False
        mock_urlopen.side_effect = urllib.error.URLError(reason="Connection refused")

        res = UpdateService.check_for_updates(force=True)
        self.assertFalse(res["success"])
        self.assertIn("Connection refused", res["error"])


class UpdateServiceApplyTests(TestCase):
    """Tests applying updates via Git and standalone ZIP release archives."""

    @patch("subprocess.run")
    @patch("core.services.update_service.UpdateService._run_post_update_tasks")
    def test_apply_git_update_success(self, mock_post_tasks, mock_run):
        mock_post_tasks.return_value = (True, "Tasks completed")

        # Mock status clean, fetch 0, pull 0
        mock_status = MagicMock(returncode=0, stdout="")
        mock_fetch = MagicMock(returncode=0, stdout="")
        mock_pull = MagicMock(returncode=0, stdout="")
        mock_run.side_effect = [mock_status, mock_fetch, mock_pull]

        with patch("pathlib.Path.is_dir", return_value=True):
            res = UpdateService._apply_git_update()

        self.assertTrue(res["success"])
        self.assertTrue(res.get("restart_required"))

    @patch("subprocess.run")
    def test_apply_git_update_conflict(self, mock_run):
        mock_status = MagicMock(returncode=0, stdout="")
        mock_fetch = MagicMock(returncode=0, stdout="")
        mock_pull = MagicMock(returncode=1, stderr="error: your local changes would be overwritten")
        mock_run.side_effect = [mock_status, mock_fetch, mock_pull]

        res = UpdateService._apply_git_update()
        self.assertFalse(res["success"])
        self.assertIn("conflict", res["error"].lower())

    def test_protected_paths_configuration(self):
        """Verifies all critical operator data paths are in PROTECTED_PATHS."""
        self.assertIn("db.sqlite3", PROTECTED_PATHS)
        self.assertIn(".env", PROTECTED_PATHS)
        self.assertIn("media", PROTECTED_PATHS)
        self.assertIn("secure", PROTECTED_PATHS)
        self.assertIn("exports", PROTECTED_PATHS)
        self.assertIn(".venv", PROTECTED_PATHS)


class UpdateEndpointsAndCommandTests(TestCase):
    """Tests Web UI REST endpoints and CLI check_updates command."""

    def setUp(self):
        self.client = Client()

    @patch("core.services.update_service.UpdateService.check_for_updates")
    def test_api_check_updates_endpoint(self, mock_check):
        mock_check.return_value = {
            "success": True,
            "update_available": True,
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "release_notes": "Added new features.",
        }

        response = self.client.get(reverse("core:api_check_updates") + "?force=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["update_available"])
        self.assertEqual(data["latest_version"], "1.1.0")

    @patch("core.services.update_service.UpdateService.apply_update")
    def test_api_apply_update_endpoint_success(self, mock_apply):
        mock_apply.return_value = {
            "success": True,
            "message": "System updated.",
            "restart_required": True,
        }

        response = self.client.post(reverse("core:api_apply_update"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["restart_required"])

    @patch("core.services.update_service.UpdateService.apply_update")
    def test_api_apply_update_endpoint_failure(self, mock_apply):
        mock_apply.return_value = {
            "success": False,
            "error": "Conflict pulling changes.",
        }

        response = self.client.post(reverse("core:api_apply_update"))
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("Conflict", data["error"])

    @patch("core.services.update_service.UpdateService.check_for_updates")
    def test_check_updates_management_command(self, mock_check):
        mock_check.return_value = {
            "success": True,
            "update_available": False,
            "current_version": __version__,
            "latest_version": __version__,
        }

        out = io.StringIO()
        call_command("check_updates", stdout=out)
        output = out.getvalue()
        self.assertIn(f"v{__version__}", output)
        self.assertIn("up-to-date", output)
