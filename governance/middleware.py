"""
GovernanceMiddleware: enforces the route → capability table for every signed-in request.

Runs after ApiAuthMiddleware (authentication + device checks). Responsibilities:
  * Suspended / burned accounts are signed out immediately and refused.
  * Each view's required capability (governance.enforcement.VIEW_CAPABILITIES) is checked at
    the user's active bond; unlisted /api/ views are denied (deny by default).
  * On the central server, the platform administrator is sent to the backoffice instead of
    the operator dashboard.
"""
from __future__ import annotations

import logging
from typing import Callable

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from governance import capabilities as C
from governance import enforcement
from governance.services import authz

logger = logging.getLogger(__name__)

# Paths governed elsewhere (auth pages, static, Django admin, backoffice has its own guard).
EXEMPT_PREFIXES = ("/login/", "/logout/", "/signup/", "/access/", "/static/", "/favicon.ico", "/admin/", "/backoffice/")


def _deny(request: HttpRequest, message: str, code: str = "PERMISSION_DENIED", status: int = 403) -> HttpResponse:
    if request.path.startswith("/api/") or request.path.startswith("/media/"):
        return JsonResponse({"success": False, "error": message, "code": code}, status=status)
    ctx = {"message": message, "code": code}
    user = getattr(request, "user", None)
    if code == "NO_ROLE" and user is not None and user.is_authenticated:
        from governance.services import identity, memberships
        roles = memberships.assigned_roles(user)
        ctx.update({
            "has_role": bool(roles),
            # Team Leaders, QIs and Supervisors get their bonds by connecting ITMS.
            "needs_itms": any(r in COMPANY_ROLES for r in roles),
            "company_email": identity.verified_company_email(user) or (user.email if identity.is_company_email(user.email or "") else ""),
        })
    return render(request, "governance/no_access.html", ctx, status=status)


COMPANY_ROLES = ("TEAM_LEADER", "QUALITY_INSPECTOR", "SUPERVISOR")
ITMS_BOOTSTRAP_VIEWS = {"api_itms_connect", "api_sync_bonds"}


class GovernanceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        path = request.path or "/"
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return None
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return None  # ApiAuthMiddleware already handled anonymous access

        # 1. Account standing: suspended/burned accounts are signed out on their next request.
        if not authz.account_ok(user):
            status = authz.account_status(user)
            logout(request)
            if path.startswith("/api/"):
                return JsonResponse({"success": False, "error": "This account is no longer active.",
                                     "code": f"ACCOUNT_{status}"}, status=401)
            return redirect("/login/")

        view_name = getattr(view_func, "__name__", "")
        spec = enforcement.spec_for(view_name, request.method)

        # 2. Platform administrator: backoffice-first on the central server, otherwise allowed.
        if authz.is_platform_admin(user):
            if (view_name == "dashboard_view" and getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central"
                    and not authz.roles_by_bond(user)):
                return redirect("/backoffice/")
            return None

        if spec == enforcement.PUBLIC:
            return None
        if not authz.enforcement_enabled():
            return None  # legacy mode; views that self-protect (superuser checks) still apply
        if spec == enforcement.ADMIN:
            return _deny(request, "Administrator privileges required.", "ADMIN_REQUIRED")

        if spec is None:
            if path.startswith("/api/") or path.startswith("/media/"):
                logger.warning("Governance: denied unlisted view %s (%s %s)", view_name, request.method, path)
                return _deny(request, "This action is not permitted.", "NOT_PERMITTED")
            return None  # unlisted non-API pages (none expected) fall through

        if isinstance(spec, str) and spec in enforcement.SPECIAL_RESOLVERS:
            spec = enforcement.SPECIAL_RESOLVERS[spec](request)

        required = spec if isinstance(spec, tuple) else (spec,)
        if view_name in ITMS_BOOTSTRAP_VIEWS and not authz.roles_by_bond(user):
            # A role without bonds yet: connecting ITMS is how the bonds arrive.
            from governance.services import memberships
            if any(r in COMPANY_ROLES for r in memberships.assigned_roles(user)):
                return None
        if view_name == "dashboard_view" and authz.has_capability(user, authz.ANY_MEMBER):
            # Capture-only roles (Helpers) work from the mobile companion.
            desk_caps = (C.DASHBOARD_VIEW, C.QUEUE_VIEW, C.STOCK_VIEW, C.REPORTS_VIEW, C.ORDERS_VIEW)
            if not authz.has_any(user, desk_caps) and authz.has_any(user, (C.CAPTURE_UPLOAD, C.STOCK_SCAN)):
                return redirect("/mobile/")
        if authz.has_any(user, required):
            return None

        if view_name == "dashboard_view":
            return _deny(request, "Your account has no active role yet. Ask your Team Leader or the "
                                  "administrator to grant you access to a bond.", "NO_ROLE")
        bond = authz.active_bond_code(user)
        return _deny(request, f"Your role at {bond or 'this bond'} does not allow this action.", "PERMISSION_DENIED")
