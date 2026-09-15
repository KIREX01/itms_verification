"""
Unit tests for the ITMS Web App API client and token lifecycle manager.
"""
import json
import time
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.services.itms_client import (
    ITMSAPIError,
    ITMSAuthError,
    ITMSClient,
    TokenData,
    TokenStore,
)
from core.services.itms_mock import StepResult


class TokenStoreTests(TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_file = f"{self.tmp_dir.name}/test_tokens.json"
        self.store = TokenStore(storage_path=self.token_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_load_tokens(self):
        tokens = self.store.save(
            access_token="acc-12345",
            refresh_token="ref-67890",
            expires_in=3600,
            user_email="operator@example.com",
        )
        self.assertEqual(tokens.access_token, "acc-12345")
        self.assertEqual(tokens.refresh_token, "ref-67890")
        self.assertTrue(tokens.is_access_valid())
        self.assertTrue(tokens.has_refresh())

        # Create a new store instance pointing to same file
        new_store = TokenStore(storage_path=self.token_file)
        loaded = new_store.load()
        self.assertEqual(loaded.access_token, "acc-12345")
        self.assertEqual(loaded.refresh_token, "ref-67890")
        self.assertEqual(loaded.user_email, "operator@example.com")

    def test_clear_tokens(self):
        self.store.save(access_token="acc-test", refresh_token="ref-test")
        self.store.clear()
        self.assertFalse(self.store.tokens.is_access_valid())
        self.assertEqual(self.store.tokens.access_token, "")

    def test_token_expiration(self):
        token_data = TokenData(
            access_token="abc",
            expires_at=time.time() - 10,  # expired 10s ago
        )
        self.assertFalse(token_data.is_access_valid(buffer_seconds=0))


class ITMSClientTests(TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.token_file = f"{self.tmp_dir.name}/test_tokens.json"
        self.store = TokenStore(storage_path=self.token_file)
        self.client = ITMSClient(base_url="https://stock.itms.ug", token_store=self.store)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_extract_tokens_standard_shape(self):
        payload = {
            "access_token": "acc-token-xyz",
            "refresh_token": "ref-token-xyz",
            "expires_in": 1800,
        }
        tokens = self.client._extract_tokens_from_json(payload, "user@example.com")
        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.access_token, "acc-token-xyz")
        self.assertEqual(tokens.refresh_token, "ref-token-xyz")

    def test_extract_tokens_nested_data_shape(self):
        payload = {
            "data": {
                "token": "nested-token",
                "refreshToken": "nested-refresh",
            }
        }
        tokens = self.client._extract_tokens_from_json(payload, "user@example.com")
        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.access_token, "nested-token")
        self.assertEqual(tokens.refresh_token, "nested-refresh")

    @patch("requests.Session.post")
    def test_login_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_resp

        tokens = self.client.login("operator@example.com", "mypassword")
        self.assertEqual(tokens.access_token, "test-access-token")
        self.assertEqual(tokens.refresh_token, "test-refresh-token")

    @patch("requests.Session.post")
    def test_refresh_token_success(self, mock_post):
        self.store.save(access_token="old-acc", refresh_token="valid-ref", expires_in=10)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new-refreshed-acc",
            "refresh_token": "valid-ref",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_resp

        refreshed = self.client.refresh_access_token()
        self.assertEqual(refreshed.access_token, "new-refreshed-acc")

    @patch("requests.Session.request")
    def test_401_triggers_token_refresh_and_retry(self, mock_request):
        self.store.save(access_token="stale-acc", refresh_token="valid-ref", expires_in=3600)

        # First call returns 401 Unauthorized
        resp_401 = MagicMock()
        resp_401.status_code = 401

        # Refresh call returns new token
        resp_refresh = MagicMock()
        resp_refresh.status_code = 200
        resp_refresh.json.return_value = {"access_token": "fresh-acc"}

        # Retry request succeeds
        resp_retry = MagicMock()
        resp_retry.status_code = 200
        resp_retry.json.return_value = {"success": True}

        # Session request calls
        with patch.object(self.client, "refresh_access_token") as mock_refresh:
            mock_refresh.return_value = TokenData(access_token="fresh-acc", refresh_token="valid-ref")
            mock_request.side_effect = [resp_401, resp_retry]

            final_resp = self.client.request("GET", "/api/orders/lookup")
            self.assertEqual(final_resp.status_code, 200)
            mock_refresh.assert_called_once()
