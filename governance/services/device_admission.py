"""
Device admission: every device must be approved by the administrator before it can open CLOSE Copilot.

A device's identity is the signed device key cookie (itms_device_trust): an HMAC-signed device id
that the server issues. Nothing a client sends in plain text (IP, headers, query parameters) can
make a device count as approved.

  admitted_device(request)        -> the APPROVED AuthorizedDevice for this browser, or None
  current_device(request)         -> this browser's device record (any status), or None
  request_access(request, ...)    -> creates / updates a PENDING request; returns (device, cookie_value)
  approve(device, actor, user)    /  block(device, actor, reason)
  device_code(device_id)          -> short code shown on the blocked page (e.g. 7KQ2-M9XD)
"""
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
import hmac
import logging
import secrets
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)

REQUEST_RATE_PER_IP_PER_HOUR = 10


def device_code(device_id: str) -> str:
    raw = base64.b32encode(hashlib.sha256((device_id or "").encode()).digest()).decode()
    return f"{raw[:4]}-{raw[4:8]}"


def _verified_device_id(request) -> str:
    from core.services.device_access_service import DEVICE_TRUST_COOKIE_NAME, verify_device_trust_token

    token = request.COOKIES.get(DEVICE_TRUST_COOKIE_NAME, "")
    verified = verify_device_trust_token(token) if token else None
    return (verified or {}).get("device_id", "")


def current_device(request):
    from core.models import AuthorizedDevice

    device_id = _verified_device_id(request)
    if not device_id:
        return None
    return AuthorizedDevice.objects.filter(device_id=device_id).first()


def admitted_device(request):
    from core.models import AuthorizedDevice

    dev = current_device(request)
    if dev is None or dev.status != AuthorizedDevice.Status.APPROVED:
        return None
    if dev.expires_at and dev.expires_at <= timezone.now():
        return None
    return dev


def find_by_code(code: str):
    """Administrator / CLI lookup of a device by the code shown on its blocked page."""
    from core.models import AuthorizedDevice

    code = (code or "").strip().upper()
    for dev in AuthorizedDevice.objects.exclude(device_id="").only("id", "device_id"):
        if device_code(dev.device_id) == code:
            return AuthorizedDevice.objects.get(pk=dev.pk)
    return None


def _client_ip(request) -> str:
    from core.services.security_envelope import extract_client_ip
    return extract_client_ip(request)


def request_access(request, name: str = "", email: str = "", note: str = "",
                   client_report: Optional[Dict[str, Any]] = None, user=None,
                   install_id: str = "", notify_admins: bool = True) -> Tuple[Any, str]:
    """
    Records (or refreshes) this browser's request for access. Returns (device, cookie_value):
    the caller must set the device key cookie so the same browser keeps its identity.
    """
    from django.core.cache import cache

    from core.models import AuthorizedDevice
    from core.services import device_access_service, device_info_service

    ip = _client_ip(request)
    dev = current_device(request)
    if dev is None:
        throttle_key = f"close:device_req:{ip}"
        count = cache.get(throttle_key, 0) or 0
        if count >= REQUEST_RATE_PER_IP_PER_HOUR:
            raise ValueError("Too many device requests from this network. Try again later.")
        cache.set(throttle_key, count + 1, 3600)
        ua = request.META.get("HTTP_USER_AGENT", "")
        is_mobile = device_info_service.parse_user_agent(ua)["is_mobile"] == "1"
        dev = AuthorizedDevice.objects.create(
            device_id=f"DEV-{secrets.token_hex(12).upper()}",
            device_type=AuthorizedDevice.DeviceType.MOBILE if is_mobile else AuthorizedDevice.DeviceType.DESKTOP,
            device_name=device_access_service._derive_friendly_device_name(ua, is_mobile, ip),
            status=AuthorizedDevice.Status.PENDING,
            ip_address=ip,
            user_agent=ua[:500],
        )
    if dev.status in (AuthorizedDevice.Status.BLOCKED, AuthorizedDevice.Status.REVOKED):
        return dev, device_access_service.sign_device_trust_token(dev.device_id, dev.request_email or "device")
    dev.request_name = (name or dev.request_name or "")[:150]
    dev.request_email = (email or dev.request_email or "")[:254].lower()
    dev.request_note = (note or dev.request_note or "")[:300]
    dev.requested_at = timezone.now()
    dev.ip_address = ip
    dev.install_id = install_id or dev.install_id
    if user is not None and getattr(user, "pk", None) and dev.user_id is None:
        dev.user = user
    if dev.status != AuthorizedDevice.Status.APPROVED:
        dev.status = AuthorizedDevice.Status.PENDING
    dev.save()
    try:
        device_info_service.enrich_device(dev, request, force=True)
        if client_report:
            device_info_service.record_client_report(dev, client_report)
    except Exception as exc:
        logger.debug("Device description skipped: %s", exc)
    if notify_admins:
        _notify_admins(dev, request)
    cookie = device_access_service.sign_device_trust_token(dev.device_id, dev.request_email or "device")
    from governance.services.fleet import security_event
    security_event("DEVICE_REQUESTED", request=request, user=user, device_id=dev.device_id,
                   reason=(f"{name} <{email}>" if email else name)[:200])
    return dev, cookie


