"""
Django admin (/admin/) guard.

On laptop installs the raw Django admin is switched off entirely: a local administrator could
otherwise edit records directly and get around the central server's control. On the central
server it requires the same 2-step sign-in as the backoffice.
"""
from __future__ import annotations

from typing import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect


class AdminSiteGuardMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith("/admin/"):
            return self.get_response(request)
        if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
            raise Http404("Not available on installed apps.")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_superuser:
            from governance.services import totp
            if not totp.is_enrolled(user):
                return redirect("/backoffice/2fa/setup/")
            if not request.session.get(totp.SESSION_KEY):
                return redirect(f"/backoffice/2fa/?next={request.path}")
        return self.get_response(request)
