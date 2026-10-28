"""
Zero-Trust Device (IP, MAC Address & Cryptographic Fingerprint) and Email Access Control Service.

Provides:
1. Real-time client IP, CIDR, Layer-2 ARP MAC address resolution (on LAN/Hotspot), and
   cryptographic device token (`itms_device_trust`) identification.
2. Automatic trust preservation for already-linked users and devices.
3. Multi-device account sharing (e.g., Team Leader on Desktop + Fitter taking photos on
   Mobile Companion) with individual device tracking and configurable per-account device slots.
4. Cryptographically signed QR Code Pairing Tokens (`?pair_token=...`) so phones scanning
   the Supervisor/TUI QR code are automatically authorized and linked to the shared account.
5. Self-service Email & Device Activation Requests (`EmailAccessRequest`) with 6-digit
   activation codes and 1-click Administrator approval or IP/MAC blocking.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import subprocess
import threading
import time
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

_ARP_CACHE_LOCK = threading.Lock()
_ARP_MAC_CACHE: Dict[str, Tuple[str, float]] = {}
_ARP_CACHE_TTL = 60.0

_MAC_REGEX = re.compile(r"([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})")


def normalize_mac_address(raw_mac: Optional[str]) -> str:
    """Normalizes a MAC address string to uppercase colon-separated format (AA:BB:CC:DD:EE:FF)."""
    if not raw_mac:
        return ""
    m = _MAC_REGEX.search(str(raw_mac).strip())
    if not m:
        clean_hex = re.sub(r"[^0-9A-Fa-f]", "", str(raw_mac).strip())
        if len(clean_hex) == 12:
            return ":".join(clean_hex[i:i + 2].upper() for i in range(0, 12, 2))
        return ""
    parts = re.split(r"[:\-]", m.group(1))
    if len(parts) == 6:
        mac = ":".join(p.upper().zfill(2) for p in parts)
        if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            return ""
        return mac
    return ""


def resolve_client_mac_address(client_ip: str, request: Optional[HttpRequest] = None) -> str:
    """
    Resolves the client's Layer-2 MAC address:
    1. Checks explicit headers/params (`X-Device-MAC`, `device_mac`) if provided by TUI or companion.
    2. If client_ip is on a local LAN/Hotspot, queries `/proc/net/arp` (Linux) or `arp -a` (Windows)
       with a 60-second in-memory cache.
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
        return ""

    if not network_service.is_private_or_loopback_ip(client_ip):
        return ""

    now = time.time()
    with _ARP_CACHE_LOCK:
        cached = _ARP_MAC_CACHE.get(client_ip)
        if cached and (now - cached[1]) < _ARP_CACHE_TTL:
            return cached[0]

    resolved_mac = ""
    # 1. Fast Linux /proc/net/arp lookup (zero subprocess overhead)
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

    # 2. Fallback to `ip neigh` or `arp -a`
    if not resolved_mac:
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    ["arp", "-a", client_ip],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                    check=False,
                )
            else:
                proc = subprocess.run(
                    ["ip", "neigh", "show", client_ip],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                    check=False,
                )
            if proc.returncode == 0 and proc.stdout:
                resolved_mac = normalize_mac_address(proc.stdout)
        except Exception:
            pass

    with _ARP_CACHE_LOCK:
        _ARP_MAC_CACHE[client_ip] = (resolved_mac, now)

    return resolved_mac


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
    When a phone scans the QR code on the Team Leader's screen, this token automatically
    trusts the phone and links it to the Team Leader's shared account.
    """
    uid = ""
    if user and getattr(user, "is_authenticated", False):
        uid = str(user.pk)
    else:
        # Fallback to primary active operator or superuser on this station
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
    """
    Verifies a `pair_token` scanned from a Desktop/TUI QR code and returns the linked User account.
    """
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
    Extracts a complete, normalized device identity profile from an incoming HTTP request:
    - client_ip
    - mac_address (ARP on LAN or explicit header)
    - device_id (from signed cookie, explicit client header/param, MAC, or browser fingerprint)
    - device_type ('MOBILE' or 'DESKTOP')
    - device_name (Human-readable OS + Browser label)
    - fingerprint_hash
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

    # Check client-supplied hardware traits or device_id
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
    elif mac_address:
        device_id = f"MAC-{mac_address.replace(':', '')}"
    else:
        # Deterministic browser/station ID combining User-Agent + IP subnet/host
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
    os_name = "Unknown OS"
    if "android" in ua_low:
        os_name = "Android Phone"
    elif "iphone" in ua_low or "ipad" in ua_low:
        os_name = "iPhone / iOS"
    elif "windows" in ua_low:
        os_name = "Windows PC"
    elif "macintosh" in ua_low or "mac os" in ua_low:
        os_name = "MacBook / macOS"
    elif "linux" in ua_low:
        os_name = "Linux Workstation"

    browser = "Browser"
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
    """
    Returns current device & email access control configuration from system_config.json.
    Defaults:
    - require_email_approval: False by default so existing workflows/tests run smoothly,
      can be toggled to True ('STRICT') by Admin in Settings or CLI.
    - max_devices_per_account: 4 (allows Team Leader Desktop + up to 3 Mobile Companions).
    """
    cfg = config_service.load_config()
    sec = cfg.get("security_access", {})
    return {
        "mode": sec.get("mode", "STANDARD"),  # "STANDARD" (Blocklist + Auto-Trust Active Users) or "STRICT" (Require Admin Approval for New Signups/Unlinked Devices)
        "require_email_approval": bool(sec.get("require_email_approval", False)),
        "max_devices_per_account": int(sec.get("max_devices_per_account", 4) or 4),
    }


def update_security_access_config(
    mode: Optional[Any] = None,
    require_email_approval: Optional[bool] = None,
    max_devices_per_account: Optional[int] = None,
) -> Dict[str, Any]:
    if isinstance(mode, dict):
        updates = mode
        mode = updates.get("mode")
        if "require_email_approval" in updates:
            require_email_approval = updates.get("require_email_approval")
        if "max_devices_per_account" in updates:
            max_devices_per_account = updates.get("max_devices_per_account")
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
    cfg["security_access"] = sec
    config_service.save_config(cfg)
    return get_security_access_config()


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
    """
    Checks if the incoming client IP, CIDR subnet, MAC address, or device_id is explicitly BLOCKED or REVOKED.
    """
    try:
        blocked_qs = list(
            AuthorizedDevice.objects.filter(
                status__in=[AuthorizedDevice.Status.BLOCKED, AuthorizedDevice.Status.REVOKED]
            )
        )
        for dev in blocked_qs:
            if device_id and dev.device_id == device_id:
                return True, dev, dev.block_reason or "This device ID has been blocked by an administrator."
            if mac_address and dev.mac_address and dev.mac_address.upper() == mac_address.upper():
                return True, dev, dev.block_reason or f"Hardware MAC address ({mac_address}) is blocked."
            if client_ip and dev.pinned_ip_cidr and _ip_matches_rule(client_ip, dev.pinned_ip_cidr):
                return True, dev, dev.block_reason or f"Network IP/Subnet ({dev.pinned_ip_cidr}) is blocked."
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


def register_or_refresh_authorized_device(
    request: HttpRequest,
    user: Optional[User] = None,
    linked_via: str = AuthorizedDevice.LinkedVia.DIRECT_LOGIN,
    force_approve: bool = False,
) -> Tuple[AuthorizedDevice, bool, str]:
    """
    Registers or refreshes a device for a user account.
    Respects:
    - Existing linked users/devices are never blocked.
    - Multiple devices can share one account (up to `max_devices_per_account`, e.g., Team Leader + Fitter Phone).
    - QR-paired devices (`linked_via == QR_PAIRING`) are automatically approved.
    Returns: (device, is_approved, message)
    """
    ident = extract_device_identity(request)
    dev_id = ident["device_id"]
    client_ip = ident["client_ip"]
    mac_address = ident["mac_address"]

    # 1. Check explicit blocklist first
    is_blocked, blocked_dev, reason = is_ip_or_mac_blocked(client_ip, mac_address, dev_id)
    if is_blocked and blocked_dev and not force_approve:
        return blocked_dev, False, reason

    target_user = user if (user and getattr(user, "is_authenticated", False)) else None
    email = (target_user.email if target_user and target_user.email else "").strip().lower()
    if not email and target_user:
        try:
            sess = OperatorITMSSession.objects.filter(user=target_user).first()
            if sess and sess.itms_email:
                email = sess.itms_email.strip().lower()
        except Exception:
            pass

    existing = AuthorizedDevice.objects.filter(device_id=dev_id).first()
    if not existing and mac_address:
        existing = AuthorizedDevice.objects.filter(mac_address__iexact=mac_address).first()

    now = timezone.now()
    if existing:
        if existing.status in (AuthorizedDevice.Status.BLOCKED, AuthorizedDevice.Status.REVOKED) and not force_approve:
            return existing, False, existing.block_reason or "Device is blocked by administrator."

        # Check pinned IP/CIDR if configured on this approved device
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
        if mac_address and not existing.mac_address:
            existing.mac_address = mac_address
            update_fields.append("mac_address")
        if target_user and not existing.user:
            existing.user = target_user
            update_fields.append("user")
        if email and not existing.authorized_email:
            existing.authorized_email = email
            update_fields.append("authorized_email")
        if force_approve and existing.status != AuthorizedDevice.Status.APPROVED:
            existing.status = AuthorizedDevice.Status.APPROVED
            existing.approved_at = now
            update_fields.extend(["status", "approved_at"])
        existing.save(update_fields=list(dict.fromkeys(update_fields)))
        is_ok = (existing.status == AuthorizedDevice.Status.APPROVED)
        return existing, is_ok, "" if is_ok else "Device is pending administrator approval."

    # New device for this account: check multi-device sharing allowance
    sec_cfg = get_security_access_config()
    max_devices = sec_cfg["max_devices_per_account"]
    if email:
        req_obj = EmailAccessRequest.objects.filter(email__iexact=email, status=EmailAccessRequest.Status.APPROVED).first()
        if req_obj and req_obj.max_devices_allowed:
            max_devices = max(max_devices, req_obj.max_devices_allowed)

    active_user_devices_count = 0
    if target_user:
        active_user_devices_count = AuthorizedDevice.objects.filter(
            user=target_user,
            status=AuthorizedDevice.Status.APPROVED,
        ).count()

    # Determine if this new device should be auto-approved:
    # - Force approve (QR pairing / Admin approval / Pre-approved email)
    # - Superuser or staff account
    # - Existing linked user within their multi-device sharing limit (e.g. Team Leader + Fitter Phone)
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
        last_seen_at=now,
    )
    if not should_approve and target_user:
        # Automatically create a pending EmailAccessRequest so Admin sees the extra device request
        create_or_update_access_request(
            request=request,
            email=email or f"{target_user.username}@local",
            username=target_user.username,
            full_name=target_user.get_full_name(),
            reason=f"Additional device #{active_user_devices_count + 1} requesting access on shared account '{target_user.username}' (limit: {max_devices}).",
            email_verified=True,
        )
        return dev, False, f"Account '{target_user.username}' already has {active_user_devices_count} active devices (limit: {max_devices}). Request submitted for administrator approval."

    return dev, should_approve, ""


def evaluate_request_access(request: HttpRequest) -> Dict[str, Any]:
    """
    Evaluates whether an incoming HTTP request is permitted by Device (IP/MAC/ID) & Account security rules.
    Also handles automatic QR Code Pairing (`?pair_token=...`) for mobile companions!
    """
    ident = extract_device_identity(request)
    client_ip = ident["client_ip"]
    mac_address = ident["mac_address"]
    device_id = ident["device_id"]

    # 1. Check if IP, CIDR, MAC, or Device ID is explicitly BLOCKED
    is_blocked, blocked_dev, block_reason = is_ip_or_mac_blocked(client_ip, mac_address, device_id)
    if is_blocked:
        return {
            "allowed": False,
            "code": "DEVICE_BLOCKED",
            "reason": block_reason,
            "device": blocked_dev,
            "identity": ident,
        }

    # 2. Check if request carries a valid Mobile QR Pairing Token (`?pair_token=...`)
    pair_token = (request.GET.get("pair_token") or "").strip()
    if pair_token:
        paired_user = verify_qr_pairing_token(pair_token)
        if paired_user:
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

    # 3. If user is authenticated, verify/refresh their device registration
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        dev, is_approved, msg = register_or_refresh_authorized_device(
            request,
            user=user,
            linked_via=AuthorizedDevice.LinkedVia.EXISTING_TRUSTED,
        )
        if not is_approved:
            return {
                "allowed": False,
                "code": "DEVICE_PENDING_APPROVAL",
                "reason": msg or "This device is awaiting administrator approval.",
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
    - No users exist yet in the database (first-time setup), OR
    - An active User or active OperatorITMSSession already exists with this email, OR
    - An EmailAccessRequest with status PRE_APPROVED or APPROVED exists for this email, OR
    - Strict email approval mode is disabled (`require_email_approval == False`).
    """
    if User.objects.count() == 0:
        return True

    sec_cfg = get_security_access_config()
    if not sec_cfg.get("require_email_approval", False):
        return True

    clean_email = (email or "").strip().lower()
    if not clean_email:
        return False

    if User.objects.filter(email__iexact=clean_email, is_active=True).exists():
        return True
    if OperatorITMSSession.objects.filter(itms_email__iexact=clean_email, is_authenticated=True).exists():
        return True
    if EmailAccessRequest.objects.filter(
        email__iexact=clean_email,
        status__in=[EmailAccessRequest.Status.PRE_APPROVED, EmailAccessRequest.Status.APPROVED],
    ).exists():
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
) -> EmailAccessRequest:
    """
    Creates or updates an EmailAccessRequest and binds the requester's device metadata (IP, MAC, Device ID).
    """
    clean_email = (email or "").strip().lower()
    ident = extract_device_identity(request) if request else {
        "device_id": "",
        "client_ip": "127.0.0.1",
        "mac_address": "",
        "device_name": "CLI / System",
        "user_agent": "",
    }

    existing = EmailAccessRequest.objects.filter(email__iexact=clean_email).order_by("-created_at").first()
    code = email_service.generate_2fa_code()
    token = secrets.token_urlsafe(32)

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
        if status:
            existing.status = status
        if not existing.activation_code:
            existing.activation_code = code
        if not existing.activation_token:
            existing.activation_token = token
        existing.save()
        return existing

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
        status=status or EmailAccessRequest.Status.PENDING,
        activation_code=code,
        activation_token=token,
    )


