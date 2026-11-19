"""Operator-facing governance endpoints (capabilities for UI rendering)."""
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from governance.services import authz


@require_GET
def api_me_capabilities(request: HttpRequest) -> JsonResponse:
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
    summary = authz.capability_summary(user)
    summary.update({"success": True, "username": user.username})
    return JsonResponse(summary)


def _body(request: HttpRequest) -> dict:
    import json
    if request.content_type == "application/json" and request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
    return request.POST.dict()


def api_me_profile(request: HttpRequest) -> JsonResponse:
    """The signed-in person's roles, company email status and grace period."""
    from governance.models import CompanyEmailLink
    from governance.services import identity, memberships

    user = request.user
    if not (user and user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
    link = CompanyEmailLink.objects.filter(user=user).first()
    grace = getattr(getattr(user, "account_state", None), "company_email_grace_until", None)
    return JsonResponse({
        "success": True,
        "username": user.username,
        "email": user.email,
        "summary": authz.capability_summary(user),
        "company_email": link.email if link else "",
        "company_email_status": link.status if link else "",
        "company_domains": identity.allowed_company_domains(),
        "grace_until": grace.isoformat() if grace else "",
        "warnings": memberships.membership_warnings(user),
    })


def api_team(request: HttpRequest) -> JsonResponse:
    """Team Leader: people at my bond and my pending invites (GET); invite a Helper / QI (POST)."""
    from governance.capabilities import TEAM_INVITE, TL_INVITABLE_ROLES, Role
    from governance.models import AccessRequest, BondMembership
    from governance.services import onboarding

    user = request.user
    bond = authz.active_bond_code(user)
    if request.method == "POST":
        from django.conf import settings
        if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
            central = getattr(settings, "CLOSE_CENTRAL_URL", "https://close.kirex.online").rstrip("/")
            return JsonResponse({"success": False, "code": "USE_CENTRAL",
                                 "error": f"Send invites from the central server: {central}"}, status=409)
        data = _body(request)
        try:
            req = onboarding.invite_by_team_leader(
                user, data.get("full_name", ""), data.get("email", ""), data.get("role", ""),
                company_email=data.get("company_email", ""), phone=data.get("phone", ""),
                message=data.get("message", ""), bond_code=bond, request=request,
            )
        except authz.CapabilityDenied as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=403)
        except onboarding.OnboardingError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)
        return JsonResponse({"success": True, "message": f"Invite for {req.email} sent to the administrator for approval."})

    if not authz.has_capability(user, TEAM_INVITE, bond):
        return JsonResponse({"success": False, "error": "Team Leaders only."}, status=403)
    members = BondMembership.objects.filter(bond_code=bond, status="ACTIVE").select_related("user")
    pending = AccessRequest.objects.filter(bond_code=bond, invited_by=user).exclude(status="USED")[:50]
    return JsonResponse({
        "success": True,
        "bond": bond,
        "members": [{"name": m.user.get_full_name() or m.user.username, "username": m.user.username,
                     "role": m.get_role_display()} for m in members],
        "invites": [{"email": r.email, "name": r.full_name, "role": r.get_requested_role_display(),
                     "status": r.get_status_display(), "created_at": r.created_at.isoformat()} for r in pending],
        "invitable_roles": [{"value": r, "label": Role(r).label} for r in sorted(TL_INVITABLE_ROLES)],
    })


def api_me_device_info(request: HttpRequest) -> JsonResponse:
    """Browser-side device report (screen, time zone, CPU, GPU, ...) for the device making the request."""
    import json
    from core.models import AuthorizedDevice
    from core.services import device_access_service, device_info_service

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required."}, status=405)
    user = request.user
    if not (user and user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"success": False, "error": "Invalid payload."}, status=400)
    ident = device_access_service.extract_device_identity(request)
    device = AuthorizedDevice.objects.filter(device_id=ident["device_id"]).first()
    if device is None:
        return JsonResponse({"success": False, "error": "Unknown device."}, status=404)
    clean = device_info_service.record_client_report(device, payload)
    return JsonResponse({"success": True, "recorded": sorted(clean)})

