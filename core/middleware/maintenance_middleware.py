"""
Maintenance Mode Middleware for CLOSE Copilot.

When maintenance mode is active (via config_service or secure/maintenance.flag):
- Intercepts incoming web requests and returns HTTP 503 with the maintenance page.
- Returns JSON error payloads with status 503 for API endpoints.
- Lets authenticated superusers through (to verify the system before reopening it).
- Keeps static assets and the login/logout pages reachable so an administrator can sign in.

Must be registered AFTER django.contrib.auth.middleware.AuthenticationMiddleware so that
request.user is available. There is intentionally no query-string bypass.
"""
from typing import Callable
from django.http import HttpRequest, HttpResponse, JsonResponse
from core.services import config_service

# Paths that stay reachable during maintenance (assets + admin sign-in).
MAINTENANCE_ALLOWED_PREFIXES = (
    "/static/",
    "/favicon.ico",
    "/login/",
    "/logout/",
)


def _is_maintenance_admin(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


class MaintenanceModeMiddleware:
    """
    Evaluates whether the Copilot Web Dashboard is under maintenance.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 1. Fast check: is maintenance mode enabled?
        if not config_service.is_maintenance_enabled():
            return self.get_response(request)

        # 2. Allow static files and admin sign-in pages
        path = request.path or "/"
        if any(path.startswith(p) for p in MAINTENANCE_ALLOWED_PREFIXES):
            return self.get_response(request)

        # 3. Authenticated superusers may use the whole system during maintenance
        if _is_maintenance_admin(request):
            return self.get_response(request)

        # 4. Format response based on request type
        accept_header = request.headers.get("Accept", "")
        is_api = (
            path.startswith("/api/")
            or "application/json" in accept_header
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )

        m_cfg = config_service.get_maintenance_config()
        if is_api:
            return JsonResponse(
                {
                    "success": False,
                    "maintenance": True,
                    "title": m_cfg.get("title"),
                    "message": m_cfg.get("message"),
                    "estimated_duration": m_cfg.get("estimated_duration"),
                    "retry_after_seconds": m_cfg.get("retry_after_seconds", 30),
                    "error": "Service is temporarily offline for scheduled maintenance.",
                },
                status=503,
            )

        # Render only; the static fallback files are written when maintenance is toggled.
        html = config_service.generate_maintenance_html(write_files=False)
        response = HttpResponse(html, status=503, content_type="text/html")
        response["Retry-After"] = str(m_cfg.get("retry_after_seconds", 30))
        return response
