from __future__ import annotations

import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from core.services import auth_service, email_service

logger = logging.getLogger(__name__)


def login_view(request: HttpRequest) -> HttpResponse:
    """Operator Sign In view."""
    raw_next = request.GET.get("next") or request.POST.get("next") or ""
    if raw_next and url_has_allowed_host_and_scheme(raw_next, allowed_hosts={request.get_host()}):
        next_url = raw_next
    else:
        next_url = "/"

    if request.user.is_authenticated:
        return redirect(next_url if next_url != "/" else "core:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        remember = bool(request.POST.get("remember"))

        user, err = auth_service.authenticate_operator(username, password)
        if user is None:
            return render(request, "core/login.html", {
                "error": err,
                "last_username": username,
                "next_url": next_url,
            })

        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=remember)
        return redirect(next_url if next_url != "/" else "core:dashboard")

    # If no users exist in the active database yet, prompt first-time onboarding
    if User.objects.count() == 0:
        return redirect("core:signup")

    prefs = auth_service.get_operator_preferences()
    last_username = prefs.get("last_username", "")

    return render(request, "core/login.html", {
        "next_url": next_url,
        "last_username": last_username,
    })


def signup_view(request: HttpRequest) -> HttpResponse:
    """Operator Registration / Onboarding view with 2FA email verification."""
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        password_confirm = request.POST.get("password_confirm", "").strip()

        connect_itms = bool(request.POST.get("connect_itms"))
        itms_email = request.POST.get("itms_email", "").strip()
        itms_password = request.POST.get("itms_password", "").strip()

        if not username or not password:
            return render(request, "core/signup.html", {
                "error": "Username and password are required.",
                "form_data": request.POST,
            })

        if not email or "@" not in email:
            return render(request, "core/signup.html", {
                "error": "A valid work email is required for 2-Factor Authentication security verification.",
                "form_data": request.POST,
            })

        if password != password_confirm:
            return render(request, "core/signup.html", {
                "error": "Passwords do not match.",
                "form_data": request.POST,
            })

        if len(password) < 6:
            return render(request, "core/signup.html", {
                "error": "Password must be at least 6 characters.",
                "form_data": request.POST,
            })

        if User.objects.filter(username=username).exists():
            return render(request, "core/signup.html", {
                "error": f"Username '{username}' already exists. Please choose a different username.",
                "form_data": request.POST,
            })

        # Check if 2FA email verification is enabled
        use_2fa = getattr(settings, "SIGNUP_2FA_ENABLED", True)
        if use_2fa:
            code = email_service.generate_2fa_code()
            ok, msg = email_service.send_2fa_verification_code(email, code, username=username)

            request.session["pending_signup"] = {
                "username": username,
                "full_name": full_name,
                "email": email,
                "password": password,
                "connect_itms": connect_itms,
                "itms_email": itms_email,
                "itms_password": itms_password,
                "code": code,
                "expires_at": time.time() + 600,  # 10 minutes
                "last_sent_at": time.time(),
            }
            request.session.modified = True

            if not ok:
                messages.warning(request, f"Email delivery notice: {msg}")

            return redirect("core:signup_verify")

        # Direct creation if 2FA is explicitly disabled
        user, err = auth_service.create_operator_account(
            username=username,
            password=password,
            full_name=full_name,
            email=email,
        )
        if user is None:
            return render(request, "core/signup.html", {
                "error": err,
                "form_data": request.POST,
            })

        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=True)
        return redirect("core:dashboard")

    return render(request, "core/signup.html", {"form_data": {}})


def signup_verify_view(request: HttpRequest) -> HttpResponse:
    """Verifies the 6-digit email 2FA code before activating operator account."""
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    pending = request.session.get("pending_signup")
    if not pending:
        messages.error(request, "No pending registration found. Please sign up below.")
        return redirect("core:signup")

    email = pending.get("email", "")
    username = pending.get("username", "")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        # Handle Resend Code request
        if action == "resend":
            last_sent = pending.get("last_sent_at", 0)
            now = time.time()
            if now - last_sent < 30:
                wait_sec = int(30 - (now - last_sent))
                return render(request, "core/signup_verify.html", {
                    "email": email,
                    "error": f"Please wait {wait_sec} seconds before requesting a new code.",
                })

            new_code = email_service.generate_2fa_code()
            pending["code"] = new_code
            pending["expires_at"] = now + 600
            pending["last_sent_at"] = now
            request.session["pending_signup"] = pending
            request.session.modified = True

            ok, msg = email_service.send_2fa_verification_code(email, new_code, username=username)
            return render(request, "core/signup_verify.html", {
                "email": email,
                "message": "A new 6-digit security code has been sent to your email.",
                "error": "" if ok else msg,
            })

        # Handle Verification Code submission
        submitted_code = request.POST.get("code", "").strip()
        expected_code = str(pending.get("code", "")).strip()
        expires_at = pending.get("expires_at", 0)

        if time.time() > expires_at:
            return render(request, "core/signup_verify.html", {
                "email": email,
                "error": "This verification code has expired. Please click 'Resend Code'.",
            })

        if submitted_code != expected_code:
            return render(request, "core/signup_verify.html", {
                "email": email,
                "error": "Invalid verification code. Please check your inbox or spam folder.",
            })

        # Code is valid! Create the operator account in the database
        user, err = auth_service.create_operator_account(
            username=pending["username"],
            password=pending["password"],
            full_name=pending.get("full_name", ""),
            email=pending.get("email", ""),
        )

        if user is None:
            return render(request, "core/signup_verify.html", {
                "email": email,
                "error": err,
            })

        # Connect optional ITMS credentials if provided
        if pending.get("connect_itms") and pending.get("itms_email") and pending.get("itms_password"):
            ok, itms_msg = auth_service.connect_itms_account(pending["itms_email"], pending["itms_password"])
            if ok:
                messages.success(request, f"Connected to ITMS WebApp as '{pending['itms_email']}'.")
            else:
                messages.warning(request, f"Account created, but ITMS connection note: {itms_msg}")

        # Clear session pending state & log user in
        del request.session["pending_signup"]
        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=True)

        messages.success(request, "Email verified successfully! Welcome to ITMS Verification Copilot.")
        return redirect("core:dashboard")

    return render(request, "core/signup_verify.html", {
        "email": email,
    })


def logout_view(request: HttpRequest) -> HttpResponse:
    """Signs out of operator session and redirects to login."""
    auth_service.clear_remembered_session()
    auth_logout(request)
    return redirect("core:login")


