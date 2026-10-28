from __future__ import annotations

import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

import json
from django.db.models import Q
from core.models import AuthorizedDevice, EmailAccessRequest
from core.services import auth_service, device_access_service, device_service, email_service

logger = logging.getLogger(__name__)

SIGNUP_VERIFY_TEMPLATE = "core/signup_verify.html"


def _redirect_after_auth(request: HttpRequest, next_url: str = "") -> HttpResponse:
    """
    Directs authenticated operators to their appropriate workspace:
    - If explicit target next_url is set, follows that destination.
    - If opening from a mobile phone, opens the touch-optimized Mobile Capture Station.
    - If opening from a laptop/desktop, opens the Supervisor Console.
    """
    if next_url and next_url != "/":
        return redirect(next_url)

    # Device-aware routing
    if device_service.is_mobile_device(request):
        return redirect("core:mobile_companion")
    return redirect("core:dashboard")


def _resolve_safe_next_url(request: HttpRequest) -> str:
    raw_next = request.GET.get("next") or request.POST.get("next") or ""
    if raw_next and url_has_allowed_host_and_scheme(raw_next, allowed_hosts={request.get_host()}):
        return raw_next
    return "/"


def login_view(request: HttpRequest) -> HttpResponse:
    """Operator Sign In view."""
    next_url = _resolve_safe_next_url(request)

    if request.user.is_authenticated:
        return _redirect_after_auth(request, next_url)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        remember = bool(request.POST.get("remember"))

        user, err = auth_service.authenticate_operator(username, password)
        if user is None:
            # Check if account exists but is inactive (pending email/device approval)
            inactive_user = User.objects.filter(username=username, is_active=False).first()
            if inactive_user:
                ident = device_access_service.extract_device_identity(request)
                return render(request, "core/access_request.html", {
                    "blocked_code": "EMAIL_PENDING_APPROVAL",
                    "blocked_reason": f"Account '{username}' ({inactive_user.email}) is awaiting administrator activation.",
                    "device_identity": ident,
                    "prefill_email": inactive_user.email,
                })
            return render(request, "core/login.html", {
                "error": err,
                "last_username": username,
                "next_url": next_url,
            })

        dev, is_approved, dev_msg = device_access_service.register_or_refresh_authorized_device(
            request,
            user=user,
            linked_via=AuthorizedDevice.LinkedVia.DIRECT_LOGIN,
        )
        if not is_approved:
            ident = device_access_service.extract_device_identity(request)
            return render(request, "core/access_request.html", {
                "blocked_code": "DEVICE_PENDING_APPROVAL",
                "blocked_reason": dev_msg or "This device requires administrator approval before signing in.",
                "device_identity": ident,
                "device_obj": dev,
                "prefill_email": user.email,
            }, status=403)

        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=remember)
        resp = _redirect_after_auth(request, next_url)
        return device_access_service.attach_device_trust_cookie(resp, dev.device_id, user.email or user.username)

    # If no users exist in the active database yet, prompt first-time onboarding
    if User.objects.count() == 0:
        return redirect("core:signup")

    prefs = auth_service.get_operator_preferences()
    last_username = prefs.get("last_username", "")

    return render(request, "core/login.html", {
        "next_url": next_url,
        "last_username": last_username,
    })


def _validate_signup_fields(username: str, email: str, password: str, password_confirm: str) -> Optional[str]:
    """Validates operator registration credentials."""
    if not username:
        return "Username is required."
    if not email:
        return "A valid work email is required for two-factor authentication."
    if not password:
        return "Password is required."
    if password != password_confirm:
        return "Passwords do not match."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    return None


