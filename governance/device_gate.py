"""
DeviceAdmissionMiddleware: unknown devices see only "This device is not authorized".

Allowed without an approved device: static files, the /device/ request pages, and the machine-to-
machine install sync API (HMAC-authenticated). On a laptop install the laptop's own browser is the
enrolled machine itself and is always allowed. Everything else - including the sign-in page - needs
an APPROVED device key, so passwords cannot even be tried from unapproved devices.
"""
from __future__ import annotations

from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from governance.services import device_admission

EXEMPT_PREFIXES = ("/static/", "/favicon.ico", "/device/", "/backoffice/api/v1/installs/",
                   "/apple-touch-icon.png", "/site.webmanifest",
                   # New people: request access, and finish sign-up from the invite link (which approves the device).
                   "/access/request/", "/signup/")


def set_device_cookie(response: HttpResponse, request: HttpRequest, cookie_value: str) -> None:
    from core.services.device_access_service import DEVICE_TRUST_COOKIE_MAX_AGE, DEVICE_TRUST_COOKIE_NAME

    response.set_cookie(DEVICE_TRUST_COOKIE_NAME, cookie_value, max_age=DEVICE_TRUST_COOKIE_MAX_AGE,
                        httponly=True, samesite="Lax", secure=request.is_secure())


def admission_enabled() -> bool:
    return bool(getattr(settings, "CLOSE_DEVICE_ADMISSION", True))


class DeviceAdmissionMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not admission_enabled() or any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
            from governance.install_middleware import is_this_computer
            if is_this_computer(request.META.get("REMOTE_ADDR", "")):
                return self.get_response(request)  # the enrolled laptop itself

        dev = device_admission.admitted_device(request)
        if dev is not None:
            from core.services.security_envelope import extract_client_ip
            from governance.services.fleet import check_device_anomalies
            if check_device_anomalies(request, dev, extract_client_ip(request)):
                return self.get_response(request)
            return _blocked(request, waiting=True, device=dev)

        # A phone scanning a pairing QR: record its request on behalf of the person who showed the QR.
        token = (request.GET.get("pair_token") or "").strip()
        if token:
            from core.services.device_access_service import verify_qr_pairing_token
            paired_user = verify_qr_pairing_token(token)
            if paired_user is not None:
                try:
                    dev, cookie = device_admission.request_access(
                        request, name=paired_user.get_full_name() or paired_user.username,
                        email=paired_user.email, note="Phone paired by scanning a QR code", user=paired_user,
                        install_id=_local_install_id(),
                    )
                except ValueError as exc:
                    return _blocked(request, str(exc))
                from governance.services.fleet import security_event
                security_event("QR_PAIRED", request=request, user=paired_user, device_id=dev.device_id,
                               reason="phone scanned a pairing QR; waiting for approval")
                response = _blocked(request, waiting=True, device=dev)
                set_device_cookie(response, request, cookie)
                return response

        return _blocked(request)


def _local_install_id() -> str:
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central":
        return ""
    try:
        from governance.services.policy_sync import load_enrollment
        return load_enrollment(decrypt=False).get("install_id", "")
    except Exception:
        return ""


def _mask_email(email: str) -> str:
    if "@" not in (email or ""):
        return email or ""
    local, domain = email.split("@", 1)
    return (local[:2] + "•" * max(1, len(local) - 2)) + "@" + domain


def _blocked(request: HttpRequest, error: str = "", waiting: bool = False, device=None, step: str = "",
             form: dict = None) -> HttpResponse:
    device = device if device is not None else device_admission.current_device(request)
    status = getattr(device, "status", "")
    message = "This device isn't authorised to use CLOSE Copilot."
    email = getattr(device, "request_email", "") or ""
    if status in ("BLOCKED", "REVOKED"):
        step = "blocked"
    elif waiting or (step == "waiting" and status == "PENDING"):
        step = "waiting"            # e.g. phone paired by QR, or no email sending: the administrator approves
    elif step != "email" and status == "PENDING" and email:
        step = "code"               # email entered: enter the code (or wait for the administrator)
    else:
        step = "email"
    if request.path.startswith("/api/") or request.path.startswith("/media/") \
            or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"success": False, "code": "DEVICE_NOT_APPROVED", "error": message,
                             "device_status": status or "UNKNOWN"}, status=403)
    return render(request, "governance/device_access.html", {
        "message": message,
        "error": error,
        "device": device,
        "status": status,
        "waiting": waiting,
        "step": step,
        "masked_email": _mask_email(email),
        "form": form or {"first_name": (getattr(device, "request_name", "") or "").partition(" ")[0],
                         "last_name": (getattr(device, "request_name", "") or "").partition(" ")[2],
                         "email": email},
        "code": device_admission.device_code(device.device_id) if device else "",
    }, status=403)
