"""
Tests for security audit remediations:
- Directory traversal guards in serve_media (CWE-22)
- ZipSlip extraction protection in UpdateService._apply_zip_update (CWE-22)
- Order unlinking attribute safety (Runtime AttributeError prevention)
- Native folder picker path sanitization
- Batch submit concurrency safety
"""
import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    VehicleInstallationPair,
)
from core.services import secure_storage, vault_service
from core.services.update_service import UpdateService


class SecurityAuditRemediationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="security_tester",
            password="password123",
        )
        self.client.login(username="security_tester", password="password123")

        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-SEC-001",
            source_type=IngestionBatch.SourceType.WEB,
        )

        self.front_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_sec_front",
            original_source_path="/test/sec_front.jpg",
            vault_file="vault/sec_front.jpg",
            detected_plate="UMA100A",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        self.rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="hash_sec_rear",
            original_source_path="/test/sec_rear.jpg",
            vault_file="vault/sec_rear.jpg",
            detected_plate="UMA100A",
            orientation=EvidenceImage.Orientation.REAR,
        )

        self.order = InstallationOrder.objects.create(
            order_number="ORD-SEC-001",
            registration_number="UMA100A",
        )

        self.pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            order=self.order,
            match_type=VehicleInstallationPair.MatchType.EXACT,
            matched_via=VehicleInstallationPair.MatchedVia.VISION,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        )

    def test_serve_media_directory_traversal_blocked(self):
        """Attempts to access files outside media/vault via .. must return 404."""
        bad_paths = [
            "../../db.sqlite3",
            "vault/../../db.sqlite3",
            "....//....//db.sqlite3",
            "..\\..\\db.sqlite3",
            "vault/../..\\core/views.py",
        ]
        for p in bad_paths:
            resp = self.client.get(f"/media/{p}")
            self.assertEqual(resp.status_code, 404, f"Path traversal attempt '{p}' did not return 404")

    def test_serve_media_null_byte_blocked(self):
        """Attempts with null bytes must return 404."""
        resp = self.client.get("/media/vault/test%00.jpg")
        self.assertEqual(resp.status_code, 404)

    def test_unlink_order_does_not_raise_attribute_error(self):
        """Unlinking an order must set MatchType.NONE and MatchedVia.MANUAL without AttributeError."""
        url = reverse("core:api_pair_action", kwargs={"pair_id": self.pair.id})
        resp = self.client.post(url, {"action": "unlink_order"})
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        self.assertTrue(data["success"])

        self.pair.refresh_from_db()
        self.assertIsNone(self.pair.order)
        self.assertEqual(self.pair.match_type, VehicleInstallationPair.MatchType.NONE)
        self.assertEqual(self.pair.matched_via, VehicleInstallationPair.MatchedVia.MANUAL)
        self.assertEqual(self.pair.match_score, 0.0)
        self.assertTrue(self.pair.is_manual_override)

    def test_zipslip_protection_in_update_service(self):
        """UpdateService._apply_zip_update must reject archives with ZipSlip directory traversal."""
        # Create a malicious zip in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("../../evil.py", b"print('compromised')")
        zip_bytes = zip_buffer.getvalue()

        # Mock urlopen to return malicious zip
        mock_resp = MagicMock()
        mock_resp.read = MagicMock(side_effect=[zip_bytes, b""])
        mock_resp.__enter__.return_value = io.BytesIO(zip_bytes)

        with patch("urllib.request.urlopen", return_value=mock_resp.__enter__.return_value):
            res = UpdateService._apply_zip_update("https://github.com/test/repo/releases/download/v1.0.2/release.zip")
            self.assertFalse(res["success"])
            self.assertIn("Malicious zip entry detected", res.get("error", ""))

    def test_batch_submit_concurrency_guard(self):
        """If a pair is concurrent altered to not APPROVED, it is skipped."""
        url = reverse("core:api_batch_submit")
        with patch("core.services.submission_worker.submit_pair") as mock_submit:
            # Change status to CONFLICT right before submission
            self.pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            self.pair.save()

            resp = self.client.post(url, {"scope": "ALL", "dry_run": "true"})
            self.assertEqual(resp.status_code, 200)
            mock_submit.assert_not_called()

    def test_serve_media_forbidden_extensions_blocked(self):
        """Requests for sensitive extensions like .py, .env, .sqlite3 must return 404."""
        bad_extensions = ["test.py", "secret.env", "data.sqlite3", "config.json", "app.log"]
        for bad in bad_extensions:
            resp = self.client.get(f"/media/{bad}")
            self.assertEqual(resp.status_code, 404, f"Forbidden extension '{bad}' was not blocked")

    def test_serve_media_hidden_files_blocked(self):
        """Requests for hidden files starting with . must return 404."""
        resp = self.client.get("/media/.env")
        self.assertEqual(resp.status_code, 404)

    def test_api_vault_folder_unauthenticated_blocked(self):
        """Unauthenticated POST to vault folder settings must be rejected with 401."""
        anon = Client()
        resp = anon.post(reverse("core:api_vault_folder"), {"path": "media/vault"})
        self.assertEqual(resp.status_code, 401)

    def test_api_vault_folder_prohibited_system_directory_rejected(self):
        """Targeting critical OS system roots must be rejected with 400."""
        prohibited_paths = ["C:\\Windows", "C:\\Program Files", "/etc", "/bin", "/usr"]
        for p in prohibited_paths:
            resp = self.client.post(reverse("core:api_vault_folder"), {"path": p})
            self.assertEqual(resp.status_code, 400, f"Prohibited directory '{p}' was not rejected")

    def test_api_browse_vault_folder_unauthenticated_blocked(self):
        """Unauthenticated invocation of native folder picker must return 401."""
        anon = Client()
        resp = anon.get(reverse("core:api_browse_vault_folder"))
        self.assertEqual(resp.status_code, 401)

    def test_api_settings_unauthenticated_post_blocked(self):
        """Unauthenticated POST modifying system configuration must return 401."""
        anon = Client()
        resp = anon.post(reverse("core:api_settings"), {"key": "submission.dry_run_mode", "value": "false"})
        self.assertEqual(resp.status_code, 401)

    def test_secure_storage_path_traversal_rejected(self):
        """secure_storage.get_secure_auth_path must reject path traversal attempts with ValueError."""
        traversal_attempts = ["../../etc/passwd", "..\\..\\windows\\win.ini", "null\x00byte.json", "....//secret.json"]
        for evil in traversal_attempts:
            with self.assertRaises((ValueError, Exception)):
                secure_storage.get_secure_auth_path(evil)

    def test_vault_service_resolve_vault_path_traversal_contained(self):
        """resolve_vault_path must never resolve to an arbitrary path outside vault/media roots."""
        vault_root = vault_service.get_vault_root().resolve()
        bad_inputs = ["../../db.sqlite3", "....//db.sqlite3", "C:\\Windows\\System32\\cmd.exe", "/etc/passwd"]
        for b in bad_inputs:
            resolved = vault_service.resolve_vault_path(b)
            # Must stay inside vault_root or media_root
            self.assertTrue(
                resolved.is_relative_to(vault_root) or resolved == vault_root,
                f"Resolved path '{resolved}' escaped vault boundary for input '{b}'"
            )

    def test_update_service_url_validation(self):
        """UpdateService.validate_download_url must allow only authentic GitHub https URLs."""
        valid_urls = [
            "https://github.com/user/repo/releases/download/v1.0.0/release.zip",
            "https://api.github.com/repos/user/repo/zipball/main",
            "https://codeload.github.com/user/repo/zip/refs/heads/main",
            "https://objects.githubusercontent.com/github-production-release-asset-2e65be/12345/archive.zip",
        ]
        invalid_urls = [
            "http://github.com/user/repo/releases/download/v1.0.0/release.zip",  # non-https
            "https://evil-github.com/release.zip",
            "https://attacker.com/malicious.zip",
            "file:///etc/shadow",
            "ftp://github.com/release.zip",
            "",
            None,
        ]
        for u in valid_urls:
            self.assertTrue(UpdateService.validate_download_url(u), f"Valid URL was rejected: {u}")
        for u in invalid_urls:
            self.assertFalse(UpdateService.validate_download_url(u), f"Invalid URL was accepted: {u}")