def _notify_admins(dev, request) -> None:
    from django.core.cache import cache

    if cache.get(f"close:device_req_alert:{dev.pk}"):
        return  # one alert per device per hour
    cache.set(f"close:device_req_alert:{dev.pk}", 1, 3600)
    try:
        from django.contrib.auth.models import User
        from core.services import email_service

        admins = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email="")
                      .values_list("email", flat=True))
        if not admins:
            return
        review_url = request.build_absolute_uri("/backoffice/devices/?status=PENDING") if request else ""
        email_service.send_access_request_admin_alert(
            admin_emails=admins,
            requester_email=dev.request_email or "(not given)",
            requester_name=f"Device access: {dev.request_name or 'unknown'} · {dev.os_name} {dev.os_version} "
                           f"{dev.device_model} · {dev.browser} · code {device_code(dev.device_id)}",
            client_ip=dev.ip_address, mac_address=dev.mac_address or "", device_name=dev.device_name,
            activation_code="", approve_url=review_url,
        )
    except Exception as exc:
        logger.warning("Could not send device request alert: %s", exc)


def approve(dev, actor, user=None, request=None):
    from core.models import AuthorizedDevice
    from governance.services import audit

    before = {"status": dev.status}
    dev.status = AuthorizedDevice.Status.APPROVED
    dev.block_reason = ""
    dev.approved_by = actor if getattr(actor, "pk", None) else None
    dev.approved_at = timezone.now()
    if user is not None:
        dev.user = user
        dev.authorized_email = (user.email or "").lower()
    dev.save()
    from governance.services.fleet import security_event
    security_event("DEVICE_APPROVED", request=request, user=dev.user, device_id=dev.device_id,
                   reason=f"by {getattr(actor, 'username', actor)}")
    if actor not in ("email-code", "invite-signup") and (dev.request_email or dev.authorized_email):
        try:
            from core.services import email_service
            email_service.send_device_approved_email(dev.request_email or dev.authorized_email,
                                                     name=dev.request_name or "", device_label=dev.device_name)
        except Exception as exc:
            logger.debug("Device approved email skipped: %s", exc)
    audit.record(actor, "device.approve", target_type="device", target_id=dev.device_id,
                 target_label=f"{dev.device_name} ({device_code(dev.device_id)})", before=before,
                 after={"status": dev.status, "user": getattr(dev.user, "username", "")}, request=request)
    return dev