def signup_view(request: HttpRequest) -> HttpResponse:
    """Operator Registration / Onboarding view with 2FA email verification."""
    if request.user.is_authenticated:
        return _redirect_after_auth(request)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        password_confirm = request.POST.get("password_confirm", "").strip()

        err_msg = _validate_signup_fields(username, email, password, password_confirm)
        if err_msg:
            return render(request, "core/signup.html", {
                "error": err_msg,
                "form_data": request.POST,
            })

        # Initiate 2FA Email Verification
        enable_2fa = getattr(settings, "SIGNUP_2FA_ENABLED", True)
        if enable_2fa:
            code = email_service.generate_2fa_code()
            request.session["pending_signup"] = {
                "username": username,
                "full_name": full_name,
                "email": email,
                "password": password,
                "code": code,
                "expires_at": time.time() + 600,  # 10 minutes
                "last_sent_at": time.time(),
            }
            request.session.modified = True

            logo_url = request.build_absolute_uri(static("core/logos/android-chrome-192x192.png"))
            verify_url = request.build_absolute_uri(reverse("core:signup_verify")) + f"?code={code}"
            ok, msg = email_service.send_2fa_verification_code(email, code, username=username, verify_url=verify_url, logo_url=logo_url)
            if not ok:
                logger.warning("Failed to send 2FA email on signup: %s", msg)
                messages.warning(request, f"Email delivery notice: {msg}")

            return redirect("core:signup_verify")

        # Direct creation if 2FA is explicitly disabled
        preapproved = device_access_service.is_email_preapproved_or_active(email)
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

        if not preapproved and not user.is_superuser:
            user.is_active = False
            user.save(update_fields=["is_active"])
            device_access_service.create_or_update_access_request(
                request=request,
                email=email,
                full_name=full_name,
                username=username,
                reason="Registered via /signup/ while strict email approval mode is active.",
                email_verified=False,
                status=EmailAccessRequest.Status.PENDING,
            )
            ident = device_access_service.extract_device_identity(request)
            AuthorizedDevice.objects.update_or_create(
                device_id=ident["device_id"],
                defaults={
                    "user": user,
                    "authorized_email": email.lower(),
                    "device_name": ident["device_name"],
                    "device_type": ident["device_type"],
                    "linked_via": AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
                    "ip_address": ident["client_ip"],
                    "mac_address": ident["mac_address"],
                    "fingerprint_hash": ident["fingerprint_hash"],
                    "user_agent": ident["user_agent"],
                    "status": AuthorizedDevice.Status.PENDING,
                },
            )
            messages.warning(
                request,
                "Your account has been created and is awaiting administrator email & device approval.",
            )
            return redirect("core:access_request")

        dev, _, _ = device_access_service.register_or_refresh_authorized_device(
            request,
            user=user,
            linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
            force_approve=True,
        )
        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=True)
        resp = _redirect_after_auth(request)
        device_access_service.attach_device_trust_cookie(resp, dev.device_id, email or user.username)
        return resp

    return render(request, "core/signup.html", {"form_data": {}})


def _process_signup_resend(request: HttpRequest, pending: dict, email: str, username: str) -> HttpResponse:
    """Handles rate-limited resending of the 2FA registration code."""
    last_sent = pending.get("last_sent_at", 0)
    now = time.time()
    if now - last_sent < 30:
        wait_sec = int(30 - (now - last_sent))
        return render(request, SIGNUP_VERIFY_TEMPLATE, {
            "email": email,
            "error": f"Please wait {wait_sec} seconds before requesting a new code.",
        })

    new_code = email_service.generate_2fa_code()
    pending["code"] = new_code
    pending["expires_at"] = now + 600
    pending["last_sent_at"] = now
    request.session["pending_signup"] = pending
    request.session.modified = True

    logo_url = request.build_absolute_uri(static("core/logos/android-chrome-192x192.png"))
    verify_url = request.build_absolute_uri(reverse("core:signup_verify")) + f"?code={new_code}"
    ok, msg = email_service.send_2fa_verification_code(email, new_code, username=username, verify_url=verify_url, logo_url=logo_url)
    return render(request, SIGNUP_VERIFY_TEMPLATE, {
        "email": email,
        "message": "A new 6-digit security code has been sent to your email.",
        "error": "" if ok else msg,
    })


