"""
Authentication & Operator Onboarding views.
"""
import logging

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from core.services import auth_service

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
    """Operator Registration / Onboarding view with optional ITMS account linking."""
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

        if password != password_confirm:
            return render(request, "core/signup.html", {
                "error": "Passwords do not match.",
                "form_data": request.POST,
            })

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

        if connect_itms and itms_email and itms_password:
            ok, itms_msg = auth_service.connect_itms_account(itms_email, itms_password)
            if ok:
                messages.success(request, f"Connected to ITMS WebApp as '{itms_email}'.")
            else:
                messages.warning(request, f"Account created, but ITMS connection note: {itms_msg}")

        return redirect("core:dashboard")

    return render(request, "core/signup.html", {"form_data": {}})


def logout_view(request: HttpRequest) -> HttpResponse:
    """Signs out of operator session and redirects to login."""
    auth_service.clear_remembered_session()
    auth_logout(request)
    return redirect("core:login")


