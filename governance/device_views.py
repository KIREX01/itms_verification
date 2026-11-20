"""
/device/: an unknown device gets access.

1. First name, last name and email.
2. A 6-digit code is emailed (Resend). It proves the email and unlocks the device.
3. Existing account -> sign in. New person -> create account (names and email carried over).
   Laptops without email sending fall back to the administrator approving the request.
"""
from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from governance.device_gate import _blocked, _local_install_id, set_device_cookie
from governance.services import device_admission, onboarding


def _client_report(request: HttpRequest) -> dict:
    try:
        report = json.loads(request.POST.get("client_report") or "{}")
        return report if isinstance(report, dict) else {}
    except ValueError:
        return {}


@require_http_methods(["GET", "POST"])
def device_access_view(request: HttpRequest) -> HttpResponse:
    if device_admission.admitted_device(request) is not None:
        unlocked = request.GET.get("unlocked", "")
        return redirect(f"/login/?unlocked={'invite' if unlocked == 'invite' else '1'}" if unlocked else "/")
    if request.method == "GET":
        if onboarding.verified_signup(request) is not None and request.GET.get("step") != "email":
            return redirect("/signup/")
        return _blocked(request, step=request.GET.get("step", ""))

    if (request.POST.get("website") or "").strip():  # honeypot
        return _blocked(request, step="code")
    action = request.POST.get("action", "send")

    if action == "verify":
        dev = device_admission.current_device(request)
        if dev is None:
            return _blocked(request, error="Enter your details first.")
        try:
            dev, user, _invite = device_admission.verify_device_code(request, dev, request.POST.get("code", ""))
        except ValueError as exc:
            return _blocked(request, error=str(exc), step="code")
        if user is not None:
            return redirect("/login/?unlocked=1")
        first, _, last = (dev.request_name or "").partition(" ")
        onboarding.start_verified_signup(request, dev.access_code_email, first, last, dev.device_id)
        return redirect("/signup/")

    form = {k: (request.POST.get(k) or "").strip() for k in ("first_name", "last_name", "email")}
    form["email"] = form["email"].lower()
    try:
        first = onboarding.clean_name(form["first_name"], "first name")
        last = onboarding.clean_name(form["last_name"], "last name")
    except onboarding.OnboardingError as exc:
        return _blocked(request, error=str(exc), form=form)
    try:
        validate_email(form["email"])
    except ValidationError:
        return _blocked(request, error="Enter a valid email address.", form=form)

    email = form["email"]
    user, invite = device_admission.eligible_identity(email)
    codes = device_admission.email_codes_available()
    try:
        dev, cookie = device_admission.request_access(
            request, name=f"{first} {last}", email=email, client_report=_client_report(request),
            user=user, install_id=_local_install_id(), notify_admins=not codes,
        )
    except ValueError as exc:
        return _blocked(request, error=str(exc), form=form)
    if codes:
        device_admission.send_device_code(request, dev, email)
        target = "/device/?step=code"
    else:
        target = "/device/?step=waiting"  # e.g. a laptop that can't send email: the administrator approves
    response = redirect(target)
    set_device_cookie(response, request, cookie)
    return response