def _validate_submitted_otp(submitted_code: str, pending: dict) -> Optional[str]:
    """Validates expiry and matches of submitted verification code."""
    expected_code = str(pending.get("code", "")).strip()
    expires_at = pending.get("expires_at", 0)

    if time.time() > expires_at:
        return "This verification code has expired. Please click 'Resend Code'."
    if submitted_code != expected_code:
        return "Invalid verification code. Please check your inbox or spam folder."
    return None


def signup_verify_view(request: HttpRequest) -> HttpResponse:
    """Verifies the 6-digit email 2FA code before activating operator account."""
    if request.user.is_authenticated:
        return _redirect_after_auth(request)

    pending = request.session.get("pending_signup")
    if not pending:
        messages.error(request, "No pending registration found. Please sign up below.")
        return redirect("core:signup")

    email = pending.get("email", "")
    username = pending.get("username", "")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "resend":
            return _process_signup_resend(request, pending, email, username)

        submitted_code = request.POST.get("code", "").strip()
        otp_err = _validate_submitted_otp(submitted_code, pending)
        if otp_err:
            return render(request, SIGNUP_VERIFY_TEMPLATE, {
                "email": email,
                "error": otp_err,
            })

        # Code is valid! Check if email is pre-approved or if Strict Approval Mode holds new signups
        is_preapproved = device_access_service.is_email_preapproved_or_active(email)
        user, err = auth_service.create_operator_account(
            username=pending["username"],
            password=pending["password"],
            full_name=pending.get("full_name", ""),
            email=pending.get("email", ""),
        )

        if user is None:
            return render(request, SIGNUP_VERIFY_TEMPLATE, {
                "email": email,
                "error": err,
            })

        del request.session["pending_signup"]

        if not is_preapproved and not user.is_superuser:
            user.is_active = False
            user.save(update_fields=["is_active"])
            req_obj = device_access_service.create_or_update_access_request(
                request=request,
                email=email,
                username=user.username,
                full_name=pending.get("full_name", ""),
                reason="New operator signup verified via 2FA OTP; awaiting administrator activation.",
                email_verified=True,
                status=EmailAccessRequest.Status.PENDING,
            )
            ident = device_access_service.extract_device_identity(request)
            AuthorizedDevice.objects.update_or_create(
                device_id=ident["device_id"],
                defaults={
                    "user": user,
                    "authorized_email": email.lower(),
                    "device_name": ident["device_name"],
                    "device_type": ident["device_type"],
                    "linked_via": AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
                    "ip_address": ident["client_ip"],
                    "mac_address": ident["mac_address"],
                    "fingerprint_hash": ident["fingerprint_hash"],
                    "user_agent": ident["user_agent"],
                    "status": AuthorizedDevice.Status.PENDING,
                },
            )
            admin_emails = list(
                User.objects.filter(Q(is_superuser=True) | Q(is_staff=True), is_active=True)
                .exclude(email="")
                .values_list("email", flat=True)
            )
            approve_url = request.build_absolute_uri(reverse("core:access_review_token", args=[req_obj.activation_token]))
            email_service.send_access_request_admin_alert(
                admin_emails=admin_emails,
                requester_email=email,
                requester_name=f"{pending.get('full_name', '')} ({user.username})".strip(),
                client_ip=ident["client_ip"],
                mac_address=ident["mac_address"],
                device_name=ident["device_name"],
                activation_code=req_obj.activation_code,
                approve_url=approve_url,
            )
            return render(request, "core/access_request.html", {
                "blocked_code": "EMAIL_PENDING_APPROVAL",
                "blocked_reason": f"Your email ({email}) has been verified! Your account and device are now queued for administrator activation.",
                "device_identity": ident,
                "prefill_email": email,
                "request_submitted": True,
            })

        # Pre-approved or standard mode: mark EmailAccessRequest & AuthorizedDevice APPROVED and log in
        device_access_service.create_or_update_access_request(
            request=request,
            email=email,
            username=user.username,
            full_name=pending.get("full_name", ""),
            email_verified=True,
            status=EmailAccessRequest.Status.APPROVED,
        )
        dev, _, _ = device_access_service.register_or_refresh_authorized_device(
            request,
            user=user,
            linked_via=AuthorizedDevice.LinkedVia.EMAIL_ACTIVATION,
            force_approve=True,
        )
        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=True)

        messages.success(request, "Email verified successfully! Welcome to ITMS Verification Copilot.")
        resp = _redirect_after_auth(request)
        return device_access_service.attach_device_trust_cookie(resp, dev.device_id, email or user.username)

    initial_code = request.GET.get("code", "").strip()
    return render(request, SIGNUP_VERIFY_TEMPLATE, {
        "email": email,
        "initial_code": initial_code,
    })