def preapprove_operator_email(
    email: str,
    facility_code: str = "AGM",
    requested_role: str = "OPERATOR",
    max_devices_allowed: int = 4,
    admin_user: Optional[User] = None,
) -> EmailAccessRequest:
    """
    Pre-approves an operator email address so when they sign up (or request device activation),
    their account and devices are automatically approved.
    """
    clean_email = (email or "").strip().lower()
    req = EmailAccessRequest.objects.filter(email__iexact=clean_email).first()
    now = timezone.now()
    if req:
        req.status = EmailAccessRequest.Status.PRE_APPROVED
        req.facility_code = facility_code or req.facility_code
        req.requested_role = requested_role or req.requested_role
        req.max_devices_allowed = max(1, int(max_devices_allowed or 4))
        req.reviewed_by = admin_user
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
            activation_code=email_service.generate_2fa_code(),
            activation_token=secrets.token_urlsafe(32),
            reviewed_by=admin_user,
            reviewed_at=now,
        )

    # Also activate any existing inactive User with this email
    User.objects.filter(email__iexact=clean_email, is_active=False).update(is_active=True)
    return req


def approve_access_request(
    request_id_or_email_or_token: Any,
    admin_user: Optional[User] = None,
    max_devices_allowed: Optional[int] = None,
) -> Tuple[bool, str, Optional[EmailAccessRequest]]:
    """
    Approves a pending EmailAccessRequest, activates the associated User account,
    marks the requester's AuthorizedDevice as APPROVED, and sends an activation notification email.
    """
    q_val = str(request_id_or_email_or_token or "").strip()
    if not q_val:
        return False, "Missing request identifier.", None

    req_obj: Optional[EmailAccessRequest] = None
    if q_val.isdigit():
        req_obj = EmailAccessRequest.objects.filter(pk=int(q_val)).first()
    if not req_obj:
        req_obj = EmailAccessRequest.objects.filter(
            Q(activation_token=q_val) | Q(email__iexact=q_val) | Q(username__iexact=q_val)
        ).order_by("-created_at").first()

    if not req_obj:
        return False, f"No access request found matching '{q_val}'.", None

    now = timezone.now()
    req_obj.status = EmailAccessRequest.Status.APPROVED
    if max_devices_allowed is not None:
        req_obj.max_devices_allowed = max(1, int(max_devices_allowed))
    if not req_obj.activation_code:
        req_obj.activation_code = email_service.generate_2fa_code()
    req_obj.reviewed_by = admin_user
    req_obj.reviewed_at = now
    req_obj.save()

    # Activate any matching User account
    user = User.objects.filter(
        Q(email__iexact=req_obj.email) | (Q(username__iexact=req_obj.username) if req_obj.username else Q(pk__in=[]))
    ).first()
    if user and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    # Approve associated device(s)
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
        approved_by=admin_user,
        approved_at=now,
    )

    if req_obj.device_id and not AuthorizedDevice.objects.filter(device_id=req_obj.device_id).exists():
        AuthorizedDevice.objects.create(
            device_id=req_obj.device_id,
            user=user,
            authorized_email=req_obj.email,
            device_name=req_obj.device_name or "Approved Operator Device",
            linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
            ip_address=req_obj.client_ip,
            mac_address=req_obj.mac_address,
            status=AuthorizedDevice.Status.APPROVED,
            approved_by=admin_user,
            approved_at=now,
        )

    # Send activation confirmation email
    try:
        email_service.send_access_activated_email(
            to_email=req_obj.email,
            username=req_obj.username or req_obj.full_name or "Operator",
            activation_code=req_obj.activation_code,
        )
    except Exception as exc:
        logger.debug("Activation email notice: %s", exc)

    return True, f"Approved access for {req_obj.email} and unlocked associated device(s).", req_obj


