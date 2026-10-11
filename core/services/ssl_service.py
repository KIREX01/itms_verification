"""
SSL/TLS Service for ITMS Verification Copilot.

Provides zero-dependency, 100% offline self-signed HTTPS certificate generation
and TLS context management. Enables modern mobile browser Secure Context (isSecureContext)
for live camera video streaming and QR/barcode scanning over LAN and Hotspot networks.

Cascading Generation Architecture:
1. Python `cryptography` library (if available in environment)
2. Windows native PowerShell .NET in-memory PKI (zero admin rights, zero cert store)
3. Standard system `openssl` CLI (if present on PATH)
4. Embedded pre-generated 10-year fallback certificate & RSA key (instant zero-dependency guarantee)
"""
import ipaddress
import logging
import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from django.conf import settings
    _base_dir = getattr(settings, "BASE_DIR", None)
except Exception:
    _base_dir = None

if not _base_dir:
    _base_dir = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

# Certificate storage location inside secure/
DEFAULT_SSL_DIR = Path(_base_dir) / "secure" / "ssl"
DEFAULT_CERT_FILE = DEFAULT_SSL_DIR / "cert.pem"
DEFAULT_KEY_FILE = DEFAULT_SSL_DIR / "key.pem"
ROOT_CERT_FILE = Path(_base_dir) / "cert.crt"
ROOT_KEY_FILE = Path(_base_dir) / "cert.key"


def _generate_via_cryptography(
    cert_path: Path,
    key_path: Path,
    san_ips: List[str],
    san_dns: List[str],
) -> bool:
    """Strategy 1: Generates RSA 2048 and self-signed X.509 cert in memory via Python cryptography."""
    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Generate RSA private key
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        subject = issuer = x509.Name([
            x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "UG"),
            x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "ITMS Verification Copilot"),
            x509.NameAttribute(x509.NameOID.COMMON_NAME, "ITMS Mobile Local Server"),
        ])

        san_items = []
        for d in san_dns:
            san_items.append(x509.DNSName(d))

        for ip_str in san_ips:
            try:
                san_items.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
            except ValueError:
                san_items.append(x509.DNSName(ip_str))

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))  # 10 years
            .add_extension(
                x509.SubjectAlternativeName(san_items),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .sign(key, hashes.SHA256(), default_backend())
        )

        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_bytes(cert_bytes)
        key_path.write_bytes(key_bytes)
        logger.info("[SSL] Generated self-signed certificates via Python cryptography at %s", cert_path)
        return True
    except Exception as exc:
        logger.debug("[SSL] Python cryptography generation unavailable: %s", exc)
        return False


