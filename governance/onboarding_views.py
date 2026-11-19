"""Public onboarding pages: request access, and complete signup from an invite."""
from __future__ import annotations

from django.contrib.auth import login as auth_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from governance.capabilities import COMPANY_EMAIL_ROLES, Role
from governance.services import identity, onboarding


def _bond_choices():
    """Bonds people can ask for: warehouses known from ITMS, else the configured list."""
    choices = []
    try:
        from core.models import Warehouse
        for code, name in Warehouse.objects.exclude(code="").order_by("name").values_list("code", "name"):
            if code and code not in [c for c, _ in choices]:
                choices.append((code, name or code))
    except Exception:
        pass
    if not choices:
        try:
            from core.services import config_service
            for b in config_service.get_available_bonds() or []:
                code = (b.get("code") or "").upper()
                if code:
                    choices.append((code, b.get("name") or code))
        except Exception:
            pass
    return choices


def _form_context(form_data=None, error=""):
    return {
        "roles": [(r.value, r.label) for r in Role],
        "company_roles": sorted(COMPANY_EMAIL_ROLES),
        "company_domains": identity.allowed_company_domains(),
        "bonds": _bond_choices(),
        "form_data": form_data or {},
        "error": error,
    }


def _central_url(path: str) -> str:
    from django.conf import settings
    return getattr(settings, "CLOSE_CENTRAL_URL", "https://close.kirex.online").rstrip("/") + path


def _is_edge() -> bool:
    from django.conf import settings
    return getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central"


@require_http_methods(["GET", "POST"])
def request_access_view(request: HttpRequest) -> HttpResponse:
    if _is_edge():
        # Access requests are reviewed on the central server, so they must be made there.
        return redirect(_central_url("/access/request/"))
    if request.user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        data = request.POST
        if (data.get("website") or "").strip():          # honeypot: bots fill hidden fields
            return render(request, "governance/request_access.html", {"submitted": True})
        try:
            onboarding.submit_request(
                full_name=data.get("full_name", ""),
                email=data.get("email", ""),
                requested_role=data.get("requested_role", ""),
                bond_code=data.get("bond_code", ""),
                company_email=data.get("company_email", ""),
                phone=data.get("phone", ""),
                message=data.get("message", ""),
                request=request,
            )
        except onboarding.OnboardingError as exc:
            return render(request, "governance/request_access.html",
                          _form_context(data, str(exc)), status=400)
        # Same response whether the request was stored, throttled or swallowed (no enumeration).
        return render(request, "governance/request_access.html", {"submitted": True})
    return render(request, "governance/request_access.html", _form_context())


@require_http_methods(["GET", "POST"])
def complete_signup_view(request: HttpRequest) -> HttpResponse:
    if _is_edge():
        query = request.META.get("QUERY_STRING", "")
        return redirect(_central_url("/signup/") + (f"?{query}" if query else ""))
    if request.user.is_authenticated:
        return redirect("/")
    token = (request.GET.get("invite") or request.POST.get("invite") or "").strip()
    invite = onboarding.find_live_invite(token)
    if invite is None:
        if token:
            return render(request, "governance/complete_signup.html", {"invalid": True}, status=400)
        return redirect("/access/request/")

    ctx = {"invite_token": token, "req": invite, "form_data": {}}
    if request.method == "POST":
        try:
            user = onboarding.redeem_invite(
                token=token,
                code=request.POST.get("code", ""),
                username=request.POST.get("username", ""),
                password=request.POST.get("password", ""),
                password_confirm=request.POST.get("password_confirm", ""),
                request=request,
            )
        except onboarding.OnboardingError as exc:
            still_live = onboarding.find_live_invite(token)
            if still_live is None:
                return render(request, "governance/complete_signup.html",
                              {"invalid": True, "error": str(exc)}, status=400)
            ctx.update({"req": still_live, "error": str(exc),
                        "form_data": {"username": request.POST.get("username", "")}})
            return render(request, "governance/complete_signup.html", ctx, status=400)
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("/")
    return render(request, "governance/complete_signup.html", ctx)


def legacy_signup_verify_redirect(request: HttpRequest) -> HttpResponse:
    return redirect("/access/request/")