def logout_view(request: HttpRequest) -> HttpResponse:
    """Signs out of operator session and redirects to login."""
    auth_service.clear_remembered_session()
    auth_logout(request)
    return redirect("core:login")


def access_request_view(request: HttpRequest) -> HttpResponse:
    """
    Self-service Email & Device Access Request and Activation Portal.
    Reachable even when a device or IP is blocked or pending approval so legitimate
    operators can request activation for their email/device or unlock with a 6-digit code.
    """
    ident = device_access_service.extract_device_identity(request)
    message = ""
    error = ""
    request_submitted = False

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        full_name = (request.POST.get("full_name") or "").strip()
        username = (request.POST.get("username") or "").strip()
        facility_code = (request.POST.get("facility_code") or "AGM").strip().upper()
        requested_role = (request.POST.get("requested_role") or "OPERATOR").strip().upper()
        reason = (request.POST.get("reason") or "").strip()

        if not email or "@" not in email:
            error = "A valid operator email address is required to request activation."
        else:
            req_obj = device_access_service.create_or_update_access_request(
                request=request,
                email=email,
                username=username,
                full_name=full_name,
                facility_code=facility_code,
                requested_role=requested_role,
                reason=reason or "Self-service device/email access request",
                status=EmailAccessRequest.Status.PENDING,
            )
            admin_emails = list(
                User.objects.filter(Q(is_superuser=True) | Q(is_staff=True), is_active=True)
                .exclude(email="")
                .values_list("email", flat=True)
            )
            approve_url = request.build_absolute_uri(reverse("core:access_review_token", args=[req_obj.activation_token]))
            email_service.send_access_request_admin_alert(
                admin_emails=admin_emails,
                requester_email=email,
                requester_name=full_name or username or email,
                client_ip=ident["client_ip"],
                mac_address=ident["mac_address"],
                device_name=ident["device_name"],
                activation_code=req_obj.activation_code,
                approve_url=approve_url,
            )
            request_submitted = True
            message = f"Activation request submitted for {email}! An administrator has been notified."

    return render(request, "core/access_request.html", {
        "device_identity": ident,
        "message": message,
        "error": error,
        "request_submitted": request_submitted,
        "prefill_email": request.GET.get("email", ""),
    })


@require_POST
def access_activate_view(request: HttpRequest) -> HttpResponse:
    """Unlocks a device and activates an email using a 6-digit activation code."""
    email = (request.POST.get("email") or "").strip().lower()
    code = (request.POST.get("activation_code") or "").strip()
    ident = device_access_service.extract_device_identity(request)

    ok, msg, dev = device_access_service.activate_with_code(request, email=email, activation_code=code)
    if not ok or dev is None:
        return render(request, "core/access_request.html", {
            "device_identity": ident,
            "error": msg,
            "prefill_email": email,
        }, status=400)

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user:
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        auth_service.save_remembered_session(user, remember=True)
        resp = _redirect_after_auth(request)
    else:
        messages.success(request, msg)
        resp = redirect("core:login")

    return device_access_service.attach_device_trust_cookie(resp, dev.device_id, email)