def block(dev, actor, reason: str = "", request=None):
    from core.models import AuthorizedDevice
    from governance.services import audit

    before = {"status": dev.status}
    dev.status = AuthorizedDevice.Status.BLOCKED
    dev.block_reason = (reason or "Blocked by administrator")[:255]
    dev.save()
    killed = 0
    try:
        from governance.models import UserSessionIndex
        from django.contrib.sessions.models import Session
        keys = list(UserSessionIndex.objects.filter(device_id=dev.device_id).values_list("session_key", flat=True))
        killed, _ = Session.objects.filter(session_key__in=keys).delete()
        UserSessionIndex.objects.filter(device_id=dev.device_id).delete()
    except Exception:
        pass
    from governance.services.fleet import security_event
    security_event("DEVICE_BLOCKED", request=request, user=dev.user, device_id=dev.device_id,
                   reason=(reason or "")[:200])
    audit.record(actor, "device.block", target_type="device", target_id=dev.device_id,
                 target_label=f"{dev.device_name} ({device_code(dev.device_id)})", before=before,
                 after={"status": dev.status, "sessions_killed": killed}, reason=reason, request=request)
    return dev


def hold_for_review(dev, reason: str = ""):
    """Anomaly: send an approved device back to the request queue and end its sessions."""
    from core.models import AuthorizedDevice
    from governance.services import audit

    dev.status = AuthorizedDevice.Status.PENDING
    dev.block_reason = (f"Held for review: {reason}" if reason else "Held for review")[:255]
    dev.save(update_fields=["status", "block_reason"])
    try:
        from django.contrib.sessions.models import Session
        from governance.models import UserSessionIndex
        keys = list(UserSessionIndex.objects.filter(device_id=dev.device_id).values_list("session_key", flat=True))
        Session.objects.filter(session_key__in=keys).delete()
        UserSessionIndex.objects.filter(device_id=dev.device_id).delete()
    except Exception:
        pass
    audit.record("system", "device.held_for_review", target_type="device", target_id=dev.device_id,
                 target_label=f"{dev.device_name} ({device_code(dev.device_id)})", reason=reason,
                 actor_label="anomaly-detection")
    return dev



# ---------------------------------------------------------------------------
# Self-activation by emailed code (people who already have access)
# ---------------------------------------------------------------------------
CODE_TTL_MINUTES = 15
CODE_MAX_ATTEMPTS = 5
CODE_SENDS_PER_DEVICE_15MIN = 3
CODE_SENDS_PER_IP_HOUR = 20


def email_codes_available() -> bool:
    from django.conf import settings
    from core.services import email_service

    return bool(email_service.get_resend_api_key()) or bool(getattr(settings, "IS_TESTING", False))


def eligible_identity(email: str):
    """
    (user, invite) for an email that already has access: an active account (login or attached company email)
    or a live access grant waiting for sign-up. (None, None) otherwise.
    """
    from django.contrib.auth.models import User
    from governance.models import AccessRequest, AccountState, CompanyEmailLink

    email = (email or "").strip().lower()
    if "@" not in email:
        return None, None

    def _ok(user):
        if user is None or not user.is_active:
            return None
        status = AccountState.objects.filter(user=user).values_list("status", flat=True).first() or "ACTIVE"
        return user if status == "ACTIVE" else None

    users = list(User.objects.filter(email__iexact=email, is_active=True)[:2])
    if len(users) == 1 and _ok(users[0]):
        return users[0], None
    link = CompanyEmailLink.objects.select_related("user").filter(email__iexact=email, status="VERIFIED").first()
    if link and _ok(link.user):
        return link.user, None
    invite = (AccessRequest.objects.filter(email__iexact=email, status=AccessRequest.Status.GRANTED)
              .order_by("-reviewed_at").first())
    if invite and (not invite.invite_expires_at or invite.invite_expires_at > timezone.now()):
        return None, invite
    return None, None


