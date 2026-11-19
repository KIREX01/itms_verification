"""
Install guard: an install only runs on the machine it was enrolled on.

  machine_fingerprint()  stable hash of this computer's OS machine ID (MachineGuid / machine-id / IOPlatformUUID)
  install_state()        CENTRAL | OPEN | UNENROLLED | ENROLLED | MOVED | REVOKED
  is_locked()            True for UNENROLLED / MOVED / REVOKED (edge installs only)
  quarantine_moved_copy()  wipe what a copied folder must never reuse (ITMS cookies, sessions, remember-me)

A copied folder carries the database, .env and secure/ (including the stored ITMS portal session).
On another machine the fingerprint no longer matches the one recorded at enrollment, so the copy
locks itself, wipes those secrets, and - if it reaches the central server - is reported as a clone.
The install's sync secret is also encrypted with a key derived from the fingerprint, so a copy
cannot sign check-ins even if the enrollment file is edited.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import platform
import subprocess
import uuid
from functools import lru_cache
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

CENTRAL, OPEN, UNENROLLED, ENROLLED, MOVED, REVOKED = (
    "CENTRAL", "OPEN", "UNENROLLED", "ENROLLED", "MOVED", "REVOKED",
)
LOCKED_STATES = (UNENROLLED, MOVED, REVOKED)


# ---------------------------------------------------------------------------
# Machine fingerprint
# ---------------------------------------------------------------------------
def _raw_machine_id() -> str:
    override = os.environ.get("CLOSE_MACHINE_ID", "").strip()
    if override:
        return override
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return str(winreg.QueryValueEx(key, "MachineGuid")[0])
        if system == "Darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split("=")[-1].strip().strip('"')
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    value = fh.read().strip()
                    if value:
                        return value
    except Exception as exc:
        logger.warning("Could not read OS machine id: %s", exc)
    return f"mac-{uuid.getnode():012x}"


@lru_cache(maxsize=1)
def _cached_fingerprint(raw: str) -> str:
    return hashlib.sha256(f"close-copilot-install|{raw}".encode()).hexdigest()


def machine_fingerprint() -> str:
    return _cached_fingerprint(_raw_machine_id())


def short_fingerprint(fp: str) -> str:
    return (fp or "")[:12]


# ---------------------------------------------------------------------------
# Machine-bound encryption for the install secret
# ---------------------------------------------------------------------------
def _fernet(install_id: str, fingerprint: Optional[str] = None):
    from cryptography.fernet import Fernet

    material = hashlib.sha256(f"{fingerprint or machine_fingerprint()}|{install_id}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def seal_secret(secret: str, install_id: str) -> str:
    return _fernet(install_id).encrypt(secret.encode()).decode()


def unseal_secret(token: str, install_id: str) -> Optional[str]:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet(install_id).decrypt((token or "").encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def enrollment_required() -> bool:
    return bool(getattr(settings, "CLOSE_REQUIRE_ENROLLMENT", True))


def install_state() -> str:
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central":
        return CENTRAL
    from governance.services import policy_sync

    data = policy_sync.load_enrollment(decrypt=False)
    if data.get("revoked"):
        return REVOKED
    if not data.get("install_id"):
        return UNENROLLED if enrollment_required() else OPEN
    if data.get("machine_fingerprint") and data["machine_fingerprint"] != machine_fingerprint():
        return MOVED
    return ENROLLED


def is_locked() -> bool:
    # UNENROLLED is only reported when enrollment is required; MOVED / REVOKED always lock.
    return install_state() in LOCKED_STATES


def lock_message(state: Optional[str] = None) -> str:
    state = state or install_state()
    if state == UNENROLLED:
        return ("This CLOSE Copilot install hasn't been activated. The administrator must enroll it with the "
                "central server before anyone can use it.")
    if state == MOVED:
        return ("This copy of CLOSE Copilot was moved from another computer, so it has been locked and its stored "
                "ITMS sessions were erased. Install CLOSE Copilot properly on this computer and ask the "
                "administrator for an enrollment code.")
    if state == REVOKED:
        return "This install has been revoked by the administrator."
    return ""


def quarantine_moved_copy() -> dict:
    """Runs once when a moved copy is detected: erase everything that must not travel with a copy."""
    from django.contrib.sessions.models import Session
    from governance.services import audit, policy_sync

    data = policy_sync.load_enrollment(decrypt=False)
    if data.get("quarantined_fingerprint") == machine_fingerprint():
        return {"already": True}
    wiped = {"itms_sessions": 0, "auth_files": 0, "sessions": 0}
    try:
        from core.models import OperatorITMSSession
        wiped["itms_sessions"] = OperatorITMSSession.objects.update(cookies={}, csrf_token="", is_authenticated=False)
    except Exception as exc:
        logger.warning("Could not wipe ITMS sessions: %s", exc)
    try:
        from core.services.secure_storage import get_secure_auth_dir
        for f in get_secure_auth_dir().glob("*"):
            if f.is_file():
                f.unlink()
                wiped["auth_files"] += 1
    except Exception as exc:
        logger.warning("Could not wipe secure auth files: %s", exc)
    try:
        wiped["sessions"], _ = Session.objects.all().delete()
    except Exception as exc:
        logger.warning("Could not clear sessions: %s", exc)
    data["quarantined_fingerprint"] = machine_fingerprint()
    policy_sync.save_enrollment(data, encrypt=False)
    audit.record("system", "install.moved_copy_quarantined", target_type="install",
                 target_id=data.get("install_id", ""), target_label=data.get("name", ""),
                 after={"new_machine": short_fingerprint(machine_fingerprint()),
                        "enrolled_machine": short_fingerprint(data.get("machine_fingerprint", "")), **wiped},
                 actor_label="install-guard")
    logger.error("Moved copy detected: install locked and secrets wiped (%s).", wiped)
    return wiped
