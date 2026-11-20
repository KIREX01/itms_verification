"""
Sign-in guard: records every sign-in attempt and stops password guessing.

  * 5 failed attempts for one username within 15 minutes -> that username is locked for 15 minutes.
  * 20 failed attempts from one IP within 15 minutes -> that IP is locked for 15 minutes.
Applies to every sign-in path (web, TUI, Django admin) through governance.auth_backend.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, Tuple

from django.utils import timezone

USER_FAIL_LIMIT = 5
IP_FAIL_LIMIT = 20
WINDOW = timedelta(minutes=15)


def _client(request) -> Tuple[str, str, str]:
    if request is None:
        return "", "", ""
    try:
        from core.services.security_envelope import extract_client_ip
        ip = extract_client_ip(request)
    except Exception:
        ip = request.META.get("REMOTE_ADDR", "") if hasattr(request, "META") else ""
    device = ""
    try:
        from core.services.device_access_service import DEVICE_TRUST_COOKIE_NAME, verify_device_trust_token
        verified = verify_device_trust_token(request.COOKIES.get(DEVICE_TRUST_COOKIE_NAME, ""))
        device = (verified or {}).get("device_id", "")
    except Exception:
        pass
    ua = (request.META.get("HTTP_USER_AGENT", "") if hasattr(request, "META") else "")[:300]
    return ip, device, ua


def lock_status(username: str, request=None) -> Tuple[bool, int]:
    """(locked, minutes_left) for this username / client IP. The lock runs for WINDOW after the
    latest failed attempt, so continued guessing keeps it locked."""
    from governance.models import LoginAttempt

    now = timezone.now()
    username = (username or "").strip().lower()
    ip, _, _ = _client(request)
    failures = (LoginAttempt.objects.filter(success=False, created_at__gte=now - WINDOW)
                .exclude(reason="CLEARED_BY_ADMIN"))
    scope = None
    if username and failures.filter(username=username).count() >= USER_FAIL_LIMIT:
        scope = failures.filter(username=username)
    elif ip and failures.filter(ip_address=ip).count() >= IP_FAIL_LIMIT:
        scope = failures.filter(ip_address=ip)
    if scope is None:
        return False, 0
    latest = scope.order_by("-created_at").values_list("created_at", flat=True).first()
    minutes = max(1, int(((latest + WINDOW) - now).total_seconds() // 60) + 1)
    return True, minutes


def record(username: str, success: bool, request=None, user=None, reason: str = "", channel: Optional[str] = None):
    from governance.models import LoginAttempt

    ip, device, ua = _client(request)
    try:
        return LoginAttempt.objects.create(
            username=(username or "").strip().lower()[:150],
            user=user if getattr(user, "pk", None) else None,
            success=success,
            reason=reason[:64],
            ip_address=ip[:64],
            device_id=device[:96],
            user_agent=ua,
            channel=channel or ("web" if request is not None else "tui"),
        )
    except Exception:
        return None


def clear_failures(username: str) -> int:
    """Administrator unlock: recent failures stop counting (they stay in the history)."""
    from governance.models import LoginAttempt

    return (LoginAttempt.objects.filter(username=(username or "").strip().lower(), success=False,
                                        created_at__gte=timezone.now() - WINDOW)
            .exclude(reason="CLEARED_BY_ADMIN").update(reason="CLEARED_BY_ADMIN"))
