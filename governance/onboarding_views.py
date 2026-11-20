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
    if not token and onboarding.verified_signup(request) is not None:
        return _verified_signup(request)
    invite = onboarding.find_live_invite(token)
    if invite is None:
        if token:
            return render(request, "governance/complete_signup.html", {"invalid": True}, status=400)
        return redirect("/access/request/")

    ctx = {"invite_token": token, "req": invite,
           "form_data": {"full_name": invite.full_name,
                         "username": onboarding.suggest_username(invite.full_name, invite.email)}}
    if request.method == "POST":
        try:
            user = onboarding.redeem_invite(
                token=token,
                code=request.POST.get("code", ""),
                username=request.POST.get("username", ""),
                password=request.POST.get("password", ""),
                password_confirm=request.POST.get("password_confirm", ""),
                request=request,
                full_name=request.POST.get("full_name", ""),
            )
        except onboarding.OnboardingError as exc:
            still_live = onboarding.find_live_invite(token)
            if still_live is None:
                return render(request, "governance/complete_signup.html",
                              {"invalid": True, "error": str(exc)}, status=400)
            ctx.update({"req": still_live, "error": str(exc),
                        "form_data": {"username": request.POST.get("username", ""),
                                      "full_name": request.POST.get("full_name", "")}})
            return render(request, "governance/complete_signup.html", ctx, status=400)
        auth_login(request, user, backend="governance.auth_backend.LockoutModelBackend")
        response = redirect("/")
        _approve_signup_device(request, response, user)
        return response
    return render(request, "governance/complete_signup.html", ctx)


def _approve_signup_device(request: HttpRequest, response: HttpResponse, user) -> None:
    """The invite proved this person's email, so the device they signed up on is approved for them."""
    from governance.device_gate import set_device_cookie
    from governance.services import device_admission

    try:
        dev = device_admission.current_device(request)
        cookie = ""
        if dev is None or dev.status in ("BLOCKED", "REVOKED"):
            if dev is not None:
                return  # a blocked device stays blocked
            dev, cookie = device_admission.request_access(
                request, name=user.get_full_name() or user.username, email=user.email, user=user,
                note="Signed up from an invite", notify_admins=False)
        if dev.status != "APPROVED":
            device_admission.approve(dev, "invite-signup", user=user, request=request)
        if cookie:
            set_device_cookie(response, request, cookie)
    except Exception as exc:  # never fail sign-up over this
        import logging
        logging.getLogger(__name__).warning("Could not approve the sign-up device: %s", exc)


def legacy_signup_verify_redirect(request: HttpRequest) -> HttpResponse:
    return redirect("/access/request/")


def _verified_signup(request: HttpRequest) -> HttpResponse:
    """Sign-up after the device code proved the email: names and email carried over, choose username + password."""
    data = onboarding.verified_signup(request)
    ctx = {"mode": "self", "email": data["email"],
           "form_data": {"first_name": data.get("first_name", ""), "last_name": data.get("last_name", ""),
                         "username": onboarding.suggest_username(
                             f"{data.get('first_name', '')} {data.get('last_name', '')}", data["email"])}}
    if request.method == "POST":
        form = {k: request.POST.get(k, "") for k in ("first_name", "last_name", "username")}
        try:
            user = onboarding.self_signup(
                request, username=form["username"], password=request.POST.get("password", ""),
                password_confirm=request.POST.get("password_confirm", ""),
                first_name=form["first_name"], last_name=form["last_name"])
        except onboarding.OnboardingError as exc:
            if onboarding.verified_signup(request) is None:
                return redirect("/device/?step=email")
            ctx.update({"error": str(exc), "form_data": form})
            return render(request, "governance/complete_signup.html", ctx, status=400)
        auth_login(request, user, backend="governance.auth_backend.LockoutModelBackend")
        response = redirect("/")
        _approve_signup_device(request, response, user)
        return response
    return render(request, "governance/complete_signup.html", ctx)