def can_receive_code(email: str) -> bool:
    """Any valid email may get a code, except burned identities and suspended / burned accounts."""
    from django.contrib.auth.models import User
    from governance.models import AccountState
    from governance.services import identity

    email = (email or "").strip().lower()
    if "@" not in email or identity.is_burned_email(email):
        return False
    for user in User.objects.filter(email__iexact=email):
        status = AccountState.objects.filter(user=user).values_list("status", flat=True).first() or "ACTIVE"
        if status != "ACTIVE" or not user.is_active:
            return False
    return True


def send_device_code(request, dev, email: str) -> bool:
    """
    Emails a 6-digit code if `email` has access. Returns True if a code was sent. Callers must show the same
    page either way (no hint whether the email is known).
    """
    from django.core.cache import cache
    from core.services import email_service

    email = (email or "").strip().lower()
    if not can_receive_code(email):
        return False
    user, invite = eligible_identity(email)
    ip = _client_ip(request)
    dev_key, ip_key = f"close:devcode:dev:{dev.device_id}", f"close:devcode:ip:{ip}"
    if int(cache.get(dev_key, 0) or 0) >= CODE_SENDS_PER_DEVICE_15MIN or \
            int(cache.get(ip_key, 0) or 0) >= CODE_SENDS_PER_IP_HOUR:
        return False
    cache.set(dev_key, int(cache.get(dev_key, 0) or 0) + 1, 15 * 60)
    cache.set(ip_key, int(cache.get(ip_key, 0) or 0) + 1, 3600)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    dev.access_code_hash = hashlib.sha256(f"{dev.device_id}:{code}".encode()).hexdigest()
    dev.access_code_email = email
    dev.access_code_expires_at = timezone.now() + timedelta(minutes=CODE_TTL_MINUTES)
    dev.access_code_attempts = 0
    dev.save(update_fields=["access_code_hash", "access_code_email", "access_code_expires_at",
                            "access_code_attempts"])
    name = (user.get_full_name() or user.username) if user else (
        (invite.full_name if invite else "") or dev.request_name or "there")
    ok, _msg = email_service.send_device_code_email(
        to_email=email, code=code, name=name,
        device_label=" · ".join(x for x in (dev.device_model or dev.device_name, dev.browser) if x),
        minutes=CODE_TTL_MINUTES)
    return ok


def verify_device_code(request, dev, code: str):
    """Checks the emailed code. On success approves the device for that person and returns the device."""
    from core.models import AuthorizedDevice
    from governance.services.fleet import security_event

    code = (code or "").strip().replace(" ", "")
    if not dev.access_code_hash or not dev.access_code_expires_at or dev.access_code_expires_at < timezone.now():
        raise ValueError("That code has expired. Send a new one.")
    if dev.access_code_attempts >= CODE_MAX_ATTEMPTS:
        raise ValueError("Too many wrong codes. Send a new one.")
    expected = hashlib.sha256(f"{dev.device_id}:{code}".encode()).hexdigest()
    if not (len(code) == 6 and code.isdigit() and hmac.compare_digest(expected, dev.access_code_hash)):
        AuthorizedDevice.objects.filter(pk=dev.pk).update(access_code_attempts=dev.access_code_attempts + 1)
        security_event("ACTIVATION_FAILED", request=request, username=dev.access_code_email,
                       device_id=dev.device_id, reason="wrong device code")
        raise ValueError("That code is not right. Check the email and try again.")
    email = dev.access_code_email
    if not can_receive_code(email):
        raise ValueError("This email can't be used. Ask your administrator.")
    user, invite = eligible_identity(email)
    dev.access_code_hash, dev.access_code_expires_at, dev.access_code_attempts = "", None, 0
    dev.authorized_email = email
    dev.save(update_fields=["access_code_hash", "access_code_expires_at", "access_code_attempts",
                            "authorized_email"])
    if user is not None:
        approve(dev, "email-code", user=user, request=request)
    # No account yet: the email is now proven; the device is approved when the account is created.
    return dev, user, invite
