"""
Fleet: enrollment tokens, the token-gated installer, the install lifecycle ledger, the security-event ledger,
release policy (minimum version + update checksum) and device anomaly checks.

Builds on InstallRegistration / policy_sync (enrollment, signed check-in, commands) - no parallel system.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"^CLOSE-[A-Z0-9]{1,12}-\d{4}-[A-Z0-9]{8}$")


class FleetError(Exception):
    pass


def _hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().upper().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Security events
# ---------------------------------------------------------------------------
def security_event(event_type: str, request=None, user=None, username: str = "", device_id: str = "",
                   mac: str = "", bond_code: str = "", reason: str = "", ip: str = "") -> None:
    """Appends to the security ledger. Never raises."""
    try:
        from governance.models import SecurityEvent

        if request is not None:
            from core.services.security_envelope import extract_client_ip
            ip = ip or extract_client_ip(request)
            ua = request.META.get("HTTP_USER_AGENT", "")[:300]
            trace = str(getattr(request, "trace_id", "") or request.META.get("HTTP_X_TRACE_ID", ""))[:64]
        else:
            ua, trace = "", ""
        if user is not None and not getattr(user, "pk", None):
            user = None
        SecurityEvent.objects.create(
            event_type=event_type, user=user, username=(username or getattr(user, "username", "") or "")[:254],
            device_id=device_id[:128], client_ip=ip[:64], mac_address=mac[:32], user_agent=ua, bond_code=bond_code[:32],
            trace_id=trace, reason=reason[:300],
        )
    except Exception as exc:
        logger.debug("Security event %s not recorded: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Enrollment tokens
# ---------------------------------------------------------------------------
def issue_token(actor, bond_code: str = "", label: str = "", max_seats: int = 5, hours: int = 24,
                allowed_ip_cidr: str = "", request=None) -> Tuple[Any, str]:
    from governance.models import EnrollmentToken
    from governance.services import audit

    bond = re.sub(r"[^A-Z0-9]", "", (bond_code or "").upper())[:12]
    if not 1 <= int(max_seats) <= 200:
        raise FleetError("Seats must be between 1 and 200.")
    if not 1 <= int(hours) <= 24 * 30:
        raise FleetError("Validity must be between 1 hour and 30 days.")
    cidrs = _parse_cidrs(allowed_ip_cidr)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = f"CLOSE-{bond or 'ANY'}-{timezone.now():%Y}-" + "".join(secrets.choice(alphabet) for _ in range(8))
    token = EnrollmentToken.objects.create(
        label=(label or "").strip()[:120], bond_code=bond, code_hash=_hash(code), code_hint=code[:-6] + "…",
        max_seats=int(max_seats), allowed_ip_cidr=",".join(str(c) for c in cidrs),
        expires_at=timezone.now() + timedelta(hours=int(hours)), issued_by=actor if getattr(actor, "pk", None) else None,
    )
    audit.record(actor, "fleet.token_issued", target_type="enrollment_token", target_id=token.pk,
                 target_label=token.label or token.code_hint, bond_code=bond,
                 after={"seats": token.max_seats, "expires_at": token.expires_at.isoformat(),
                        "cidr": token.allowed_ip_cidr}, request=request)
    return token, code


def revoke_token(token, actor, request=None) -> None:
    from governance.services import audit

    token.revoked = True
    token.save(update_fields=["revoked"])
    audit.record(actor, "fleet.token_revoked", target_type="enrollment_token", target_id=token.pk,
                 target_label=token.label or token.code_hint, request=request)


def _parse_cidrs(raw: str):
    out = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                raise FleetError(f"{part} is not a valid IP range.")
    return out


def check_token(code: str, ip: str = "", for_update: bool = False):
    """Returns the usable token for `code` or raises FleetError (and logs the refusal)."""
    from governance.models import EnrollmentToken

    code = (code or "").strip().upper()
    qs = EnrollmentToken.objects.select_for_update() if for_update else EnrollmentToken.objects
    token = qs.filter(code_hash=_hash(code)).first() if TOKEN_RE.match(code) else None
    reason = ""
    if token is None:
        reason = "unknown token"
    elif token.revoked:
        reason = "revoked"
    elif token.expires_at < timezone.now():
        reason = "expired"
    elif token.used_seats >= token.max_seats:
        reason = "no seats left"
    elif token.allowed_ip_cidr and not _ip_allowed(ip, token.allowed_ip_cidr):
        reason = f"IP {ip or 'unknown'} not allowed"
    if reason:
        security_event("INSTALL_TOKEN_REFUSED", ip=ip, reason=f"{code[:18]}…: {reason}",
                       bond_code=getattr(token, "bond_code", ""))
        raise FleetError("This enrollment token is not valid (expired, revoked, used up or not allowed from here).")
    return token


def _ip_allowed(ip: str, cidrs: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _parse_cidrs(cidrs))


def is_token_code(code: str) -> bool:
    return bool(TOKEN_RE.match((code or "").strip().upper()))


def enroll_with_token(code: str, ip: str, name: str, device_info: Dict[str, Any]):
    """Creates a new install from a facility token (consumes one seat). Returns (install, enrollment_code)."""
    from governance.models import InstallRegistration
    from governance.services import policy_sync

    with transaction.atomic():
        token = check_token(code, ip=ip, for_update=True)
        token.used_seats += 1
        token.save(update_fields=["used_seats"])
        host = str((device_info or {}).get("hostname") or "").strip()
        label = (name or host or f"{token.bond_code or 'Laptop'} install").strip()[:120]
        inst, one_time = policy_sync.create_install(label, token.issued_by)
        InstallRegistration.objects.filter(pk=inst.pk).update(bond_code=token.bond_code, enrolled_token=token)
    return inst, one_time, token


# ---------------------------------------------------------------------------
# Token-gated installer
# ---------------------------------------------------------------------------
def installer_script(kind: str, code: str, central_url: str, request=None) -> str:
    """The install script with this token and the central URL baked in. Logs SCRIPT_DOWNLOADED."""
    from core.services.security_envelope import extract_client_ip

    ip = extract_client_ip(request) if request is not None else ""
    token = check_token(code, ip=ip)
    path = Path(settings.BASE_DIR) / ("install.ps1" if kind == "ps1" else "install.sh")
    script = path.read_text(encoding="utf-8")
    code = code.strip().upper()
    url = central_url.rstrip("/")
    if not re.match(r"^https?://[A-Za-z0-9.\-:]+$", url):
        raise FleetError("Bad central URL.")
    if kind == "ps1":
        inject = (f'$EnrollmentToken = "{code}"\n$ControlPlaneUrl = "{url}"\n')
        marker = '$ErrorActionPreference = "Stop"'
        script = script.replace(marker, inject + marker, 1) if marker in script else inject + script
    else:
        inject = f'CLOSE_ENROLL_TOKEN="{code}"\nCLOSE_CENTRAL_URL="{url}"\n'
        script = script.replace("set -e\n", "set -e\n" + inject, 1) if "set -e\n" in script else inject + script
    record_lifecycle(None, "SCRIPT_DOWNLOADED", token=token, ip=ip,
                     user_agent=request.META.get("HTTP_USER_AGENT", "") if request is not None else "",
                     metadata={"kind": kind})
    return script


def install_commands(code: str, central_url: str) -> Dict[str, str]:
    base = f"{central_url.rstrip('/')}/backoffice/api/v1/installs/script/{code}"
    return {
        "windows": f"Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process; irm {base}/install.ps1 | iex",
        "linux": f"curl -fsSL {base}/install.sh | bash",
    }


# ---------------------------------------------------------------------------
# Lifecycle ledger
# ---------------------------------------------------------------------------
def record_lifecycle(inst, event_type: str, token=None, ok: bool = True, ip: str = "", user_agent: str = "",
                     from_version: str = "", to_version: str = "", detail: str = "",
                     metadata: Optional[dict] = None, occurred_at=None) -> None:
    from governance.models import InstallLifecycleEvent

    if event_type not in InstallLifecycleEvent.Type.values:
        return
    InstallLifecycleEvent.objects.create(
        install=inst, token=token or getattr(inst, "enrolled_token", None), event_type=event_type, ok=bool(ok),
        client_ip=ip[:64], user_agent=user_agent[:300], from_version=str(from_version)[:32],
        to_version=str(to_version)[:32], detail=str(detail)[:2000], metadata=metadata or {},
        occurred_at=occurred_at or timezone.now(),
    )


def ingest_events(inst, events: list, ip: str = "") -> int:
    """(Central) Events an install sent with its check-in."""
    from django.utils.dateparse import parse_datetime

    from governance.services.telemetry_outbox import EVENT_TYPES

    n = 0
    for e in (events or [])[:200]:
        if not isinstance(e, dict) or e.get("event_type") not in EVENT_TYPES:
            continue
        when = parse_datetime(str(e.get("occurred_at") or "")) or timezone.now()
        record_lifecycle(inst, e["event_type"], ok=e.get("ok", True), ip=ip, from_version=e.get("from_version", ""),
                         to_version=e.get("to_version", ""), detail=e.get("detail", ""),
                         metadata=e.get("metadata") if isinstance(e.get("metadata"), dict) else {}, occurred_at=when)
        n += 1
    return n


# ---------------------------------------------------------------------------
# Release policy (minimum version, pinned update checksum)
# ---------------------------------------------------------------------------
def _version_tuple(v: str):
    nums = re.findall(r"\d+", str(v or ""))[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums)) if nums else (0, 0, 0)


def release_policy() -> Dict[str, Any]:
    from core.version import __version__
    from governance.services import flags

    return {
        "latest_version": __version__,
        "min_version": str(flags.get_flag("release.min_version", default="") or ""),
        "zip_sha256": str(flags.get_flag("release.zip_sha256", default="") or "").lower(),
        "auto_update_outdated": bool(flags.get_flag("release.auto_update_outdated", default=False)),
    }


def set_release_policy(actor, min_version: str = "", zip_sha256: str = "", auto_update: bool = False,
                       request=None) -> None:
    from governance.services import flags

    min_version = (min_version or "").strip()
    if min_version and not re.match(r"^\d+(\.\d+){0,2}$", min_version):
        raise FleetError("Minimum version must look like 1.3.0.")
    sha = (zip_sha256 or "").strip().lower()
    if sha and not re.match(r"^[0-9a-f]{64}$", sha):
        raise FleetError("The checksum must be a 64-character SHA-256 hex digest.")
    flags.set_flag("release.min_version", min_version, actor=actor, request=request)
    flags.set_flag("release.zip_sha256", sha, actor=actor, request=request)
    flags.set_flag("release.auto_update_outdated", bool(auto_update), actor=actor, request=request)


def is_outdated(app_version: str, policy: Optional[Dict[str, Any]] = None) -> bool:
    policy = policy or release_policy()
    return bool(policy["min_version"]) and _version_tuple(app_version) < _version_tuple(policy["min_version"])


def maybe_queue_auto_update(inst) -> None:
    """(Central) An outdated install gets one update command when auto-update is on."""
    from governance.models import InstallCommand

    policy = release_policy()
    if not policy["auto_update_outdated"] or not is_outdated(inst.app_version, policy):
        return
    if InstallCommand.objects.filter(install=inst, command="update.apply", status__in=["QUEUED", "SENT"]).exists():
        return
    since = timezone.now() - timedelta(hours=6)
    if InstallCommand.objects.filter(install=inst, command="update.apply", created_at__gte=since).exists():
        return
    InstallCommand.objects.create(install=inst, command="update.apply", args={"reason": "below minimum version"})


def verify_update_archive(zip_path: Path, expected_sha256: str) -> Tuple[bool, str]:
    """Edge: refuse a downloaded release archive whose SHA-256 doesn't match the pinned value."""
    if not expected_sha256:
        return True, "no checksum pinned"
    h = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected_sha256.lower():
        return False, f"Update archive checksum mismatch (expected {expected_sha256[:12]}…, got {actual[:12]}…)."
    return True, "checksum verified"


