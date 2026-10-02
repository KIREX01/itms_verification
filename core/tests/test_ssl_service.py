"""
Unit tests for the self-signed SSL/TLS certificate service and HTTPS mobile pairing.
"""
import ssl
from pathlib import Path
import pytest
from django.test import Client
from django.urls import reverse

from core.services import network_service, ssl_service


class TestSslService:

    def test_embedded_certificates_exist_and_valid(self):
        """Verifies that the embedded fallback certificate and key PEM constants are valid strings."""
        assert "BEGIN CERTIFICATE" in ssl_service.EMBEDDED_FALLBACK_CERT_PEM
        assert "END CERTIFICATE" in ssl_service.EMBEDDED_FALLBACK_CERT_PEM
        assert "BEGIN RSA PRIVATE KEY" in ssl_service.EMBEDDED_FALLBACK_KEY_PEM
        assert "END RSA PRIVATE KEY" in ssl_service.EMBEDDED_FALLBACK_KEY_PEM

    def test_ensure_ssl_certificates(self, tmp_path):
        """Tests that ensure_ssl_certificates writes usable cert.pem and key.pem files."""
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"

        assert not cert_file.exists()
        assert not key_file.exists()

        out_cert, out_key = ssl_service.ensure_ssl_certificates(
            cert_path=cert_file,
            key_path=key_file,
            san_ips=["192.168.8.182", "192.168.137.1"],
        )

        assert out_cert.exists()
        assert out_key.exists()
        assert out_cert.stat().st_size > 100
        assert out_key.stat().st_size > 100

        # Verify is_ssl_configured returns True
        assert ssl_service.is_ssl_configured(cert_path=out_cert, key_path=out_key) is True

    def test_get_ssl_context(self, tmp_path):
        """Verifies that get_ssl_context returns a valid Python TLS server SSLContext."""
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"

        ctx = ssl_service.get_ssl_context(
            cert_path=cert_file,
            key_path=key_file,
            san_ips=["127.0.0.1", "192.168.137.1"],
        )

        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.protocol == ssl.PROTOCOL_TLS_SERVER
        if hasattr(ssl, "TLSVersion"):
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_network_service_defaults_to_https(self):
        """Ensures network_service.get_mobile_connection_info defaults to HTTPS and port 8443."""
        info = network_service.get_mobile_connection_info(port=8000, ssl_port=8443)
        assert info["success"] is True
        assert info["use_https"] is True
        assert info["scheme"] == "https"
        assert info["active_port"] == 8443
        assert info["primary_url"].startswith("https://")
        assert ":8443/mobile/" in info["primary_url"]

    def test_api_network_info_endpoint_returns_https(self):
        """Tests that /api/network/info/ returns HTTPS pairing URL by default."""
        client = Client()
        url = reverse("core:api_network_info")
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["primary_url"].startswith("https://")
        assert ":8443/mobile/" in data["primary_url"]
        assert "https_primary_url" in data
        assert "http_primary_url" in data
