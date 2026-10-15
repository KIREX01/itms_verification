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
    "/api/itms/status/",  # Safe connectivity and heartbeat probe
}

# Path prefixes accessible without authentication:
PUBLIC_PREFIXES = (
    "/login/",
    "/signup/",
    "/logout/",
    "/static/",
    "/favicon.ico",
    "/admin/",
)


class ApiAuthMiddleware:
    """Enforces authentication across all API, media, and private application routes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path

        # 1. Allow whitelisted static, admin, and authentication routes
        if path in PUBLIC_EXACT_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return self.get_response(request)

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
        return self.get_response(request)
