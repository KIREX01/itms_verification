"""
Central policy sync: the central server (DEPLOYMENT_ROLE=central) is the source of truth for
accounts, roles, company emails, burned identities and kill switches. Installs ("edge") enroll
once, then check in every minute and apply signed policy snapshots.

Wire format: JSON bodies signed with HMAC-SHA256 over "<timestamp>.<body>" using the install
secret; headers X-Close-Install, X-Close-Timestamp, X-Close-Signature. Messages older than
5 minutes are rejected (replay protection).

Central side:  create_install, enroll, handle_heartbeat, build_snapshot, bump_policy_version
Edge side:     enroll_with_central, heartbeat_once, apply_snapshot, is_policy_stale, PolicySyncDaemon
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_SKEW_SECONDS = 300
HEARTBEAT_SECONDS = 60


class PolicySyncError(Exception):
    pass


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------
def sign(secret: str, body: bytes, ts: Optional[str] = None) -> Tuple[str, str]:
    ts = ts or str(int(time.time()))
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return ts, mac


def verify(secret: str, body: bytes, ts: str, signature: str) -> bool:
    try:
        if abs(int(time.time()) - int(ts)) > MAX_SKEW_SECONDS:
            return False
    except (TypeError, ValueError):
        return False
    _, expected = sign(secret, body, ts)
    return hmac.compare_digest(expected, signature or "")


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


# ---------------------------------------------------------------------------
# Policy version
# ---------------------------------------------------------------------------
def bump_policy_version() -> None:
    from governance.models import PolicyState

    try:
        if not PolicyState.objects.filter(pk=1).update(version=F("version") + 1):
            PolicyState.objects.get_or_create(pk=1, defaults={"version": 2})
    except Exception as exc:  # table may not exist during migrations
        logger.debug("Policy version bump skipped: %s", exc)


def policy_version() -> int:
    from governance.models import PolicyState

    return PolicyState.current().version


# ---------------------------------------------------------------------------
# Central: installs
# ---------------------------------------------------------------------------
def create_install(name: str, actor, request=None) -> Tuple[Any, str]:
    """Creates an install and returns (install, one-time enrollment code)."""
    from governance.models import InstallRegistration
    from governance.services import audit

    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))  # e.g. 3F9A-11C2-7B0D
    inst = InstallRegistration.objects.create(
        name=name.strip() or "Install",
        install_id=uuid.uuid4().hex,
        enrollment_code_hash=_hash(code),
        enrollment_expires_at=timezone.now() + timedelta(hours=24),
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    audit.record(actor, "install.create", target_type="install", target_id=inst.install_id,
                 target_label=inst.name, request=request)
    return inst, code


@transaction.atomic
def enroll(code: str, app_version: str = "", ip: str = "") -> Dict[str, Any]:
    from governance.models import InstallRegistration
    from governance.services import audit

    inst = (InstallRegistration.objects.select_for_update()
            .filter(enrollment_code_hash=_hash((code or "").strip().upper()),
                    status=InstallRegistration.Status.PENDING).first())
    if inst is None or (inst.enrollment_expires_at and inst.enrollment_expires_at < timezone.now()):
        raise PolicySyncError("Invalid or expired enrollment code.")
    inst.secret = secrets.token_urlsafe(48)
    inst.status = InstallRegistration.Status.ACTIVE
    inst.enrollment_code_hash = ""
    inst.enrolled_at = timezone.now()
    inst.last_seen_at = timezone.now()
    inst.last_ip = ip
    inst.app_version = app_version[:32]
    inst.save()
    audit.record("system", "install.enrolled", target_type="install", target_id=inst.install_id,
                 target_label=inst.name, after={"ip": ip, "version": app_version}, actor_label=inst.name)
    return {"install_id": inst.install_id, "secret": inst.secret, "name": inst.name}


def build_snapshot() -> Dict[str, Any]:
    """Everything an install needs to enforce central policy offline."""
    from django.contrib.auth.models import User
    from governance.models import (
        AccountState, BondMembership, BurnedIdentity, CompanyEmailLink, FeatureFlag, ItmsWarehouseAccess,
    )

    users = []
    states = {s.user_id: s for s in AccountState.objects.all()}
    for u in User.objects.all():
        st = states.get(u.pk)
        users.append({
            "username": u.username, "email": u.email, "first_name": u.first_name, "last_name": u.last_name,
            "password": u.password, "is_active": u.is_active, "is_superuser": u.is_superuser,
            "is_staff": u.is_staff,
            "status": st.status if st else "ACTIVE",
            "security_stamp": st.security_stamp if st else "",
            "reason": st.reason if st else "",
            "grace_until": st.company_email_grace_until.isoformat() if st and st.company_email_grace_until else None,
        })
    return {
        "policy_version": policy_version(),
        "generated_at": timezone.now().isoformat(),
        "users": users,
        "memberships": [
            {"username": m.user.username, "bond_code": m.bond_code, "role": m.role, "status": m.status,
             "status_reason": m.status_reason}
            for m in BondMembership.objects.select_related("user")
        ],
        "company_emails": [
            {"username": c.user.username, "email": c.email, "status": c.status, "verified_via": c.verified_via}
            for c in CompanyEmailLink.objects.select_related("user")
        ],
        "itms_access": [
            {"email": a.company_email, "bond_code": a.bond_code, "active": a.active}
            for a in ItmsWarehouseAccess.objects.all()
        ],
        "burned": list(BurnedIdentity.objects.values_list("email", flat=True)),
        "flags": [{"key": f.key, "bond_code": f.bond_code, "value": f.value} for f in FeatureFlag.objects.all()],
    }


def handle_heartbeat(install_id: str, body: bytes, ts: str, signature: str, ip: str = "") -> Dict[str, Any]:
    from governance.models import InstallRegistration

    inst = InstallRegistration.objects.filter(install_id=install_id).first()
    if inst is None or inst.status != InstallRegistration.Status.ACTIVE or not inst.secret:
        raise PolicySyncError("Unknown or revoked install.")
    if not verify(inst.secret, body, ts, signature):
        raise PolicySyncError("Bad signature.")
    payload = json.loads(body.decode() or "{}")
    current = policy_version()
    inst.last_seen_at = timezone.now()
    inst.last_ip = ip
    inst.app_version = str(payload.get("app_version", ""))[:32]
    inst.policy_version_acked = int(payload.get("policy_version") or 0)
    inst.health = payload.get("health") or {}
    inst.save(update_fields=["last_seen_at", "last_ip", "app_version", "policy_version_acked", "health"])
    response: Dict[str, Any] = {
        "policy_version": current,
        "lock": {"locked": inst.locked, "reason": inst.lock_reason},
        "server_time": timezone.now().isoformat(),
    }
    if inst.policy_version_acked != current:
        response["snapshot"] = build_snapshot()
    return response


# ---------------------------------------------------------------------------
# Edge: enrollment state
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    from core.services.secure_storage import get_secure_dir

    return Path(get_secure_dir()) / "central_enrollment.json"


def load_enrollment() -> Dict[str, Any]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_enrollment(data: Dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        from core.services import platform_service
        platform_service.safe_chmod(path, 0o600)
    except Exception:
        pass


def is_enrolled() -> bool:
    data = load_enrollment()
    return bool(data.get("install_id") and data.get("secret") and data.get("central_url"))


def is_policy_stale() -> bool:
    """True when this install is enrolled but hasn't reached the central server within the grace period."""
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central" or not is_enrolled():
        return False
    from governance.services.flags import get_flag

    data = load_enrollment()
    last = data.get("last_sync_ok_at")
    if not last:
        return True
    hours = int(get_flag("sync.policy_offline_grace_hours", default=24) or 24)
    try:
        from datetime import datetime
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return timezone.now() - last_dt > timedelta(hours=hours)