def activate_with_code(
    request: HttpRequest,
    email: str,
    activation_code: str,
) -> Tuple[bool, str, Optional[AuthorizedDevice]]:
    """
    Allows an operator on a pending or new device to unlock access immediately using their
    email and 6-digit activation_code.
    """
    clean_email = (email or "").strip().lower()
    clean_code = (activation_code or "").strip()
    if not clean_email or not clean_code:
        return False, "Both email address and activation code are required.", None

    req_obj = EmailAccessRequest.objects.filter(
        email__iexact=clean_email,
        activation_code=clean_code,
    ).exclude(
        status__in=[EmailAccessRequest.Status.BLOCKED, EmailAccessRequest.Status.REJECTED]
    ).first()

    if not req_obj:
        return False, "Invalid email or activation code. Please check with your supervisor.", None

    req_obj.status = EmailAccessRequest.Status.APPROVED
    req_obj.email_verified = True
    req_obj.save(update_fields=["status", "email_verified", "updated_at"])

    user = User.objects.filter(email__iexact=clean_email).first()
    if user and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    dev, _, _ = register_or_refresh_authorized_device(
        request,
        user=user,
        linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
        force_approve=True,
    )
    if not dev.authorized_email:
        dev.authorized_email = clean_email
        dev.save(update_fields=["authorized_email"])

    return True, f"Device successfully activated for {clean_email}!", dev


