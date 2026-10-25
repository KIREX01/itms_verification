"""
Maintenance Mode Middleware for ITMS Verification Copilot.

When maintenance mode is active (via config_service or secure/maintenance.flag):
- Intercepts incoming web requests and returns HTTP 503 with the compiled maintenance page.
- Returns JSON error payloads with status 503 for API endpoints.
- Supports bypass for administrative tasks via ?bypass_maintenance=1 or session override.
- Allows static asset delivery so assets render cleanly.
"""
from typing import Callable
from django.http import HttpRequest, HttpResponse, JsonResponse
from core.services import config_service


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

        # 2. Allow static files and internal media
        path = request.path or "/"
        if path.startswith("/static/") or path.startswith("/favicon.ico"):
            return self.get_response(request)

        # 3. Allow administrative bypass via query parameter or session
        if request.GET.get("bypass_maintenance") in ("1", "true", "yes"):
            if hasattr(request, "session"):
                request.session["bypass_maintenance"] = True
            return self.get_response(request)

        if getattr(request, "session", None) and request.session.get("bypass_maintenance"):
            return self.get_response(request)

        # 4. Allow Django admin if user is superuser
        if path.startswith("/admin/"):
            user = getattr(request, "user", None)
            if user and user.is_authenticated and user.is_staff:
                return self.get_response(request)

        # 5. Format response based on request type
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

        html = config_service.generate_maintenance_html()
        response = HttpResponse(html, status=503, content_type="text/html")
        response["Retry-After"] = str(m_cfg.get("retry_after_seconds", 30))
        return response
