"""
Global API and Media Authentication Middleware.

Enforces zero-trust and zero-data-disclosure:
1. Every endpoint starting with `/api/` (except safe, whitelisted endpoints like `/api/itms/status/`)
   requires an authenticated user session (`request.user.is_authenticated`).
   Unauthenticated requests receive an immediate HTTP 401 Unauthorized with standardized OHAS error detail.
2. Every request to `/media/` (vault evidence, vehicle photographs) requires an authenticated user session.
   Unauthenticated requests receive HTTP 401 Unauthorized.
3. Every browser web request (except whitelisted auth views `/login/`, `/signup/`, `/signup/verify/`, `/logout/`, and `/static/`)
   requires an authenticated user session, redirecting anonymous users to `/login/?next=<path>`.
"""
from __future__ import annotations

import logging
from typing import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from core.services.api_envelope import (
    create_error_detail,
    format_api_response,
)

logger = logging.getLogger(__name__)

# Exact paths accessible without authentication:
PUBLIC_EXACT_PATHS = {
    "/login/",
    "/signup/",
    "/signup/verify/",
    "/logout/",
    "/access/request/",
    "/access/activate/",
    "/api/itms/status/",  # Safe connectivity and heartbeat probe
}

# Path prefixes accessible without authentication:
PUBLIC_PREFIXES = (
    "/login/",
    "/signup/",
    "/logout/",
    "/access/",
    "/static/",
    "/favicon.ico",
    "/admin/",
)

# Paths always reachable even when a device/IP/MAC is blocked or pending approval
# so legitimate operators can submit an email activation request or enter an unlock code:
ACCESS_PORTAL_PREFIXES = (
    "/access/",
    "/logout/",
    "/static/",
    "/favicon.ico",
)


class ApiAuthMiddleware:
    """Enforces authentication and zero-trust device/IP/MAC access control across routes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def _finalize_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        cookie_meta = getattr(request, "_attach_device_trust_cookie", None)
        if cookie_meta and isinstance(cookie_meta, tuple) and len(cookie_meta) == 2:
            try:
                from core.services import device_access_service
                device_access_service.attach_device_trust_cookie(response, cookie_meta[0], cookie_meta[1])
            except Exception:
                pass
        return response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path

        # 0. Evaluate Zero-Trust Device (IP / MAC / Device ID) & QR Pairing access
        if not any(path.startswith(p) for p in ACCESS_PORTAL_PREFIXES):
            verdict = None
            try:
                from core.services import device_access_service
                verdict = device_access_service.evaluate_request_access(request)
            except Exception as exc:
                logger.debug("Device access guard evaluation notice: %s", exc)

            if verdict and not verdict.get("allowed", True):
                err_code = verdict.get("code", "DEVICE_ACCESS_DENIED")
                err_msg = verdict.get("reason", "Unauthorized device or IP address.")
                if path.startswith("/api/"):
                    error = create_error_detail(
                        code=err_code,
                        message=err_msg,
                        field="device",
                    )
                    return format_api_response(
                        errors=[error],
                        status_code=403,
                        request=request,
                    )
                if path.startswith("/media/"):
                    return HttpResponse("Forbidden: Device not authorized", status=403, content_type="text/plain")

                from django.shortcuts import render
                ident = verdict.get("identity", {})
                return render(
                    request,
                    "core/access_request.html",
                    {
                        "blocked_code": err_code,
                        "blocked_reason": err_msg,
                        "device_identity": ident,
                        "device_obj": verdict.get("device"),
                    },
                    status=403,
                )

        # 1. Allow whitelisted static, admin, access portal, and authentication routes
        if path in PUBLIC_EXACT_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return self._finalize_response(request, self.get_response(request))

        # 2. Check user authentication
        user = getattr(request, "user", None)
        is_authenticated = bool(user and getattr(user, "is_authenticated", False))

        if not is_authenticated:
            # A. If it's an API request, return HTTP 401 Unauthorized with standardized OHAS envelope
            if path.startswith("/api/"):
                error = create_error_detail(
                    code="AUTHENTICATION_REQUIRED",
                    message="Authentication required. Please sign in to the ITMS Verification Copilot.",
                    field="session",
                )
                return format_api_response(
                    errors=[error],
                    status_code=401,
                    request=request,
                )

            # B. If it's a media request (vault evidence photos, crops, docs), return HTTP 401
            if path.startswith("/media/"):
                return HttpResponse("Unauthorized media access", status=401, content_type="text/plain")

            # C. If it's a browser request (e.g. /, /upload/, /mobile/), redirect to login
            full_path = request.get_full_path()
            return redirect(f"/login/?next={full_path}")

        # 3. For authenticated users: if opening an /api/ endpoint directly via browser URL bar, redirect to dashboard
        if is_authenticated and path.startswith("/api/"):
            sec_dest = request.headers.get("Sec-Fetch-Dest", "")
            sec_mode = request.headers.get("Sec-Fetch-Mode", "")
            accept = request.headers.get("Accept", "")
            if sec_dest == "document" or (sec_mode == "navigate" and "text/html" in accept):
                return redirect("/")

        # Authenticated user - proceed normally
        return self._finalize_response(request, self.get_response(request))