def block_device_or_network_target(
    target: str,
    reason: str = "Blocked by administrator",
    admin_user: Optional[User] = None,
    **_kwargs: Any,
) -> Tuple[bool, str, Optional[AuthorizedDevice]]:
    """
    Blocks a device by Device ID, MAC address (`AA:BB:CC:DD:EE:FF`), IP address, or CIDR subnet.
    """
    raw = (target or "").strip()
    if not raw:
        return False, "Target IP, MAC address, or Device ID is required.", None

    # 1. Check if target matches an existing AuthorizedDevice ID or PK
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

    # 2. Check if target is a MAC address
    norm_mac = normalize_mac_address(raw)
    if norm_mac:
        dev = AuthorizedDevice.objects.filter(mac_address__iexact=norm_mac).first()
        if dev:
            dev.status = AuthorizedDevice.Status.BLOCKED
            dev.block_reason = reason or f"MAC {norm_mac} blocked by administrator"
            dev.save(update_fields=["status", "block_reason", "last_seen_at"])
        else:
            dev = AuthorizedDevice.objects.create(
                device_id=f"BLOCK-MAC-{norm_mac.replace(':', '')}",
                device_name=f"Blocked MAC ({norm_mac})",
                device_type=AuthorizedDevice.DeviceType.NETWORK_RULE,
                linked_via=AuthorizedDevice.LinkedVia.ADMIN_RULE,
                mac_address=norm_mac,
                status=AuthorizedDevice.Status.BLOCKED,
                block_reason=reason or f"MAC {norm_mac} blocked by administrator",
                approved_by=admin_user,
            )
        return True, f"Blocked hardware MAC address {norm_mac}.", dev

    # 3. Check if target is an IPv4/IPv6 address or CIDR subnet
    try:
        if "/" in raw:
            net = ipaddress.ip_network(raw, strict=False)
            rule_str = str(net)
        else:
            addr = ipaddress.ip_address(raw)
            rule_str = str(addr)
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
                "block_reason": reason or f"IP/Subnet {rule_str} blocked by administrator",
                "approved_by": admin_user,
            },
        )
        return True, f"Blocked IP/Subnet {rule_str}.", dev
    except ValueError:
        pass

    return False, f"Unrecognized target '{raw}'. Enter a valid IP, CIDR, MAC address, or Device ID.", None


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
            dev.approved_by = admin_user
            dev.approved_at = now
            dev.save(update_fields=["status", "block_reason", "approved_by", "approved_at"])

    return True, f"Unblocked and approved {len(matched)} device/rule(s) matching '{raw}'."