def _http_post(url: str, body: bytes, headers: Dict[str, str], timeout: float = 15.0) -> Tuple[int, bytes, Dict[str, str]]:
    import requests

    resp = requests.post(url, data=body, headers=headers, timeout=timeout)
    return resp.status_code, resp.content, {k.lower(): v for k, v in resp.headers.items()}


def enroll_with_central(central_url: str, code: str, name: str = "") -> Dict[str, Any]:
    from core.version import __version__

    central_url = central_url.rstrip("/")
    body = json.dumps({"code": code, "name": name, "app_version": __version__}).encode()
    status, content, _ = _http_post(f"{central_url}/backoffice/api/v1/installs/enroll/", body,
                                    {"Content-Type": "application/json"})
    data = json.loads(content.decode() or "{}")
    if status != 200 or not data.get("install_id"):
        raise PolicySyncError(data.get("error") or f"Enrollment failed (HTTP {status}).")
    save_enrollment({"central_url": central_url, "install_id": data["install_id"], "secret": data["secret"],
                     "name": data.get("name", name), "policy_version": 0, "last_sync_ok_at": None})
    heartbeat_once()
    return load_enrollment()


def _health() -> Dict[str, Any]:
    health: Dict[str, Any] = {}
    try:
        from core.models import QualityFlag, VehicleInstallationPair
        health["pending_review"] = VehicleInstallationPair.objects.filter(
            verification_status__in=["PENDING_REVIEW", "CONFLICT"]).count()
        health["approved_waiting"] = VehicleInstallationPair.objects.filter(verification_status="APPROVED").count()
        health["failed"] = VehicleInstallationPair.objects.filter(verification_status="FAILED").count()
        health["outbox"] = VehicleInstallationPair.objects.filter(verification_status="OFFLINE_OUTBOX").count()
        health["open_flags"] = QualityFlag.objects.filter(status="OPEN").count()
    except Exception:
        pass
    return health


