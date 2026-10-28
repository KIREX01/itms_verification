"""
Zero-Trust Device (IP, MAC Address & Cryptographic Fingerprint) and Time-Bound Email Access Control Service.

Provides:
1. Real-time client IP, CIDR, Layer-2 ARP / Host NIC MAC address resolution, and
   cryptographic device token (`itms_device_trust`) identification with automatic backfill.
2. Automatic onboarding & trust preservation for already-active accounts and linked devices.
3. Multi-device account sharing (e.g., Team Leader on Desktop + Fitter taking photos on
   Mobile Companion) with individual device tracking and configurable per-account device slots.
4. Cryptographically signed QR Code Pairing Tokens (`?pair_token=...`) so phones scanning
   the Supervisor/TUI QR code are automatically authorized and linked to the shared account.
5. Time-Bound Email Invitations & Access Grants (`EmailAccessRequest` + `AuthorizedDevice.expires_at`)
   managed centrally from the TUI (with quick email invitation available in the Web UI).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from core.models import AuthorizedDevice, EmailAccessRequest, OperatorITMSSession
from core.services import config_service, device_service, email_service, network_service
from core.services.security_envelope import extract_client_ip

logger = logging.getLogger(__name__)

DEVICE_TRUST_COOKIE_NAME = "itms_device_trust"
DEVICE_TRUST_COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90 days
QR_PAIR_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours (full shift validity)
DEFAULT_ACCESS_DURATION_HOURS = 24  # Default time-bound window for granted emails (24h / 1 day)

_ARP_CACHE_LOCK = threading.Lock()
_ARP_MAC_CACHE: Dict[str, Tuple[str, float]] = {}
_ARP_CACHE_TTL = 60.0
_LOCAL_MAC_CACHE: Optional[str] = None

_MAC_REGEX = re.compile(r"([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})")


def normalize_mac_address(raw_mac: Optional[str]) -> str:
    """Normalizes a MAC address string to uppercase colon-separated format (AA:BB:CC:DD:EE:FF)."""
    if not raw_mac:
        return ""
    m = _MAC_REGEX.search(str(raw_mac).strip())
    if not m:
        clean_hex = re.sub(r"[^0-9A-Fa-f]", "", str(raw_mac).strip())
        if len(clean_hex) == 12:
            mac = ":".join(clean_hex[i:i + 2].upper() for i in range(0, 12, 2))
            return "" if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF") else mac
        return ""
    parts = re.split(r"[:\-]", m.group(1))
    if len(parts) == 6:
        mac = ":".join(p.upper().zfill(2) for p in parts)
        if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            return ""
        return mac
    return ""


def resolve_local_host_mac_address() -> str:
    """
    Resolves the primary physical network interface MAC address of the local host/server.
    Used to automatically populate MAC addresses for local workstation / TUI sessions.
    """
    global _LOCAL_MAC_CACHE
    if _LOCAL_MAC_CACHE is not None:
        return _LOCAL_MAC_CACHE

    sys_net = "/sys/class/net"
    if os.path.isdir(sys_net):
        try:
            for iface in sorted(os.listdir(sys_net)):
                if iface == "lo" or iface.startswith(("docker", "veth", "br-", "virbr")):
                    continue
                addr_file = os.path.join(sys_net, iface, "address")
                if os.path.isfile(addr_file):
                    with open(addr_file, "r", encoding="utf-8") as f:
                        cand = normalize_mac_address(f.read().strip())
                        if cand:
                            _LOCAL_MAC_CACHE = cand
                            return cand
        except Exception:
            pass

    try:
        node = uuid.getnode()
        hex_mac = f"{node:012X}"
        cand = normalize_mac_address(":".join(hex_mac[i:i + 2] for i in range(0, 12, 2)))
        _LOCAL_MAC_CACHE = cand
        return cand
    except Exception:
        _LOCAL_MAC_CACHE = ""
        return ""


def resolve_local_host_ip() -> str:
    """Resolves the primary outbound LAN IP of the local host, falling back to 127.0.0.1."""
    try:
        lan_ip = network_service.get_primary_lan_ip()
        if lan_ip:
            return lan_ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def resolve_client_mac_address(client_ip: str, request: Optional[HttpRequest] = None) -> str:
    """
    Resolves the client's Layer-2 MAC address automatically:
    1. Explicit headers/params (`X-Device-MAC`, `device_mac`) if provided by client/companion.
    2. Local physical NIC MAC if `client_ip` is loopback (`127.0.0.1`, `::1`, `localhost`) or matches local host IP.
    3. `/proc/net/arp` (Linux), `ip neigh`, or `arp -a` (Windows) for LAN/Hotspot devices (60s cache).
    """
    if request is not None:
        header_mac = (
            request.headers.get("X-Device-MAC")
            or request.POST.get("device_mac")
            or request.GET.get("device_mac")
            or ""
        )
        norm_hdr = normalize_mac_address(header_mac)
        if norm_hdr:
            return norm_hdr

    if not client_ip or client_ip in ("127.0.0.1", "::1", "localhost"):
        return resolve_local_host_mac_address()

    if not network_service.is_private_or_loopback_ip(client_ip):
        return ""

    now = time.time()
    with _ARP_CACHE_LOCK:
        cached = _ARP_MAC_CACHE.get(client_ip)
        if cached and (now - cached[1]) < _ARP_CACHE_TTL:
            return cached[0]

    resolved_mac = ""
    proc_arp = "/proc/net/arp"
    if os.path.exists(proc_arp):
        try:
            with open(proc_arp, "r", encoding="utf-8") as f:
                for line in f.readlines()[1:]:
                    cols = line.split()
                    if len(cols) >= 4 and cols[0] == client_ip:
                        candidate = normalize_mac_address(cols[3])
                        if candidate:
                            resolved_mac = candidate
                            break
        except Exception:
            pass

    if not resolved_mac:
        try:
            cmd = ["arp", "-a", client_ip] if os.name == "nt" else ["ip", "neigh", "show", client_ip]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.2, check=False)
            if proc.returncode == 0 and proc.stdout:
                resolved_mac = normalize_mac_address(proc.stdout)
        except Exception:
            pass

    if not resolved_mac and client_ip == resolve_local_host_ip():
        resolved_mac = resolve_local_host_mac_address()

    with _ARP_CACHE_LOCK:
        _ARP_MAC_CACHE[client_ip] = (resolved_mac, now)

    return resolved_mac


def parse_duration_hours(raw_duration: Any, default_hours: int = DEFAULT_ACCESS_DURATION_HOURS) -> int:
    """
    Parses a human duration input such as '12', '24h', '7d', '30d', '1w' into integer hours.
    Clamped between 1 hour and 8760 hours (365 days).
    """
    if raw_duration is None:
        return default_hours
    s = str(raw_duration).strip().lower()
    if not s:
        return default_hours
    m = re.match(r"^(\d+)\s*([hdwm]?)$", s)
    if not m:
        try:
            val = int(float(s))
            return max(1, min(8760, val))
        except Exception:
            return default_hours
    num = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        hours = num * 24
    elif unit == "w":
        hours = num * 24 * 7
    elif unit == "m":
        hours = num * 24 * 30
    else:
        hours = num
    return max(1, min(8760, hours))


def format_expiry_countdown(expires_at: Optional[Any]) -> str:
    """Formats an `expires_at` datetime into a concise human countdown string for TUI/UI."""
    if not expires_at:
        return "Permanent (Core)"
    now = timezone.now()
    delta = expires_at - now
    total_sec = int(delta.total_seconds())
    if total_sec <= 0:
        return "EXPIRED"
    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    mins = (total_sec % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h left"
    if hours > 0:
        return f"{hours}h {mins}m left"
    return f"{max(1, mins)}m left"


def _get_hmac_secret() -> bytes:
    secret = getattr(settings, "SECRET_KEY", "itms-local-fallback-secret-key")
    return str(secret).encode("utf-8")


def sign_device_trust_token(device_id: str, email_or_username: str = "") -> str:
    """Signs a device_id + identity binding using HMAC-SHA256."""
    payload = f"{device_id}|{email_or_username or 'station'}|{int(time.time())}"
    sig = hmac.new(_get_hmac_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_device_trust_token(token: str) -> Optional[Dict[str, str]]:
    """Verifies a signed `itms_device_trust` cookie token."""
    if not token or "|" not in token:
        return None
    parts = token.split("|")
    if len(parts) != 4:
        return None
    device_id, identity, ts_str, sig = parts
    payload = f"{device_id}|{identity}|{ts_str}"
    expected_sig = hmac.new(_get_hmac_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return {
        "device_id": device_id,
        "identity": identity,
        "timestamp": ts_str,
    }


def generate_qr_pairing_token(user: Optional[Any] = None, **_kwargs: Any) -> str:
    """
    Generates a cryptographically signed QR pairing token (`pair_token`) for mobile companions.
    """
    uid = ""
    if user and getattr(user, "is_authenticated", False):
        uid = str(user.pk)
    else:
        first_op = User.objects.filter(is_active=True).order_by("-is_superuser", "id").first()
        if first_op:
            uid = str(first_op.pk)
    if not uid:
        return ""
    ts = str(int(time.time()))
    payload = f"QRPAIR:{uid}:{ts}"
    sig = hmac.new(_get_hmac_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{uid}.{ts}.{sig}"


def verify_qr_pairing_token(token: str) -> Optional[User]:
    """Verifies a `pair_token` scanned from a Desktop/TUI QR code and returns the linked User."""
    if not token or "." not in token:
        return None
    parts = str(token).strip().split(".")
    if len(parts) != 3:
        return None
    uid_str, ts_str, sig = parts
    if not uid_str.isdigit() or not ts_str.isdigit():
        return None
    ts = int(ts_str)
    if abs(int(time.time()) - ts) > QR_PAIR_TOKEN_TTL_SECONDS:
        return None
    payload = f"QRPAIR:{uid_str}:{ts_str}"
    expected_sig = hmac.new(_get_hmac_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return User.objects.filter(pk=int(uid_str), is_active=True).first()


def extract_device_identity(request: HttpRequest) -> Dict[str, str]:
    """
    Extracts a complete, normalized device identity profile from an incoming HTTP request,
    automatically filling client IP, ARP/Host MAC address, and deterministic Device ID.
    """
    client_ip = extract_client_ip(request)
    mac_address = resolve_client_mac_address(client_ip, request=request)
    user_agent = (request.META.get("HTTP_USER_AGENT") or "").strip()
    is_mobile = device_service.is_mobile_device(request)
    device_type = (
        AuthorizedDevice.DeviceType.MOBILE
        if is_mobile
        else AuthorizedDevice.DeviceType.DESKTOP
    )

    explicit_dev_id = (
        request.headers.get("X-Device-ID")
        or request.POST.get("device_id")
        or request.GET.get("device_id")
        or ""
    ).strip()

    cookie_token = request.COOKIES.get(DEVICE_TRUST_COOKIE_NAME, "")
    verified_cookie = verify_device_trust_token(cookie_token) if cookie_token else None

    client_fp = (
        request.headers.get("X-Device-Fingerprint")
        or request.POST.get("device_fingerprint")
        or ""
    ).strip()
    fp_source = f"{user_agent}|{client_fp}|{mac_address or client_ip}"
    fingerprint_hash = hashlib.sha256(fp_source.encode("utf-8")).hexdigest()[:32]

    if verified_cookie and verified_cookie.get("device_id"):
        device_id = verified_cookie["device_id"]
    elif explicit_dev_id:
        device_id = re.sub(r"[^A-Za-z0-9_\-:]", "", explicit_dev_id)[:96]
    elif mac_address and client_ip not in ("127.0.0.1", "::1", "localhost"):
        device_id = f"MAC-{mac_address.replace(':', '')}"
    else:
        ua_hash = hashlib.sha256(f"{user_agent}|{client_ip}".encode("utf-8")).hexdigest()[:16].upper()
        prefix = "MOB" if is_mobile else "DSK"
        device_id = f"{prefix}-{ua_hash}"

    device_name = _derive_friendly_device_name(user_agent, is_mobile, client_ip)
    return {
        "device_id": device_id,
        "client_ip": client_ip,
        "mac_address": mac_address,
        "device_type": device_type,
        "device_name": device_name,
        "user_agent": user_agent[:500],
        "fingerprint_hash": fingerprint_hash,
        "has_valid_trust_cookie": "1" if verified_cookie else "0",
    }


def _derive_friendly_device_name(user_agent: str, is_mobile: bool, client_ip: str) -> str:
    ua_low = (user_agent or "").lower()
    os_name = "Workstation"
    if "android" in ua_low:
        os_name = "Android"
    elif "iphone" in ua_low or "ipad" in ua_low:
        os_name = "iOS"
    elif "windows" in ua_low:
        os_name = "Windows"
    elif "macintosh" in ua_low or "mac os" in ua_low:
        os_name = "macOS"
    elif "linux" in ua_low:
        os_name = "Linux"

    browser = "Client"
    if "edg/" in ua_low:
        browser = "Edge"
    elif "chrome/" in ua_low and "edg/" not in ua_low:
        browser = "Chrome"
    elif "firefox/" in ua_low:
        browser = "Firefox"
    elif "safari/" in ua_low and "chrome/" not in ua_low:
        browser = "Safari"

    role_prefix = "📱 Mobile" if is_mobile else "💻 Console"
    return f"{role_prefix} ({os_name} • {browser})"


def get_security_access_config() -> Dict[str, Any]:
    """Returns current device & time-bound email access configuration."""
    cfg = config_service.load_config()
    sec = cfg.get("security_access", {})
    return {
        "mode": sec.get("mode", "STANDARD"),
        "require_email_approval": bool(sec.get("require_email_approval", False)),
        "max_devices_per_account": int(sec.get("max_devices_per_account", 4) or 4),
        "default_duration_hours": int(sec.get("default_duration_hours", DEFAULT_ACCESS_DURATION_HOURS) or DEFAULT_ACCESS_DURATION_HOURS),
    }


def update_security_access_config(
    mode: Optional[Any] = None,
    require_email_approval: Optional[bool] = None,
    max_devices_per_account: Optional[int] = None,
    default_duration_hours: Optional[int] = None,
) -> Dict[str, Any]:
    if isinstance(mode, dict):
        updates = mode
        mode = updates.get("mode")
        if "require_email_approval" in updates:
            require_email_approval = updates.get("require_email_approval")
        if "max_devices_per_account" in updates:
            max_devices_per_account = updates.get("max_devices_per_account")
        if "default_duration_hours" in updates:
            default_duration_hours = updates.get("default_duration_hours")
    cfg = config_service.load_config()
    sec = cfg.get("security_access", {})
    if mode is not None:
        clean_mode = str(mode).strip().upper()
        if clean_mode in ("STRICT", "STANDARD"):
            sec["mode"] = clean_mode
            sec["require_email_approval"] = (clean_mode == "STRICT")
    if require_email_approval is not None:
        sec["require_email_approval"] = bool(require_email_approval)
        sec["mode"] = "STRICT" if sec["require_email_approval"] else "STANDARD"
    if max_devices_per_account is not None:
        sec["max_devices_per_account"] = max(1, min(20, int(max_devices_per_account)))
    if default_duration_hours is not None:
        sec["default_duration_hours"] = parse_duration_hours(default_duration_hours)
    cfg["security_access"] = sec
    config_service.save_config(cfg)
    return get_security_access_config()


def sync_existing_active_accounts_and_devices() -> int:
    """
    Automatically onboards already-active operator accounts & linked ITMS sessions into
    `AuthorizedDevice` and backfills missing IP and MAC addresses from local host / ARP tables.
    Returns count of created or updated records.
    """
    touched = 0
    local_ip = resolve_local_host_ip()
    local_mac = resolve_local_host_mac_address()
    now = timezone.now()

    try:
        # 1. Backfill missing MAC addresses on existing AuthorizedDevice rows via ARP or local NIC
        for dev in AuthorizedDevice.objects.filter(mac_address=""):
            if dev.ip_address:
                resolved = resolve_client_mac_address(dev.ip_address)
                if resolved:
                    dev.mac_address = resolved
                    dev.save(update_fields=["mac_address"])
                    touched += 1

        # 2. Backfill missing MAC addresses on EmailAccessRequest rows
        for req in EmailAccessRequest.objects.filter(mac_address=""):
            if req.client_ip:
                resolved = resolve_client_mac_address(req.client_ip)
                if resolved:
                    req.mac_address = resolved
                    req.save(update_fields=["mac_address"])
                    touched += 1

        # 3. Ensure every already-active User account has at least one registered AuthorizedDevice
        active_users = list(User.objects.filter(is_active=True))
        itms_emails = {
            s.user_id: (s.itms_email or "").strip().lower()
            for s in OperatorITMSSession.objects.filter(user__in=active_users)
        }
        for u in active_users:
            has_dev = AuthorizedDevice.objects.filter(user=u).exists()
            u_email = (u.email or itms_emails.get(u.id) or "").strip().lower()
            if not has_dev:
                dev_id = f"ACTIVE-USER-{u.id}"
                AuthorizedDevice.objects.get_or_create(
                    device_id=dev_id,
                    defaults={
                        "user": u,
                        "authorized_email": u_email,
                        "device_name": f"🖥️ Active Account ({u.username})",
                        "device_type": AuthorizedDevice.DeviceType.TUI,
                        "linked_via": AuthorizedDevice.LinkedVia.EXISTING_TRUSTED,
                        "ip_address": local_ip,
                        "mac_address": local_mac,
                        "status": AuthorizedDevice.Status.APPROVED,
                        "approved_at": now,
                        "last_seen_at": now,
                    },
                )
                touched += 1
    except Exception as exc:
        logger.debug("Active account/device auto-sync notice: %s", exc)

    return touched


def _ip_matches_rule(client_ip: str, rule_ip_or_cidr: str) -> bool:
    if not client_ip or not rule_ip_or_cidr:
        return False
    rule_clean = rule_ip_or_cidr.strip()
    if client_ip == rule_clean:
        return True
    if "/" in rule_clean:
        try:
            net = ipaddress.ip_network(rule_clean, strict=False)
            addr = ipaddress.ip_address(client_ip)
            return addr in net
        except ValueError:
            return False
    return False


def is_ip_or_mac_blocked(
    client_ip: str,
    mac_address: str = "",
    device_id: str = "",
) -> Tuple[bool, Optional[AuthorizedDevice], str]:
    """Checks if incoming IP, CIDR subnet, MAC address, or device_id is BLOCKED or REVOKED."""
    try:
        blocked_qs = list(
            AuthorizedDevice.objects.filter(
                status__in=[AuthorizedDevice.Status.BLOCKED, AuthorizedDevice.Status.REVOKED]
            )
        )
        for dev in blocked_qs:
            if device_id and dev.device_id == device_id:
                return True, dev, dev.block_reason or "This device has been blocked."
            if mac_address and dev.mac_address and dev.mac_address.upper() == mac_address.upper():
                return True, dev, dev.block_reason or f"MAC address ({mac_address}) is blocked."
            if client_ip and dev.pinned_ip_cidr and _ip_matches_rule(client_ip, dev.pinned_ip_cidr):
                return True, dev, dev.block_reason or f"Network ({dev.pinned_ip_cidr}) is blocked."
            if (
                client_ip
                and dev.device_type == AuthorizedDevice.DeviceType.NETWORK_RULE
                and dev.ip_address
                and _ip_matches_rule(client_ip, dev.ip_address)
            ):
                return True, dev, dev.block_reason or f"IP address ({client_ip}) is blocked."
    except Exception as exc:
        logger.debug("Blocklist check notice: %s", exc)

    return False, None, ""


def _resolve_email_expiry_for_user(user: Optional[User], email: str = "") -> Tuple[bool, Optional[Any], str]:
    """
    Checks if a granted email access window has expired.
    Returns: (is_expired, expires_at_dt, reason)
    Core superusers are exempt from automatic lockout.
    """
    if user and getattr(user, "is_superuser", False):
        return False, None, ""

    target_email = (email or (user.email if user else "") or "").strip().lower()
    if not target_email:
        return False, None, ""

    req = EmailAccessRequest.objects.filter(email__iexact=target_email).order_by("-updated_at").first()
    if not req:
        return False, None, ""

    now = timezone.now()
    if req.status == EmailAccessRequest.Status.EXPIRED:
        return True, req.expires_at, f"Time-bound access for {target_email} has expired. Please request renewal."

    if req.expires_at and now >= req.expires_at:
        req.status = EmailAccessRequest.Status.EXPIRED
        req.save(update_fields=["status", "updated_at"])
        AuthorizedDevice.objects.filter(authorized_email__iexact=target_email).exclude(
            status=AuthorizedDevice.Status.BLOCKED
        ).update(status=AuthorizedDevice.Status.EXPIRED)
        return True, req.expires_at, f"Time-bound access for {target_email} expired. Please request access renewal."

    return False, req.expires_at, ""


def register_or_refresh_authorized_device(
    request: HttpRequest,
    user: Optional[User] = None,
    linked_via: str = AuthorizedDevice.LinkedVia.DIRECT_LOGIN,
    force_approve: bool = False,
    expires_at: Optional[Any] = None,
) -> Tuple[AuthorizedDevice, bool, str]:
    """
    Registers or refreshes a device for an operator account, auto-filling IP and MAC address
    and enforcing time-bound email/device expiration.
    """
    ident = extract_device_identity(request)
    dev_id = ident["device_id"]
    client_ip = ident["client_ip"]
    mac_address = ident["mac_address"]

    # 1. Explicit blocklist check
    is_blocked, blocked_dev, reason = is_ip_or_mac_blocked(client_ip, mac_address, dev_id)
    if is_blocked and blocked_dev and not force_approve:
        return blocked_dev, False, reason

    target_user = user if (user and getattr(user, "is_authenticated", False) or isinstance(user, User)) else None
    email = (target_user.email if target_user and target_user.email else "").strip().lower()
    if not email and target_user:
        try:
            sess = OperatorITMSSession.objects.filter(user=target_user).first()
            if sess and sess.itms_email:
                email = sess.itms_email.strip().lower()
        except Exception:
            pass

    # 2. Check time-bound email expiry (unless force_approve is actively renewing it)
    email_expired, email_expires_at, exp_msg = _resolve_email_expiry_for_user(target_user, email)
    if email_expired and not force_approve:
        existing_exp = AuthorizedDevice.objects.filter(device_id=dev_id).first()
        if existing_exp:
            return existing_exp, False, exp_msg
        dev_exp = AuthorizedDevice.objects.create(
            device_id=dev_id,
            user=target_user,
            authorized_email=email,
            device_name=ident["device_name"],
            device_type=ident["device_type"],
            linked_via=linked_via,
            ip_address=client_ip,
            mac_address=mac_address,
            status=AuthorizedDevice.Status.EXPIRED,
            expires_at=email_expires_at,
        )
        return dev_exp, False, exp_msg

    effective_expires_at = expires_at or email_expires_at

    # Backfill EmailAccessRequest telemetry (MAC / IP / Device ID) automatically if matching email exists
    if email:
        EmailAccessRequest.objects.filter(email__iexact=email, mac_address="").exclude(
            mac_address=mac_address
        ).update(
            mac_address=mac_address,
            client_ip=client_ip,
            device_id=dev_id,
            device_name=ident["device_name"],
        )

    existing = AuthorizedDevice.objects.filter(device_id=dev_id).first()
    if not existing and mac_address and client_ip not in ("127.0.0.1", "::1", "localhost"):
        existing = AuthorizedDevice.objects.filter(mac_address__iexact=mac_address).first()

    now = timezone.now()
    if existing:
        if existing.status in (AuthorizedDevice.Status.BLOCKED, AuthorizedDevice.Status.REVOKED) and not force_approve:
            return existing, False, existing.block_reason or "Device is blocked by administrator."

        # Check device-level time-bound expiry
        if (
            not force_approve
            and not (target_user and target_user.is_superuser)
            and existing.expires_at
            and now >= existing.expires_at
        ):
            if existing.status != AuthorizedDevice.Status.EXPIRED:
                existing.status = AuthorizedDevice.Status.EXPIRED
                existing.save(update_fields=["status"])
            return existing, False, "Time-bound access for this device has expired. Please request renewal."

        if (
            existing.status == AuthorizedDevice.Status.APPROVED
            and existing.pinned_ip_cidr
            and not _ip_matches_rule(client_ip, existing.pinned_ip_cidr)
            and not force_approve
        ):
            return existing, False, f"Device is pinned to IP/Subnet {existing.pinned_ip_cidr} (current IP: {client_ip})."

        update_fields = ["ip_address", "last_seen_at", "user_agent", "fingerprint_hash"]
        existing.ip_address = client_ip
        existing.last_seen_at = now
        existing.user_agent = ident["user_agent"]
        existing.fingerprint_hash = ident["fingerprint_hash"]
        if mac_address and existing.mac_address != mac_address:
            existing.mac_address = mac_address
            update_fields.append("mac_address")
        if target_user and not existing.user:
            existing.user = target_user
            update_fields.append("user")
        if email and not existing.authorized_email:
            existing.authorized_email = email
            update_fields.append("authorized_email")
        if effective_expires_at and existing.expires_at != effective_expires_at:
            existing.expires_at = effective_expires_at
            update_fields.append("expires_at")
        if force_approve and existing.status != AuthorizedDevice.Status.APPROVED:
            existing.status = AuthorizedDevice.Status.APPROVED
            existing.approved_at = now
            update_fields.extend(["status", "approved_at"])
        existing.save(update_fields=list(dict.fromkeys(update_fields)))
        is_ok = (existing.status == AuthorizedDevice.Status.APPROVED)
        return existing, is_ok, "" if is_ok else "Device access is pending or expired."

    # New device for this account: check multi-device sharing allowance
    sec_cfg = get_security_access_config()
    max_devices = sec_cfg["max_devices_per_account"]
    if email:
        req_obj = EmailAccessRequest.objects.filter(
            email__iexact=email,
            status__in=[EmailAccessRequest.Status.APPROVED, EmailAccessRequest.Status.PRE_APPROVED],
        ).first()
        if req_obj and req_obj.max_devices_allowed:
            max_devices = max(max_devices, req_obj.max_devices_allowed)

    active_user_devices_count = 0
    if target_user:
        # Exclude synthetic TUI placeholder row when counting browser/mobile devices
        active_user_devices_count = AuthorizedDevice.objects.filter(
            user=target_user,
            status=AuthorizedDevice.Status.APPROVED,
        ).exclude(device_id__startswith="ACTIVE-USER-").count()

    should_approve = bool(
        force_approve
        or linked_via == AuthorizedDevice.LinkedVia.QR_PAIRING
        or (target_user and (target_user.is_superuser or target_user.is_staff))
        or (target_user and target_user.is_active and active_user_devices_count < max_devices)
        or not sec_cfg["require_email_approval"]
    )

    new_status = AuthorizedDevice.Status.APPROVED if should_approve else AuthorizedDevice.Status.PENDING
    dev = AuthorizedDevice.objects.create(
        device_id=dev_id,
        user=target_user,
        authorized_email=email,
        device_name=ident["device_name"],
        device_type=ident["device_type"],
        linked_via=linked_via,
        ip_address=client_ip,
        mac_address=mac_address,
        fingerprint_hash=ident["fingerprint_hash"],
        user_agent=ident["user_agent"],
        status=new_status,
        approved_at=now if should_approve else None,
        expires_at=effective_expires_at,
        last_seen_at=now,
    )
    if not should_approve and target_user:
        create_or_update_access_request(
            request=request,
            email=email or f"{target_user.username}@local",
            username=target_user.username,
            full_name=target_user.get_full_name(),
            reason=f"Additional device #{active_user_devices_count + 1} on shared account '{target_user.username}'.",
            email_verified=True,
        )
        return dev, False, f"Account '{target_user.username}' reached {max_devices} shared devices. Approval requested."

    return dev, should_approve, ""


def evaluate_request_access(request: HttpRequest) -> Dict[str, Any]:
    """
    Evaluates whether an incoming HTTP request is permitted by IP/MAC/Device rules & Time-Bound expiry.
    Also handles automatic QR Code Pairing (`?pair_token=...`) for mobile companions.
    """
    ident = extract_device_identity(request)
    client_ip = ident["client_ip"]
    mac_address = ident["mac_address"]
    device_id = ident["device_id"]

    # 1. Explicit IP, CIDR, MAC, or Device ID blocklist
    is_blocked, blocked_dev, block_reason = is_ip_or_mac_blocked(client_ip, mac_address, device_id)
    if is_blocked:
        return {
            "allowed": False,
            "code": "DEVICE_BLOCKED",
            "reason": block_reason,
            "device": blocked_dev,
            "identity": ident,
        }

    # 2. Mobile QR Pairing Token (`?pair_token=...`)
    pair_token = (request.GET.get("pair_token") or "").strip()
    if pair_token:
        paired_user = verify_qr_pairing_token(pair_token)
        if paired_user:
            email_exp, _, exp_reason = _resolve_email_expiry_for_user(paired_user, paired_user.email)
            if email_exp:
                return {
                    "allowed": False,
                    "code": "ACCESS_EXPIRED",
                    "reason": exp_reason,
                    "device": None,
                    "identity": ident,
                }
            from django.contrib.auth import login as auth_login
            if not (getattr(request, "user", None) and request.user.is_authenticated):
                auth_login(request, paired_user, backend="django.contrib.auth.backends.ModelBackend")
            dev, _, _ = register_or_refresh_authorized_device(
                request,
                user=paired_user,
                linked_via=AuthorizedDevice.LinkedVia.QR_PAIRING,
                force_approve=True,
            )
            setattr(request, "_attach_device_trust_cookie", (dev.device_id, paired_user.email or paired_user.username))
            return {
                "allowed": True,
                "code": "QR_PAIRED_ALLOWED",
                "reason": "",
                "device": dev,
                "identity": ident,
            }

    # 3. Authenticated operator session: auto-refresh MAC/IP and verify time-bound window
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        dev, is_approved, msg = register_or_refresh_authorized_device(
            request,
            user=user,
            linked_via=AuthorizedDevice.LinkedVia.EXISTING_TRUSTED,
        )
        if not is_approved:
            code = "ACCESS_EXPIRED" if (dev and dev.status == AuthorizedDevice.Status.EXPIRED) else "DEVICE_PENDING_APPROVAL"
            return {
                "allowed": False,
                "code": code,
                "reason": msg or "Access is awaiting approval or has expired.",
                "device": dev,
                "identity": ident,
            }
        if ident.get("has_valid_trust_cookie") != "1":
            setattr(request, "_attach_device_trust_cookie", (dev.device_id, user.email or user.username))
        return {
            "allowed": True,
            "code": "ALLOWED",
            "reason": "",
            "device": dev,
            "identity": ident,
        }

    return {
        "allowed": True,
        "code": "ANONYMOUS_ALLOWED_FOR_AUTH_FLOW",
        "reason": "",
        "device": None,
        "identity": ident,
    }


def attach_device_trust_cookie(response: HttpResponse, device_id: str, identity: str = "") -> HttpResponse:
    """Attaches the signed HTTP-only `itms_device_trust` cookie to the outgoing response."""
    token = sign_device_trust_token(device_id, identity)
    response.set_cookie(
        DEVICE_TRUST_COOKIE_NAME,
        token,
        max_age=DEVICE_TRUST_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


def is_email_preapproved_or_active(email: str) -> bool:
    """
    Returns True if:
    - First-time setup (no users exist yet), OR
    - Active User / ITMS session exists with non-expired access, OR
    - Non-expired EmailAccessRequest (PRE_APPROVED / APPROVED) exists, OR
    - Strict email approval gate is OFF.
    """
    if User.objects.count() == 0:
        return True

    clean_email = (email or "").strip().lower()
    now = timezone.now()

    if clean_email:
        req = EmailAccessRequest.objects.filter(
            email__iexact=clean_email,
            status__in=[EmailAccessRequest.Status.PRE_APPROVED, EmailAccessRequest.Status.APPROVED],
        ).order_by("-updated_at").first()
        if req:
            if req.expires_at and now >= req.expires_at:
                req.status = EmailAccessRequest.Status.EXPIRED
                req.save(update_fields=["status", "updated_at"])
                return False
            return True

    sec_cfg = get_security_access_config()
    if not sec_cfg.get("require_email_approval", False):
        return True

    if not clean_email:
        return False

    if User.objects.filter(email__iexact=clean_email, is_active=True).exists():
        is_exp, _, _ = _resolve_email_expiry_for_user(None, clean_email)
        return not is_exp
    if OperatorITMSSession.objects.filter(itms_email__iexact=clean_email, is_authenticated=True).exists():
        return True

    return False


def create_or_update_access_request(
    request: Optional[HttpRequest],
    email: str,
    username: str = "",
    full_name: str = "",
    facility_code: str = "AGM",
    requested_role: str = "OPERATOR",
    reason: str = "",
    email_verified: bool = False,
    status: Optional[str] = None,
    duration_hours: Optional[int] = None,
    expires_at: Optional[Any] = None,
) -> EmailAccessRequest:
    """Creates or updates an EmailAccessRequest and auto-attaches client IP, MAC, and Device ID."""
    clean_email = (email or "").strip().lower()
    ident = extract_device_identity(request) if request else {
        "device_id": "",
        "client_ip": resolve_local_host_ip(),
        "mac_address": resolve_local_host_mac_address(),
        "device_name": "TUI / System",
        "user_agent": "",
    }

    existing = EmailAccessRequest.objects.filter(email__iexact=clean_email).order_by("-created_at").first()
    code = email_service.generate_2fa_code()
    token = secrets.token_urlsafe(32)
    sec_cfg = get_security_access_config()
    eff_hours = parse_duration_hours(duration_hours, default_hours=sec_cfg["default_duration_hours"])

    if existing:
        if username:
            existing.username = username
        if full_name:
            existing.full_name = full_name
        if facility_code:
            existing.facility_code = facility_code
        if requested_role:
            existing.requested_role = requested_role
        if reason:
            existing.reason = reason
        if ident["device_id"]:
            existing.device_id = ident["device_id"]
        if ident["mac_address"]:
            existing.mac_address = ident["mac_address"]
        if ident["client_ip"]:
            existing.client_ip = ident["client_ip"]
        if ident["device_name"]:
            existing.device_name = ident["device_name"]
        if email_verified:
            existing.email_verified = True
        if duration_hours is not None:
            existing.duration_hours = eff_hours
        if expires_at is not None:
            existing.expires_at = expires_at
        if status:
            existing.status = status
            if status in (EmailAccessRequest.Status.APPROVED, EmailAccessRequest.Status.PRE_APPROVED) and not existing.expires_at:
                existing.expires_at = timezone.now() + timedelta(hours=existing.duration_hours or eff_hours)
        if not existing.activation_code:
            existing.activation_code = code
        if not existing.activation_token:
            existing.activation_token = token
        existing.save()
        return existing

    initial_status = status or EmailAccessRequest.Status.PENDING
    initial_exp = expires_at
    if initial_exp is None and initial_status in (EmailAccessRequest.Status.APPROVED, EmailAccessRequest.Status.PRE_APPROVED):
        initial_exp = timezone.now() + timedelta(hours=eff_hours)

    return EmailAccessRequest.objects.create(
        email=clean_email,
        username=username,
        full_name=full_name,
        facility_code=facility_code,
        requested_role=requested_role,
        reason=reason,
        device_id=ident["device_id"],
        mac_address=ident["mac_address"],
        client_ip=ident["client_ip"],
        device_name=ident["device_name"],
        user_agent=ident["user_agent"],
        email_verified=email_verified,
        status=initial_status,
        duration_hours=eff_hours,
        expires_at=initial_exp,
        activation_code=code,
        activation_token=token,
    )


def preapprove_operator_email(
    email: str,
    facility_code: str = "AGM",
    requested_role: str = "OPERATOR",
    max_devices_allowed: int = 4,
    duration_hours: Any = None,
    admin_user: Optional[User] = None,
    send_invite_email: bool = True,
    invite_url: str = "",
    **_kwargs: Any,
) -> EmailAccessRequest:
    """
    Invites / pre-approves an operator email with a mandatory **time-bound** expiration window.
    """
    clean_email = (email or "").strip().lower()
    sec_cfg = get_security_access_config()
    hours = parse_duration_hours(duration_hours, default_hours=sec_cfg["default_duration_hours"])
    now = timezone.now()
    exp_dt = now + timedelta(hours=hours)

    req = EmailAccessRequest.objects.filter(email__iexact=clean_email).first()
    if req:
        req.status = EmailAccessRequest.Status.PRE_APPROVED
        req.facility_code = facility_code or req.facility_code
        req.requested_role = requested_role or req.requested_role
        req.max_devices_allowed = max(1, int(max_devices_allowed or 4))
        req.duration_hours = hours
        req.expires_at = exp_dt
        req.reviewed_by = admin_user if isinstance(admin_user, User) else None
        req.reviewed_at = now
        if not req.activation_code:
            req.activation_code = email_service.generate_2fa_code()
        req.save()
    else:
        req = EmailAccessRequest.objects.create(
            email=clean_email,
            facility_code=facility_code or "AGM",
            requested_role=requested_role or "OPERATOR",
            status=EmailAccessRequest.Status.PRE_APPROVED,
            max_devices_allowed=max(1, int(max_devices_allowed or 4)),
            duration_hours=hours,
            expires_at=exp_dt,
            activation_code=email_service.generate_2fa_code(),
            activation_token=secrets.token_urlsafe(32),
            reviewed_by=admin_user if isinstance(admin_user, User) else None,
            reviewed_at=now,
        )

    User.objects.filter(email__iexact=clean_email, is_active=False).update(is_active=True)
    AuthorizedDevice.objects.filter(authorized_email__iexact=clean_email).exclude(
        status=AuthorizedDevice.Status.BLOCKED
    ).update(status=AuthorizedDevice.Status.APPROVED, expires_at=exp_dt)

    if send_invite_email:
        try:
            exp_str = f"{timezone.localtime(exp_dt).strftime('%Y-%m-%d %H:%M')} ({hours}h)"
            email_service.send_access_activated_email(
                to_email=clean_email,
                username=req.full_name or req.username or "Operator",
                activation_code=req.activation_code,
                login_url=invite_url or "https://close.kirex.online/signup/",
                expires_at_display=exp_str,
                is_invitation=True,
            )
        except Exception as exc:
            logger.debug("Email invitation send notice: %s", exc)

    return req


def approve_access_request(
    request_id_or_email_or_token: Any,
    admin_user: Optional[User] = None,
    max_devices_allowed: Optional[int] = None,
    duration_hours: Any = None,
    **_kwargs: Any,
) -> Tuple[bool, str, Optional[EmailAccessRequest]]:
    """
    Approves a pending EmailAccessRequest with a **time-bound** expiration window (`expires_at`),
    activates the User account, approves associated device(s), and emails the activation code.
    """
    if isinstance(request_id_or_email_or_token, EmailAccessRequest):
        req_obj: Optional[EmailAccessRequest] = request_id_or_email_or_token
    else:
        q_val = str(request_id_or_email_or_token or "").strip()
        if not q_val:
            return False, "Missing request identifier.", None
        req_obj = EmailAccessRequest.objects.filter(pk=int(q_val)).first() if q_val.isdigit() else None
        if not req_obj:
            req_obj = EmailAccessRequest.objects.filter(
                Q(activation_token=q_val) | Q(email__iexact=q_val) | Q(username__iexact=q_val)
            ).order_by("-created_at").first()

    if not req_obj:
        return False, "Access request not found.", None

    sec_cfg = get_security_access_config()
    hours = parse_duration_hours(
        duration_hours if duration_hours is not None else req_obj.duration_hours,
        default_hours=sec_cfg["default_duration_hours"],
    )
    now = timezone.now()
    exp_dt = now + timedelta(hours=hours)

    req_obj.status = EmailAccessRequest.Status.APPROVED
    req_obj.duration_hours = hours
    req_obj.expires_at = exp_dt
    if max_devices_allowed is not None:
        req_obj.max_devices_allowed = max(1, int(max_devices_allowed))
    if not req_obj.activation_code:
        req_obj.activation_code = email_service.generate_2fa_code()
    req_obj.reviewed_by = admin_user if isinstance(admin_user, User) else None
    req_obj.reviewed_at = now
    req_obj.save()

    user = User.objects.filter(
        Q(email__iexact=req_obj.email) | (Q(username__iexact=req_obj.username) if req_obj.username else Q(pk__in=[]))
    ).first()
    if user and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    dev_q = Q(authorized_email__iexact=req_obj.email)
    if req_obj.device_id:
        dev_q |= Q(device_id=req_obj.device_id)
    if req_obj.mac_address:
        dev_q |= Q(mac_address__iexact=req_obj.mac_address)
    if user:
        dev_q |= Q(user=user)

    AuthorizedDevice.objects.filter(dev_q).update(
        status=AuthorizedDevice.Status.APPROVED,
        block_reason="",
        approved_by=admin_user if isinstance(admin_user, User) else None,
        approved_at=now,
        expires_at=exp_dt,
    )

    if req_obj.device_id and not AuthorizedDevice.objects.filter(device_id=req_obj.device_id).exists():
        AuthorizedDevice.objects.create(
            device_id=req_obj.device_id,
            user=user,
            authorized_email=req_obj.email,
            device_name=req_obj.device_name or "Approved Operator Device",
            linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
            ip_address=req_obj.client_ip,
            mac_address=req_obj.mac_address or resolve_client_mac_address(req_obj.client_ip),
            status=AuthorizedDevice.Status.APPROVED,
            approved_by=admin_user if isinstance(admin_user, User) else None,
            approved_at=now,
            expires_at=exp_dt,
        )

    exp_str = f"{timezone.localtime(exp_dt).strftime('%Y-%m-%d %H:%M')} ({hours}h)"
    try:
        email_service.send_access_activated_email(
            to_email=req_obj.email,
            username=req_obj.username or req_obj.full_name or "Operator",
            activation_code=req_obj.activation_code,
            expires_at_display=exp_str,
            is_invitation=False,
        )
    except Exception as exc:
        logger.debug("Activation email notice: %s", exc)

    return True, f"Approved {req_obj.email} for {hours}h (expires {exp_str}).", req_obj


def extend_or_set_expiry(
    target_email_or_device: str,
    duration_hours: Any = 24,
    admin_user: Optional[User] = None,
) -> Tuple[bool, str]:
    """Extends or sets the time-bound access expiration window (from now) for an email or device."""
    raw = str(target_email_or_device or "").strip()
    if not raw:
        return False, "Target email or device ID is required."

    hours = parse_duration_hours(duration_hours)
    now = timezone.now()
    exp_dt = now + timedelta(hours=hours)
    exp_str = f"{timezone.localtime(exp_dt).strftime('%Y-%m-%d %H:%M')} ({hours}h)"

    updated = 0
    if "@" in raw or raw.isdigit():
        req_qs = (
            EmailAccessRequest.objects.filter(pk=int(raw))
            if raw.isdigit()
            else EmailAccessRequest.objects.filter(email__iexact=raw)
        )
        for req in req_qs:
            req.status = EmailAccessRequest.Status.APPROVED
            req.duration_hours = hours
            req.expires_at = exp_dt
            req.reviewed_by = admin_user if isinstance(admin_user, User) else None
            req.reviewed_at = now
            req.save()
            AuthorizedDevice.objects.filter(authorized_email__iexact=req.email).exclude(
                status=AuthorizedDevice.Status.BLOCKED
            ).update(status=AuthorizedDevice.Status.APPROVED, expires_at=exp_dt)
            updated += 1

    dev_q = Q(device_id=raw) | Q(authorized_email__iexact=raw)
    norm_mac = normalize_mac_address(raw)
    if norm_mac:
        dev_q |= Q(mac_address__iexact=norm_mac)
    dev_cnt = AuthorizedDevice.objects.filter(dev_q).update(
        status=AuthorizedDevice.Status.APPROVED,
        block_reason="",
        expires_at=exp_dt,
    )
    updated += dev_cnt

    if updated == 0:
        return False, f"No email or device found matching '{raw}'."
    return True, f"Access window set to {hours}h (valid until {exp_str})."


def expire_immediately(
    target_email_or_device: str,
    admin_user: Optional[User] = None,
) -> Tuple[bool, str]:
    """Immediately expires time-bound access for an email or device without IP-banning."""
    raw = str(target_email_or_device or "").strip()
    if not raw:
        return False, "Target email or device ID is required."

    now = timezone.now()
    updated = 0
    req_qs = (
        EmailAccessRequest.objects.filter(pk=int(raw))
        if raw.isdigit()
        else EmailAccessRequest.objects.filter(email__iexact=raw)
    )
    for req in req_qs:
        req.status = EmailAccessRequest.Status.EXPIRED
        req.expires_at = now
        req.reviewed_by = admin_user if isinstance(admin_user, User) else None
        req.reviewed_at = now
        req.save()
        AuthorizedDevice.objects.filter(authorized_email__iexact=req.email).update(
            status=AuthorizedDevice.Status.EXPIRED,
            expires_at=now,
        )
        updated += 1

    dev_cnt = AuthorizedDevice.objects.filter(Q(device_id=raw) | Q(authorized_email__iexact=raw)).update(
        status=AuthorizedDevice.Status.EXPIRED,
        expires_at=now,
    )
    updated += dev_cnt
    if updated == 0:
        return False, f"No active grant found matching '{raw}'."
    return True, f"Immediately expired access for '{raw}'."


def activate_with_code(
    request: HttpRequest,
    email: str,
    activation_code: str,
) -> Tuple[bool, str, Optional[AuthorizedDevice]]:
    """
    Unlocks a device using an email and 6-digit activation code, enforcing time-bound expiry.
    """
    clean_email = (email or "").strip().lower()
    clean_code = (activation_code or "").strip()
    if not clean_email or not clean_code:
        return False, "Email and 6-digit code are required.", None

    req_obj = EmailAccessRequest.objects.filter(
        email__iexact=clean_email,
        activation_code=clean_code,
    ).exclude(
        status__in=[EmailAccessRequest.Status.BLOCKED, EmailAccessRequest.Status.REJECTED]
    ).first()

    if not req_obj:
        return False, "Invalid email or activation code.", None

    now = timezone.now()
    if req_obj.status == EmailAccessRequest.Status.EXPIRED or (req_obj.expires_at and now >= req_obj.expires_at):
        req_obj.status = EmailAccessRequest.Status.EXPIRED
        req_obj.save(update_fields=["status", "updated_at"])
        return False, "This activation code has expired. Please submit a new access request.", None

    # Ensure time-bound window is set
    if not req_obj.expires_at:
        sec_cfg = get_security_access_config()
        hours = req_obj.duration_hours or sec_cfg["default_duration_hours"]
        req_obj.expires_at = now + timedelta(hours=hours)

    ident = extract_device_identity(request)
    req_obj.status = EmailAccessRequest.Status.APPROVED
    req_obj.email_verified = True
    if ident["mac_address"]:
        req_obj.mac_address = ident["mac_address"]
    if ident["client_ip"]:
        req_obj.client_ip = ident["client_ip"]
    if ident["device_id"]:
        req_obj.device_id = ident["device_id"]
    req_obj.save()

    user = User.objects.filter(email__iexact=clean_email).first()
    if user and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    dev, _, _ = register_or_refresh_authorized_device(
        request,
        user=user,
        linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
        force_approve=True,
        expires_at=req_obj.expires_at,
    )
    if not dev.authorized_email:
        dev.authorized_email = clean_email
        dev.save(update_fields=["authorized_email"])

    return True, f"Device activated for {clean_email} ({format_expiry_countdown(req_obj.expires_at)}).", dev


def block_device_or_network_target(
    target: str,
    reason: str = "Blocked via TUI Security",
    admin_user: Optional[User] = None,
    **_kwargs: Any,
) -> Tuple[bool, str, Optional[AuthorizedDevice]]:
    """Blocks a device by Device ID, MAC address (`AA:BB:CC:DD:EE:FF`), IP address, or CIDR subnet."""
    raw = (target or "").strip()
    if not raw:
        return False, "Target IP, MAC address, or Device ID is required.", None

    dev: Optional[AuthorizedDevice] = None
    if raw.isdigit():
        dev = AuthorizedDevice.objects.filter(pk=int(raw)).first()
    if not dev:
        dev = AuthorizedDevice.objects.filter(device_id=raw).first()

    if dev:
        dev.status = AuthorizedDevice.Status.BLOCKED
        dev.block_reason = reason or "Blocked by administrator"
        dev.save(update_fields=["status", "block_reason", "last_seen_at"])
        return True, f"Blocked device '{dev.device_name}' ({dev.device_id}).", dev

    norm_mac = normalize_mac_address(raw)
    if norm_mac:
        dev = AuthorizedDevice.objects.filter(mac_address__iexact=norm_mac).first()
        if dev:
            dev.status = AuthorizedDevice.Status.BLOCKED
            dev.block_reason = reason or f"MAC {norm_mac} blocked"
            dev.save(update_fields=["status", "block_reason", "last_seen_at"])
        else:
            dev = AuthorizedDevice.objects.create(
                device_id=f"BLOCK-MAC-{norm_mac.replace(':', '')}",
                device_name=f"Blocked MAC ({norm_mac})",
                device_type=AuthorizedDevice.DeviceType.NETWORK_RULE,
                linked_via=AuthorizedDevice.LinkedVia.ADMIN_RULE,
                mac_address=norm_mac,
                status=AuthorizedDevice.Status.BLOCKED,
                block_reason=reason or f"MAC {norm_mac} blocked",
                approved_by=admin_user if isinstance(admin_user, User) else None,
            )
        return True, f"Blocked hardware MAC address {norm_mac}.", dev

    try:
        rule_str = str(ipaddress.ip_network(raw, strict=False)) if "/" in raw else str(ipaddress.ip_address(raw))
        rule_id = f"BLOCK-IP-{re.sub(r'[^0-9A-Fa-f]', '_', rule_str)}"
        dev, _ = AuthorizedDevice.objects.update_or_create(
            device_id=rule_id,
            defaults={
                "device_name": f"Firewall Rule ({rule_str})",
                "device_type": AuthorizedDevice.DeviceType.NETWORK_RULE,
                "linked_via": AuthorizedDevice.LinkedVia.ADMIN_RULE,
                "ip_address": rule_str if "/" not in rule_str else "",
                "pinned_ip_cidr": rule_str,
                "status": AuthorizedDevice.Status.BLOCKED,
                "block_reason": reason or f"IP/Subnet {rule_str} blocked",
                "approved_by": admin_user if isinstance(admin_user, User) else None,
            },
        )
        return True, f"Blocked IP/Subnet {rule_str}.", dev
    except ValueError:
        pass

    return False, f"Invalid target '{raw}'. Enter IP, CIDR, MAC, or Device ID.", None


def unblock_device_or_target(
    target: str,
    admin_user: Optional[User] = None,
    **_kwargs: Any,
) -> Tuple[bool, str]:
    """Unblocks a previously blocked device, MAC address, or IP firewall rule."""
    raw = (target or "").strip()
    if not raw:
        return False, "Target identifier is required."

    norm_mac = normalize_mac_address(raw)
    q = Q(device_id=raw) | Q(ip_address=raw) | Q(pinned_ip_cidr=raw) | Q(authorized_email__iexact=raw)
    if raw.isdigit():
        q |= Q(pk=int(raw))
    if norm_mac:
        q |= Q(mac_address__iexact=norm_mac)

    matched = list(AuthorizedDevice.objects.filter(q))
    if not matched:
        return False, f"No device or rule found matching '{raw}'."

    now = timezone.now()
    for dev in matched:
        if dev.device_type == AuthorizedDevice.DeviceType.NETWORK_RULE and dev.device_id.startswith("BLOCK-"):
            dev.delete()
        else:
            dev.status = AuthorizedDevice.Status.APPROVED
            dev.block_reason = ""
            dev.approved_by = admin_user if isinstance(admin_user, User) else None
            dev.approved_at = now
            dev.save(update_fields=["status", "block_reason", "approved_by", "approved_at"])

    return True, f"Unblocked {len(matched)} device/rule(s) matching '{raw}'."