@require_GET
def access_review_token_view(request: HttpRequest, token: str) -> HttpResponse:
    """One-click email approval endpoint for administrators."""
    action = (request.GET.get("action") or "approve").strip().lower()
    req_obj = EmailAccessRequest.objects.filter(activation_token=token).first()
    if not req_obj:
        return HttpResponse("Invalid or expired access review token.", status=404, content_type="text/plain")

    admin_user = request.user if (getattr(request, "user", None) and request.user.is_authenticated) else None
    if action == "block":
        req_obj.status = EmailAccessRequest.Status.BLOCKED
        req_obj.save(update_fields=["status", "updated_at"])
        if req_obj.device_id or req_obj.client_ip:
            device_access_service.block_device_or_network_target(
                req_obj.device_id or req_obj.client_ip,
                reason=f"Blocked during access review ({req_obj.email})",
                admin_user=admin_user,
            )
        messages.warning(request, f"Blocked access request and device for {req_obj.email}.")
    else:
        ok, msg, _ = device_access_service.approve_access_request(token, admin_user=admin_user)
        if ok:
            messages.success(request, msg)
        else:
            messages.error(request, msg)

    if admin_user:
        return redirect("core:dashboard")
    return redirect("core:login")


@require_GET
def api_security_overview(request: HttpRequest) -> JsonResponse:
    """Returns device registry, email access requests, and security configuration for the Settings UI."""
    if not (getattr(request, "user", None) and request.user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)

    sec_cfg = device_access_service.get_security_access_config()
    current_ident = device_access_service.extract_device_identity(request)

    devices_qs = AuthorizedDevice.objects.select_related("user", "approved_by").order_by("-last_seen_at")[:250]
    devices_list = [
        {
            "id": d.id,
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "linked_via": d.linked_via,
            "username": d.user.username if d.user else "",
            "email": d.authorized_email or (d.user.email if d.user else ""),
            "ip_address": d.ip_address,
            "pinned_ip_cidr": d.pinned_ip_cidr,
            "mac_address": d.mac_address,
            "status": d.status,
            "block_reason": d.block_reason,
            "is_current_device": (d.device_id == current_ident.get("device_id")),
            "last_seen_at": d.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if d.last_seen_at else "",
        }
        for d in devices_qs
    ]

    requests_qs = EmailAccessRequest.objects.order_by("-created_at")[:150]
    requests_list = [
        {
            "id": r.id,
            "email": r.email,
            "username": r.username,
            "full_name": r.full_name,
            "facility_code": r.facility_code,
            "requested_role": r.requested_role,
            "reason": r.reason,
            "device_id": r.device_id,
            "mac_address": r.mac_address,
            "client_ip": r.client_ip,
            "device_name": r.device_name,
            "email_verified": r.email_verified,
            "status": r.status,
            "activation_code": r.activation_code,
            "max_devices_allowed": r.max_devices_allowed,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        }
        for r in requests_qs
    ]

    return JsonResponse({
        "success": True,
        "config": sec_cfg,
        "current_device": current_ident,
        "devices": devices_list,
        "access_requests": requests_list,
        "summary": {
            "approved_devices": sum(1 for d in devices_list if d["status"] == "APPROVED"),
            "blocked_devices": sum(1 for d in devices_list if d["status"] in ("BLOCKED", "REVOKED")),
            "pending_requests": sum(1 for r in requests_list if r["status"] == "PENDING"),
            "preapproved_emails": sum(1 for r in requests_list if r["status"] in ("PRE_APPROVED", "APPROVED")),
        },
    })