def heartbeat_once() -> Dict[str, Any]:
    """One check-in. Applies a snapshot / lock when the server sends one. Raises on failure."""
    from core.version import __version__

    state = load_enrollment()
    if not state.get("install_id"):
        raise PolicySyncError("This install is not enrolled.")
    body = json.dumps({"app_version": __version__, "policy_version": state.get("policy_version", 0),
                       "health": _health()}).encode()
    ts, sig = sign(state["secret"], body)
    status, content, headers = _http_post(
        f"{state['central_url']}/backoffice/api/v1/installs/heartbeat/", body,
        {"Content-Type": "application/json", "X-Close-Install": state["install_id"],
         "X-Close-Timestamp": ts, "X-Close-Signature": sig},
    )
    if status != 200:
        raise PolicySyncError(f"Central server refused the check-in (HTTP {status}).")
    if not verify(state["secret"], content, headers.get("x-close-timestamp", ""), headers.get("x-close-signature", "")):
        raise PolicySyncError("Central response signature is invalid; ignoring it.")
    data = json.loads(content.decode() or "{}")
    if "snapshot" in data:
        apply_snapshot(data["snapshot"])
        state["policy_version"] = data["snapshot"].get("policy_version", data.get("policy_version", 0))
    _apply_lock(data.get("lock") or {}, state)
    state["last_sync_ok_at"] = timezone.now().isoformat()
    save_enrollment(state)
    return data


def _apply_lock(lock: Dict[str, Any], state: Dict[str, Any]) -> None:
    from core.services import config_service

    locked = bool(lock.get("locked"))
    if locked and not state.get("locked"):
        config_service.set_maintenance_mode(enabled=True, title="Locked by administrator",
                                            message=lock.get("reason") or "This install has been locked centrally.")
    elif not locked and state.get("locked"):
        config_service.set_maintenance_mode(enabled=False)
    state["locked"] = locked


