"""
Administrator 2-step sign-in (RFC 6238 TOTP, the 6-digit codes from Google Authenticator,
Microsoft Authenticator, Authy, ...). Dependency-free.

  start_enrollment(user)       -> (secret, otpauth_uri)   (unconfirmed until a code verifies)
  confirm_enrollment(user, c)  -> list of one-time recovery codes (shown once)
  verify(user, code)           -> True for a fresh code or an unused recovery code
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import List, Optional, Tuple
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

STEP = 30
DIGITS = 6
WINDOW = 1  # accept the previous / next 30-second step (clock drift)
ISSUER = "CLOSE Copilot"
SESSION_KEY = "close_admin_2fa_ok"


def _fernet():
    from cryptography.fernet import Fernet

    key = hashlib.sha256(f"close-admin-totp|{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def _unseal(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def _code_at(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_code(secret_b32: str, at: Optional[float] = None) -> str:
    return _code_at(secret_b32, int((at or time.time()) // STEP))


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}|{code.strip().upper()}".encode()).hexdigest()


def is_enrolled(user) -> bool:
    from governance.models import AdminTOTP

    return AdminTOTP.objects.filter(user=user, confirmed=True).exists()


def start_enrollment(user) -> Tuple[str, str]:
    """Returns the pending (unconfirmed) secret, creating one if needed, so reloading the setup
    page or mistyping a code doesn't force the administrator to scan again."""
    from governance.models import AdminTOTP

    rec = AdminTOTP.objects.filter(user=user, confirmed=False).first()
    if rec is not None:
        secret = _unseal(rec.secret_sealed)
    else:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        AdminTOTP.objects.update_or_create(
            user=user, defaults={"secret_sealed": _seal(secret), "confirmed": False, "last_used_step": 0,
                                 "recovery_hashes": [], "confirmed_at": None},
        )
    label = quote(f"{ISSUER}:{user.username}")
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"
    return secret, uri


def _check_code(rec, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return False
    secret = _unseal(rec.secret_sealed)
    now_step = int(time.time() // STEP)
    for step in range(now_step - WINDOW, now_step + WINDOW + 1):
        if step <= rec.last_used_step:
            continue  # replay protection: a code can be used once
        if hmac.compare_digest(_code_at(secret, step), code):
            rec.last_used_step = step
            rec.save(update_fields=["last_used_step"])
            return True
    return False


def confirm_enrollment(user, code: str) -> Optional[List[str]]:
    from governance.models import AdminTOTP

    rec = AdminTOTP.objects.filter(user=user, confirmed=False).first()
    if rec is None or not _check_code(rec, code):
        return None
    recovery = ["-".join(secrets.token_hex(2).upper() for _ in range(2)) for _ in range(8)]
    rec.recovery_hashes = [_hash_recovery(c) for c in recovery]
    rec.confirmed = True
    rec.confirmed_at = timezone.now()
    rec.save()
    return recovery


def verify(user, code: str) -> bool:
    from governance.models import AdminTOTP

    rec = AdminTOTP.objects.filter(user=user, confirmed=True).first()
    if rec is None:
        return False
    if _check_code(rec, code):
        return True
    hashed = _hash_recovery(code or "")
    if hashed in (rec.recovery_hashes or []):
        rec.recovery_hashes = [h for h in rec.recovery_hashes if h != hashed]
        rec.save(update_fields=["recovery_hashes"])
        return True
    return False


def reset(user) -> None:
    from governance.models import AdminTOTP

    AdminTOTP.objects.filter(user=user).delete()


def qr_svg(uri: str) -> str:
    """Inline SVG QR code for the authenticator app (no files written)."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
