"""Administrator 2-step sign-in: set up an authenticator app, then enter its code once per session."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from governance.services import audit, authz, totp


def _admin_or_404(request: HttpRequest):
    from django.conf import settings

    if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
        raise Http404("The backoffice is only available on the central server.")
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return redirect(f"/login/?next={request.path}")
    if not authz.is_platform_admin(user):
        return render(request, "governance/no_access.html",
                      {"message": "The backoffice is for the administrator only.", "code": "ADMIN_REQUIRED"},
                      status=403)
    return None


def _safe_next(request: HttpRequest) -> str:
    nxt = request.POST.get("next") or request.GET.get("next") or "/backoffice/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) or not nxt.startswith("/"):
        nxt = "/backoffice/"
    return nxt


@require_http_methods(["GET", "POST"])
def twofa_setup(request: HttpRequest) -> HttpResponse:
    blocked = _admin_or_404(request)
    if blocked:
        return blocked
    if totp.is_enrolled(request.user):
        return redirect("backoffice:twofa_verify")
    if request.method == "POST":
        recovery = totp.confirm_enrollment(request.user, request.POST.get("code", ""))
        if recovery is None:
            messages.error(request, "That code didn't match. Check the time on your phone and try the newest code.")
            return redirect("backoffice:twofa_setup")
        request.session[totp.SESSION_KEY] = True
        audit.record(request.user, "admin.2fa_enrolled", target_type="user", target_id=request.user.pk,
                     target_label=request.user.username, request=request)
        return render(request, "backoffice/twofa_recovery.html", {"recovery_codes": recovery})
    secret, uri = totp.start_enrollment(request.user)
    return render(request, "backoffice/twofa_setup.html", {
        "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "qr_svg": totp.qr_svg(uri),
    })


@require_http_methods(["GET", "POST"])
def twofa_verify(request: HttpRequest) -> HttpResponse:
    blocked = _admin_or_404(request)
    if blocked:
        return blocked
    if not totp.is_enrolled(request.user):
        return redirect("backoffice:twofa_setup")
    if request.method == "POST":
        from governance.services import login_guard

        locked, minutes = login_guard.lock_status(request.user.username, request)
        if locked:
            messages.error(request, f"Too many failed attempts. Try again in {minutes} minute(s).")
        elif totp.verify(request.user, request.POST.get("code", "")):
            request.session.cycle_key()
            request.session[totp.SESSION_KEY] = True
            login_guard.record(request.user.username, True, request, user=request.user, reason="2FA_OK")
            return redirect(_safe_next(request))
        else:
            login_guard.record(request.user.username, False, request, user=request.user, reason="BAD_2FA_CODE")
            messages.error(request, "Incorrect code.")
    return render(request, "backoffice/twofa_verify.html", {"next": _safe_next(request)})