@transaction.atomic
def apply_snapshot(snapshot: Dict[str, Any]) -> Dict[str, int]:
    """Edge: make local governance match central. Local-only superusers are left alone (break-glass)."""
    from django.contrib.auth.models import User
    from governance.models import (
        AccountState, BondMembership, BurnedIdentity, CompanyEmailLink, FeatureFlag, ItmsWarehouseAccess,
    )
    from governance.services import accounts, switches

    counts = {"users": 0, "killed": 0}
    by_username: Dict[str, User] = {}
    for row in snapshot.get("users", []):
        user, _ = User.objects.get_or_create(username=row["username"])
        user.email = row.get("email", "")
        user.first_name = row.get("first_name", "")
        user.last_name = row.get("last_name", "")
        user.password = row.get("password") or user.password
        user.is_active = bool(row.get("is_active"))
        user.is_superuser = bool(row.get("is_superuser"))
        user.is_staff = bool(row.get("is_staff"))
        user.save()
        state = accounts.get_state(user)
        stamp_changed = row.get("security_stamp") and row["security_stamp"] != state.security_stamp
        state.status = row.get("status") or "ACTIVE"
        state.reason = row.get("reason", "")
        if row.get("security_stamp"):
            state.security_stamp = row["security_stamp"]
        grace = row.get("grace_until")
        if grace:
            from datetime import datetime
            state.company_email_grace_until = datetime.fromisoformat(grace)
        state.save()
        if stamp_changed or state.status != "ACTIVE" or not user.is_active:
            counts["killed"] += accounts.kill_sessions(user)
        by_username[user.username] = user
        counts["users"] += 1

    synced_ids = [u.pk for u in by_username.values()]
    BondMembership.objects.filter(user_id__in=synced_ids).delete()
    BondMembership.objects.bulk_create([
        BondMembership(user=by_username[m["username"]], bond_code=m["bond_code"], role=m["role"],
                       status=m["status"], status_reason=m.get("status_reason", ""))
        for m in snapshot.get("memberships", []) if m["username"] in by_username
    ])
    CompanyEmailLink.objects.filter(user_id__in=synced_ids).delete()
    CompanyEmailLink.objects.filter(email__in=[c["email"] for c in snapshot.get("company_emails", [])]).delete()
    CompanyEmailLink.objects.bulk_create([
        CompanyEmailLink(user=by_username[c["username"]], email=c["email"], status=c["status"],
                         verified_via=c.get("verified_via", ""), verified_at=timezone.now())
        for c in snapshot.get("company_emails", []) if c["username"] in by_username
    ])
    for a in snapshot.get("itms_access", []):
        ItmsWarehouseAccess.objects.update_or_create(
            company_email=a["email"], bond_code=a["bond_code"], defaults={"active": a["active"]})
    for email in snapshot.get("burned", []):
        BurnedIdentity.objects.get_or_create(email=email)
    for f in snapshot.get("flags", []):
        FeatureFlag.objects.update_or_create(key=f["key"], bond_code=f.get("bond_code", ""),
                                             defaults={"value": f["value"]})
        if f["key"].startswith("switch.") and not f.get("bond_code"):
            key = f["key"][len("switch."):]
            if key in switches.SWITCHES:
                try:
                    switches.apply_locally(key, bool(f["value"]), actor_label="central")
                except Exception as exc:
                    logger.warning("Could not apply central switch %s: %s", key, exc)
    return counts


class PolicySyncDaemon(threading.Thread):
    """Background check-in loop for enrolled installs."""

    def __init__(self, interval: int = HEARTBEAT_SECONDS):
        super().__init__(name="close-policy-sync", daemon=True)
        self.interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            if is_enrolled():
                try:
                    heartbeat_once()
                except Exception as exc:
                    logger.warning("Central policy check-in failed: %s", exc)
            self._stop.wait(self.interval)


_daemon: Optional[PolicySyncDaemon] = None


def start_policy_sync_daemon() -> None:
    """Idempotently starts the check-in loop on edge installs."""
    global _daemon
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central" or getattr(settings, "IS_TESTING", False):
        return
    if _daemon is None or not _daemon.is_alive():
        _daemon = PolicySyncDaemon()
        _daemon.start()