@require_POST
def api_security_action(request: HttpRequest) -> JsonResponse:
    """
    Executes administrator security actions:
    - set_mode: Toggle STANDARD vs STRICT mode and max_devices_per_account
    - preapprove_email: Add email to pre-approved list
    - approve_request: Approve a pending EmailAccessRequest & unlock device
    - reject_request: Reject a pending EmailAccessRequest
    - block_target: Block an IP, CIDR, MAC address, or Device ID
    - unblock_target: Unblock & approve a device/IP/MAC
    - pin_ip: Pin an AuthorizedDevice to a specific IP or CIDR subnet
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)

    body: dict = {}
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            body = {}
    action = (body.get("action") or request.POST.get("action") or "").strip().lower()
    target = str(
        body.get("target")
        or body.get("request_id")
        or request.POST.get("target")
        or request.POST.get("request_id")
        or ""
    ).strip()

    if action in ("set_mode", "update_config"):
        mode = body.get("mode") or request.POST.get("mode")
        req_approval = body.get("require_email_approval")
        max_devs = body.get("max_devices_per_account") or request.POST.get("max_devices_per_account")
        cfg = device_access_service.update_security_access_config(
            mode=mode,
            require_email_approval=req_approval if req_approval is not None else None,
            max_devices_per_account=int(max_devs) if max_devs else None,
        )
        return JsonResponse({"success": True, "message": f"Updated security mode to {cfg['mode']} (Max {cfg['max_devices_per_account']} devices/account).", "config": cfg})

    if action == "preapprove_email":
        email = (body.get("email") or request.POST.get("email") or target).strip().lower()
        if not email or "@" not in email:
            return JsonResponse({"success": False, "error": "Valid email address required."}, status=400)
        facility = (body.get("facility_code") or request.POST.get("facility_code") or "AGM").strip().upper()
        role = (body.get("requested_role") or request.POST.get("requested_role") or "OPERATOR").strip().upper()
        max_devs = int(body.get("max_devices_allowed") or request.POST.get("max_devices_allowed") or 4)
        req_obj = device_access_service.preapprove_operator_email(
            email=email,
            facility_code=facility,
            requested_role=role,
            max_devices_allowed=max_devs,
            admin_user=user,
        )
        return JsonResponse({
            "success": True,
            "message": f"Pre-approved {email} (Activation Code: {req_obj.activation_code}, Max {req_obj.max_devices_allowed} devices).",
            "activation_code": req_obj.activation_code,
        })

    if action == "approve_request":
        ok, msg, req_obj = device_access_service.approve_access_request(target, admin_user=user)
        return JsonResponse({"success": ok, "message": msg, "error": "" if ok else msg}, status=200 if ok else 400)

    if action == "reject_request":
        req_obj = EmailAccessRequest.objects.filter(pk=int(target)).first() if target.isdigit() else EmailAccessRequest.objects.filter(email__iexact=target).first()
        if not req_obj:
            return JsonResponse({"success": False, "error": "Request not found."}, status=404)
        req_obj.status = EmailAccessRequest.Status.REJECTED
        req_obj.reviewed_by = user
        req_obj.reviewed_at = timezone.now()
        req_obj.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        return JsonResponse({"success": True, "message": f"Rejected access request for {req_obj.email}."})

    if action == "block_target":
        reason = (body.get("reason") or request.POST.get("reason") or "Blocked by administrator").strip()
        ok, msg, _ = device_access_service.block_device_or_network_target(target, reason=reason, admin_user=user)
        return JsonResponse({"success": ok, "message": msg, "error": "" if ok else msg}, status=200 if ok else 400)

    if action in ("unblock_target", "approve_device"):
        ok, msg = device_access_service.unblock_device_or_target(target, admin_user=user)
        return JsonResponse({"success": ok, "message": msg, "error": "" if ok else msg}, status=200 if ok else 400)

    if action == "pin_ip":
        cidr = (body.get("pinned_ip_cidr") or request.POST.get("pinned_ip_cidr") or "").strip()
        dev = AuthorizedDevice.objects.filter(pk=int(target)).first() if target.isdigit() else AuthorizedDevice.objects.filter(device_id=target).first()
        if not dev:
            return JsonResponse({"success": False, "error": "Device not found."}, status=404)
        dev.pinned_ip_cidr = cidr
        dev.save(update_fields=["pinned_ip_cidr"])
        return JsonResponse({"success": True, "message": f"Updated IP restriction on {dev.device_name} to '{cidr or 'Any (Roaming Allowed)'}'."})

    return JsonResponse({"success": False, "error": f"Unknown security action '{action}'."}, status=400)