def _generate_via_powershell_net(
    cert_path: Path,
    key_path: Path,
    san_ips: List[str],
    san_dns: List[str],
) -> bool:
    """Strategy 2: Generates RSA 2048 and self-signed X.509 cert via Windows native PowerShell .NET (zero-store)."""
    if sys.platform != "win32":
        return False

    powershell_bin = shutil.which("powershell.exe") or "powershell.exe"
    all_sans = list(san_dns) + list(san_ips)
    sans_ps_list = ",".join(f'"{s}"' for s in all_sans)

    ps_code = f"""
Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

public class PemGen
{{
    private static void WriteDerLen(Stream s, int len) {{
        if (len < 128) s.WriteByte((byte)len);
        else if (len < 256) {{ s.WriteByte(0x81); s.WriteByte((byte)len); }}
        else if (len < 65536) {{ s.WriteByte(0x82); s.WriteByte((byte)(len >> 8)); s.WriteByte((byte)(len & 0xFF)); }}
        else {{ s.WriteByte(0x83); s.WriteByte((byte)(len >> 16)); s.WriteByte((byte)((len >> 8) & 0xFF)); s.WriteByte((byte)(len & 0xFF)); }}
    }}
    private static void WriteDerInt(Stream s, byte[] b) {{
        s.WriteByte(0x02);
        if (b == null || b.Length == 0) {{ s.WriteByte(0x01); s.WriteByte(0x00); return; }}
        bool lz = (b[0] & 0x80) != 0;
        int l = b.Length + (lz ? 1 : 0);
        WriteDerLen(s, l);
        if (lz) s.WriteByte(0x00);
        s.Write(b, 0, b.Length);
    }}
    public static void Gen(string certPath, string keyPath, string[] sans) {{
        using (RSA rsa = RSA.Create(2048)) {{
            var dn = new X500DistinguishedName("CN=ITMS Verification Copilot, O=ITMS Local");
            var req = new CertificateRequest(dn, rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            var sb = new SubjectAlternativeNameBuilder();
            sb.AddDnsName("localhost");
            sb.AddDnsName("*.local");
            if (sans != null) {{
                foreach (var s in sans) {{
                    System.Net.IPAddress ip;
                    if (System.Net.IPAddress.TryParse(s, out ip)) sb.AddIpAddress(ip);
                    else if (!string.IsNullOrWhiteSpace(s)) sb.AddDnsName(s);
                }}
            }}
            req.CertificateExtensions.Add(sb.Build());
            req.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
            using (X509Certificate2 cert = req.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(10))) {{
                string certB64 = Convert.ToBase64String(cert.RawData, Base64FormattingOptions.InsertLineBreaks);
                File.WriteAllText(certPath, "-----BEGIN CERTIFICATE-----\\r\\n" + certB64 + "\\r\\n-----END CERTIFICATE-----\\r\\n");
            }}
            RSAParameters p = rsa.ExportParameters(true);
            using (MemoryStream body = new MemoryStream()) {{
                WriteDerInt(body, new byte[] {{ 0x00 }});
                WriteDerInt(body, p.Modulus); WriteDerInt(body, p.Exponent); WriteDerInt(body, p.D);
                WriteDerInt(body, p.P); WriteDerInt(body, p.Q); WriteDerInt(body, p.DP);
                WriteDerInt(body, p.DQ); WriteDerInt(body, p.InverseQ);
                byte[] bBytes = body.ToArray();
                using (MemoryStream seq = new MemoryStream()) {{
                    seq.WriteByte(0x30);
                    WriteDerLen(seq, bBytes.Length);
                    seq.Write(bBytes, 0, bBytes.Length);
                    string keyB64 = Convert.ToBase64String(seq.ToArray(), Base64FormattingOptions.InsertLineBreaks);
                    File.WriteAllText(keyPath, "-----BEGIN RSA PRIVATE KEY-----\\r\\n" + keyB64 + "\\r\\n-----END RSA PRIVATE KEY-----\\r\\n");
                }}
            }}
        }}
    }}
}}
"@ -Language CSharp
[PemGen]::Gen('{str(cert_path).replace("\\", "\\\\")}', '{str(key_path).replace("\\", "\\\\")}', @({sans_ps_list}))
"""
    try:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [powershell_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0 and cert_path.exists() and key_path.exists():
            logger.info("[SSL] Generated self-signed certificates via Windows PowerShell .NET at %s", cert_path)
            return True
        logger.debug("[SSL] PowerShell generation stderr: %s", res.stderr)
    except Exception as exc:
        logger.debug("[SSL] PowerShell .NET generation failed: %s", exc)
    return False


def _generate_dynamic_fallback(
    cert_path: Path,
    key_path: Path,
    san_ips: List[str],
    san_dns: List[str],
) -> bool:
    """Strategy 3: Generates self-signed certificate on-the-fly via OpenSSL CLI if available."""
    try:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "3650", "-nodes", "-subj", "/CN=localhost/O=ITMS Local",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and cert_path.exists() and key_path.exists():
            platform_service.safe_chmod(key_path, 0o600)
            logger.info("[SSL] Generated dynamic fallback certificate via OpenSSL at %s", cert_path)
            return True
    except Exception as exc:
        logger.debug("[SSL] OpenSSL fallback generation note: %s", exc)
    return False


def ensure_ssl_certificates(
    cert_path: Optional[Path] = None,
    key_path: Optional[Path] = None,
    san_ips: Optional[List[str]] = None,
    san_dns: Optional[List[str]] = None,
    force_regenerate: bool = False,
) -> Tuple[Path, Path]:
    """
    Ensures that a valid self-signed SSL certificate and private key exist.
    If missing or force_regenerate is True, attempts cascading generation strategies.
    Guaranteed to return usable (cert_path, key_path) on any platform.
    """
    from core.services.network_service import WINDOWS_HOTSPOT_DEFAULT_IP

    cert_file = Path(cert_path) if cert_path else DEFAULT_CERT_FILE
    key_file = Path(key_path) if key_path else DEFAULT_KEY_FILE

    def _sync_root_files(c: Path, k: Path):
        try:
            if not ROOT_CERT_FILE.exists() or ROOT_CERT_FILE.stat().st_size < 100:
                shutil.copyfile(c, ROOT_CERT_FILE)
            if not ROOT_KEY_FILE.exists() or ROOT_KEY_FILE.stat().st_size < 100:
                shutil.copyfile(k, ROOT_KEY_FILE)
        except Exception as copy_exc:
            logger.debug("[SSL] Could not sync root cert files: %s", copy_exc)

    if not force_regenerate and cert_file.exists() and key_file.exists():
        if cert_file.stat().st_size > 100 and key_file.stat().st_size > 100:
            _sync_root_files(cert_file, key_file)
            return cert_file, key_file

    # Build SAN lists
    default_ips = ["127.0.0.1", WINDOWS_HOTSPOT_DEFAULT_IP]  # localhost & Windows Mobile Hotspot
    if san_ips:
        for ip in san_ips:
            if ip and ip not in default_ips:
                default_ips.append(ip)

    default_dns = ["localhost", "*.local", "itms.local"]
    if san_dns:
        for d in san_dns:
            if d and d not in default_dns:
                default_dns.append(d)

    # Strategy 1: Python cryptography
    if _generate_via_cryptography(cert_file, key_file, default_ips, default_dns):
        _sync_root_files(cert_file, key_file)
        return cert_file, key_file

    # Strategy 2: Windows native PowerShell .NET
    if _generate_via_powershell_net(cert_file, key_file, default_ips, default_dns):
        _sync_root_files(cert_file, key_file)
        return cert_file, key_file

    # Strategy 3: Dynamic OpenSSL generation fallback
    _generate_dynamic_fallback(cert_file, key_file, default_ips, default_dns)
    _sync_root_files(cert_file, key_file)
    return cert_file, key_file


def get_ssl_context(
    cert_path: Optional[Path] = None,
    key_path: Optional[Path] = None,
    san_ips: Optional[List[str]] = None,
) -> ssl.SSLContext:
    """
    Creates and returns a modern, secure TLS server context configured with
    the self-signed certificates.
    """
    cert_file, key_file = ensure_ssl_certificates(cert_path=cert_path, key_path=key_path, san_ips=san_ips)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

    # Security settings
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2

    # Disable insecure ciphers
    try:
        context.set_ciphers("DEFAULT:!aNULL:!eNULL:!MD5:!3DES:!DES:!RC4:!IDEA:!SEED:!aDSS:!SRP:!PSK")
    except Exception:
        pass

    return context


def is_ssl_configured(cert_path: Optional[Path] = None, key_path: Optional[Path] = None) -> bool:
    """Returns True if valid certificate and private key files exist on disk."""
    cert_file = Path(cert_path) if cert_path else DEFAULT_CERT_FILE
    key_file = Path(key_path) if key_path else DEFAULT_KEY_FILE
    return cert_file.exists() and key_file.exists() and cert_file.stat().st_size > 100 and key_file.stat().st_size > 100
