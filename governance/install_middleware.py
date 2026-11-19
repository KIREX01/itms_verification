"""
Install-level web gates for laptop / workstation installs (DEPLOYMENT_ROLE=edge).

LocalAccessMiddleware
    Only this computer's own browser and phones paired through its QR code may reach the app.
    Every other device on the Wi-Fi / hotspot is refused before it sees the login page.
    Uses the socket address (REMOTE_ADDR) only - never X-Forwarded-For, which any client can forge.

InstallGateMiddleware
    An install that is unenrolled, moved to another computer, or revoked is locked: only sign-in
    and the administrator's /install/ activation page work. A moved copy erases its stored ITMS
    sessions the first time it is detected.
"""
from __future__ import annotations

import ipaddress
import logging
import time
from typing import Callable, Set

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)

_LOCAL_IPS_CACHE = {"at": 0.0, "ips": set()}


def _own_ips() -> Set[str]:
    """This computer's own addresses (cached for a minute)."""
    now = time.time()
    if now - _LOCAL_IPS_CACHE["at"] > 60:
        ips = set()
        try:
            from core.services.network_service import get_local_ipv4_addresses
            ips = {i.get("ip") for i in get_local_ipv4_addresses() if i.get("ip")}
        except Exception as exc:
            logger.debug("Could not list local addresses: %s", exc)
        _LOCAL_IPS_CACHE.update(at=now, ips=ips)
    return _LOCAL_IPS_CACHE["ips"]


def is_this_computer(remote_addr: str) -> bool:
    try:
        ip = ipaddress.ip_address((remote_addr or "").split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or str(ip) in _own_ips()


def _is_paired_phone(request: HttpRequest) -> bool:
    """A device that holds a valid signed trust cookie for an APPROVED, QR-paired / mobile device."""
    try:
        from core.models import AuthorizedDevice
        from core.services.device_access_service import DEVICE_TRUST_COOKIE_NAME, verify_device_trust_token

        token = request.COOKIES.get(DEVICE_TRUST_COOKIE_NAME, "")
        verified = verify_device_trust_token(token) if token else None
        if not verified:
            return False
        dev = AuthorizedDevice.objects.filter(device_id=verified["device_id"]).first()
        return bool(dev and dev.status == AuthorizedDevice.Status.APPROVED and (
            dev.linked_via == AuthorizedDevice.LinkedVia.QR_PAIRING
            or dev.device_type == AuthorizedDevice.DeviceType.MOBILE))
    except Exception as exc:
        logger.debug("Paired-phone check failed: %s", exc)
        return False


def _has_valid_pair_token(request: HttpRequest) -> bool:
    token = (request.GET.get("pair_token") or "").strip()
    if not token:
        return False
    try:
        from core.services.device_access_service import verify_qr_pairing_token
        return verify_qr_pairing_token(token) is not None
    except Exception:
        return False


def _refuse(request: HttpRequest, message: str, code: str, status: int) -> HttpResponse:
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"success": False, "error": message, "code": code}, status=status)
    return render(request, "governance/install_locked.html", {"message": message, "code": code}, status=status)


class LocalAccessMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central"
                or getattr(settings, "CLOSE_LOCAL_ACCESS", "pc_and_paired") != "pc_and_paired"):
            return self.get_response(request)
        remote = request.META.get("REMOTE_ADDR", "")
        if is_this_computer(remote):
            return self.get_response(request)
        if request.path.startswith("/static/") or request.path == "/favicon.ico":
            return self.get_response(request)
        if _has_valid_pair_token(request) or _is_paired_phone(request):
            return self.get_response(request)
        logger.warning("Refused web access from unpaired device %s to %s", remote, request.path)
        return _refuse(request, "This computer only accepts its own browser and phones paired by scanning "
                                "its QR code. Open CLOSE Copilot on this computer and scan the pairing QR.",
                       "DEVICE_NOT_PAIRED", 403)


INSTALL_GATE_ALLOWED = ("/static/", "/favicon.ico", "/login/", "/logout/", "/install/")


class InstallGateMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central":
            return self.get_response(request)
        from governance.services import install_guard

        state = install_guard.install_state()
        if state not in install_guard.LOCKED_STATES:
            return self.get_response(request)
        if state == install_guard.MOVED:
            install_guard.quarantine_moved_copy()
        if any(request.path.startswith(p) for p in INSTALL_GATE_ALLOWED):
            return self.get_response(request)
        return _refuse(request, install_guard.lock_message(state), f"INSTALL_{state}", 423)