# ---------------------------------------------------------------------------
# Device anomalies (called by DeviceAdmissionMiddleware for admitted devices)
# ---------------------------------------------------------------------------
def check_device_anomalies(request, dev, ip: str) -> bool:
    """Returns False when the device must be held for review (MAC mismatch). IP jumps are logged only."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_loopback:
        return True

    if addr.is_private and dev.mac_address:
        from core.services.device_access_service import resolve_client_mac_address
        # ARP only (request=None): client-supplied MAC headers are never trusted.
        seen = resolve_client_mac_address(ip, None)
        if seen and seen.lower() != dev.mac_address.lower():
            security_event("ANOMALY_MAC_MISMATCH", request=request, user=dev.user, device_id=dev.device_id,
                           mac=seen, reason=f"expected {dev.mac_address}, network shows {seen}")
            from governance.services import device_admission
            device_admission.hold_for_review(dev, reason="MAC address mismatch")
            return False

    if not addr.is_private:
        key = f"close:fleet:lastip:{dev.device_id}"
        last = cache.get(key)
        cache.set(key, ip, 3600)
        if last and last != ip:
            try:
                last_net = ipaddress.ip_network(f"{last}/16", strict=False)
            except ValueError:
                last_net = None
            if last_net is not None and addr not in last_net:
                flag_key = f"close:fleet:ipjump:{dev.device_id}:{ip}"
                if cache.add(flag_key, 1, 3600):
                    security_event("ANOMALY_IP_JUMP", request=request, user=dev.user, device_id=dev.device_id,
                                   reason=f"{last} → {ip} within an hour")
    return True
