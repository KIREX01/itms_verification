"""
CLOSE Copilot Backoffice: the administrator's control centre.

Every view requires the platform administrator. Every change goes through a governance
service (which writes the audit log); views only parse input and render. Dangerous changes
(burn, suspend, role changes, kill switches) require re-entering the administrator password.
"""
from __future__ import annotations

import functools
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from governance.capabilities import Role
from governance.models import (
    AccessRequest, AccountState, AdminAuditLog, BondMembership, BurnedIdentity, CompanyEmailLink,
    FeatureFlag, InstallRegistration, ItmsWarehouseAccess,
)
from governance.services import accounts, audit, authz, flags, identity, memberships, onboarding, switches


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def admin_required(view):
    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs):
        # The central server is the only control centre: installed apps have no backoffice.
        if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
            raise Http404("The backoffice is only available on the central server.")
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return redirect(f"/login/?next={request.path}")
        if not authz.is_platform_admin(user):
            return render(request, "governance/no_access.html",
                          {"message": "The backoffice is for the administrator only.", "code": "ADMIN_REQUIRED"},
                          status=403)
        if request.method == "POST" and _is_managed_edge():
            messages.error(request, "This install is managed by the central server. Make changes in the central "
                                    "backoffice; they arrive here on the next check-in.")
            return redirect(request.path)
        return view(request, *args, **kwargs)
    return wrapper


def _is_managed_edge() -> bool:
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central":
        return False
    from governance.services.policy_sync import is_enrolled
    return is_enrolled()


def _step_up_ok(request: HttpRequest) -> bool:
    """Dangerous actions re-check the administrator's password."""
    pw = request.POST.get("confirm_password", "")
    if pw and request.user.check_password(pw):
        return True
    messages.error(request, "Confirm with your password to perform this action.")
    return False


def _ctx(request, active, **extra):
    pending = AccessRequest.objects.filter(status=AccessRequest.Status.PENDING).count()
    try:
        from core.models import QualityFlag
        open_flags = QualityFlag.objects.filter(status=QualityFlag.Status.OPEN).count()
    except Exception:
        open_flags = 0
    from governance.services.policy_sync import load_enrollment
    base = {
        "managed_edge": _is_managed_edge(),
        "enrollment": load_enrollment() if _is_managed_edge() else {},
        "active": active,
        "nav_pending_requests": pending,
        "nav_open_flags": open_flags,
        "deployment_role": getattr(settings, "DEPLOYMENT_ROLE", "edge"),
    }
    base.update(extra)
    return base


def _bond_names():
    names = {}
    try:
        from core.models import Warehouse
        names = {c: n for c, n in Warehouse.objects.values_list("code", "name") if c}
    except Exception:
        pass
    return names


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@admin_required
def overview(request: HttpRequest) -> HttpResponse:
    from core.models import QualityFlag, VehicleInstallationPair

    since = timezone.now() - timedelta(days=1)
    people = User.objects.filter(is_superuser=False)
    states = dict(AccountState.objects.values_list("status").annotate(n=Count("id")))
    ctx = _ctx(
        request, "overview",
        stats={
            "people": people.count(),
            "active": states.get("ACTIVE", 0),
            "suspended": states.get("SUSPENDED", 0),
            "burned": states.get("BURNED", 0),
            "pending_requests": AccessRequest.objects.filter(status="PENDING").count(),
            "open_flags": QualityFlag.objects.filter(status="OPEN").count(),
            "submitted_24h": VehicleInstallationPair.objects.filter(
                verification_status="SUBMITTED", updated_at__gte=since).count(),
            "failed_24h": VehicleInstallationPair.objects.filter(
                verification_status="FAILED", updated_at__gte=since).count(),
        },
        grace_users=User.objects.filter(
            account_state__company_email_grace_until__isnull=False,
            account_state__status="ACTIVE",
            bond_memberships__status="ACTIVE",
            bond_memberships__role__in=["TEAM_LEADER", "QUALITY_INSPECTOR", "SUPERVISOR"],
        ).exclude(company_email__status="VERIFIED").distinct()[:20],
        switches=switches.all_switches(),
        recent=AdminAuditLog.objects.select_related("actor")[:12],
        clones=InstallRegistration.objects.filter(clone_detected_at__isnull=False).order_by("-clone_detected_at")[:10],
    )
    return render(request, "backoffice/overview.html", ctx)


# ---------------------------------------------------------------------------
# Access requests (onboarding)
# ---------------------------------------------------------------------------
@admin_required
def access_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "PENDING").upper()
    qs = AccessRequest.objects.select_related("invited_by", "reviewed_by")
    if status != "ALL":
        qs = qs.filter(status=status)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "backoffice/access_list.html", _ctx(
        request, "access", page=page, status=status, statuses=["PENDING", "GRANTED", "USED", "REJECTED", "EXPIRED", "ALL"],
    ))


@admin_required
@require_http_methods(["GET", "POST"])
def access_detail(request: HttpRequest, pk: int) -> HttpResponse:
    req = get_object_or_404(AccessRequest, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "grant":
                _, _token, _code = onboarding.grant(
                    req, request.user,
                    role=request.POST.get("role") or None,
                    bond_code=request.POST.get("bond_code") or None,
                    company_email=request.POST.get("company_email", req.company_email),
                    note=request.POST.get("note", ""), request=request,
                )
                messages.success(request, f"Access granted. An invite was emailed to {req.email}.")
            elif action == "reject":
                onboarding.reject(req, request.user, request.POST.get("note", ""), request=request)
                messages.success(request, "Request rejected.")
            elif action == "reissue":
                onboarding.reissue_invite(req, request.user, request=request)
                messages.success(request, f"A new invite was emailed to {req.email}.")
            else:
                messages.error(request, "Unknown action.")
        except (onboarding.OnboardingError, authz.CapabilityDenied) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:access_detail", pk=req.pk)

    existing = User.objects.filter(email__iexact=req.email).first()
    return render(request, "backoffice/access_detail.html", _ctx(
        request, "access", req=req, roles=[(r.value, r.label) for r in Role],
        bonds=sorted(_bond_names().items()), existing_user=existing,
        burned=identity.is_burned_email(req.email),
    ))


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
@admin_required
def people_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").upper()
    bond = (request.GET.get("bond") or "").upper()
    role = (request.GET.get("role") or "").upper()
    users = User.objects.select_related("account_state").prefetch_related("bond_memberships")
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                             | Q(last_name__icontains=q) | Q(company_email__email__icontains=q))
    if status:
        users = users.filter(account_state__status=status)
    if bond:
        users = users.filter(bond_memberships__bond_code=bond, bond_memberships__status="ACTIVE")
    if role:
        users = users.filter(bond_memberships__role=role, bond_memberships__status="ACTIVE")
    users = users.distinct().order_by("username")
    page = Paginator(users, 50).get_page(request.GET.get("page"))
    company = dict(CompanyEmailLink.objects.values_list("user_id", "email"))
    rows = []
    for u in page:
        roles = [m for m in u.bond_memberships.all() if m.status == "ACTIVE"]
        rows.append({"user": u, "roles": roles, "company_email": company.get(u.pk, ""),
                     "status": getattr(getattr(u, "account_state", None), "status", "ACTIVE")})
    return render(request, "backoffice/people_list.html", _ctx(
        request, "people", page=page, rows=rows, q=q, status=status, bond=bond, role=role,
        roles=[(r.value, r.label) for r in Role], bonds=sorted(_bond_names().items()),
    ))


@admin_required
@require_http_methods(["GET", "POST"])
def person_detail(request: HttpRequest, pk: int) -> HttpResponse:
    person = get_object_or_404(User, pk=pk)
    state = accounts.get_state(person)
    if request.method == "POST":
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "").strip()
        dangerous = {"suspend", "burn", "assign_role", "remove_role", "reinstate", "attach_company_email",
                     "revoke_company_email", "force_logout"}
        if action in dangerous and not _step_up_ok(request):
            return redirect("backoffice:person_detail", pk=pk)
        try:
            if action == "suspend":
                accounts.suspend(person, reason or "Suspended by administrator", actor=request.user, request=request)
                messages.success(request, f"{person.username} is suspended and signed out everywhere.")
            elif action == "reinstate":
                accounts.reinstate(person, reason, actor=request.user, request=request)
                messages.success(request, f"{person.username} is reinstated.")
            elif action == "burn":
                if request.POST.get("confirm_username", "") != person.username:
                    messages.error(request, "Type the username exactly to confirm the burn.")
                    return redirect("backoffice:person_detail", pk=pk)
                accounts.burn(person, reason, actor=request.user, request=request)
                messages.success(request, f"{person.username} is burned. This cannot be undone.")
            elif action == "force_logout":
                n = accounts.kill_sessions(person)
                audit.record(request.user, "account.force_logout", target_type="user", target_id=person.pk,
                             target_label=person.username, after={"sessions_killed": n}, request=request)
                messages.success(request, f"Signed out {n} session(s).")
            elif action == "assign_role":
                memberships.assign_role(person, request.POST.get("bond_code", ""), request.POST.get("role", ""),
                                        actor=request.user, reason=reason, request=request)
                messages.success(request, "Role assigned.")
            elif action == "remove_role":
                m = get_object_or_404(BondMembership, pk=request.POST.get("membership_id"), user=person)
                memberships.remove_role(m, actor=request.user, reason=reason, request=request)
                messages.success(request, "Role removed.")
            elif action == "attach_company_email":
                identity.attach_company_email(person, request.POST.get("company_email", ""),
                                              CompanyEmailLink.VerifiedVia.ADMIN_GRANT,
                                              actor=request.user, request=request)
                messages.success(request, "Company email attached and verified.")
            elif action == "revoke_company_email":
                link = CompanyEmailLink.objects.filter(user=person).first()
                if link:
                    before = {"email": link.email, "status": link.status}
                    link.status = CompanyEmailLink.Status.REVOKED
                    link.save()
                    audit.record(request.user, "identity.company_email_revoked", target_type="user",
                                 target_id=person.pk, target_label=person.username, before=before,
                                 after={"status": link.status}, reason=reason, request=request)
                    messages.success(request, "Company email revoked; company roles are inactive until a new one is attached.")
            else:
                messages.error(request, "Unknown action.")
        except (accounts.AccountLifecycleError, memberships.MembershipError, ValueError,
                authz.CapabilityDenied) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:person_detail", pk=pk)

    from core.models import AuthorizedDevice
    company = CompanyEmailLink.objects.filter(user=person).first()
    return render(request, "backoffice/person_detail.html", _ctx(
        request, "people",
        person=person, state=state, company=company,
        memberships=BondMembership.objects.filter(user=person).order_by("status", "bond_code"),
        warnings=memberships.membership_warnings(person),
        itms_access=ItmsWarehouseAccess.objects.filter(company_email=company.email) if company else [],
        devices=AuthorizedDevice.objects.filter(Q(user=person) | Q(authorized_email__iexact=person.email or "-"))[:30],
        history=AdminAuditLog.objects.filter(target_type="user", target_id=str(person.pk))[:30],
        roles=[(r.value, r.label) for r in Role], bonds=sorted(_bond_names().items()),
        effective=authz.capability_summary(person),
    ))


# ---------------------------------------------------------------------------
# Bonds
# ---------------------------------------------------------------------------
@admin_required
def bonds(request: HttpRequest) -> HttpResponse:
    from core.models import QualityFlag, VehicleInstallationPair

    names = _bond_names()
    codes = set(names) | set(BondMembership.objects.values_list("bond_code", flat=True))
    rows = []
    for code in sorted(c for c in codes if c):
        members = BondMembership.objects.filter(bond_code=code, status="ACTIVE").select_related("user")
        by_role = {}
        for m in members:
            by_role.setdefault(m.get_role_display(), []).append(m.user)
        rows.append({
            "code": code, "name": names.get(code, code), "by_role": by_role,
            "open_flags": QualityFlag.objects.filter(bond_code=code, status="OPEN").count(),
            "pending_review": VehicleInstallationPair.objects.filter(
                facility_code=code, verification_status__in=["PENDING_REVIEW", "CONFLICT"]).count(),
            "itms_emails": list(ItmsWarehouseAccess.objects.filter(bond_code=code, active=True)
                                .values_list("company_email", flat=True)),
        })
    return render(request, "backoffice/bonds.html", _ctx(request, "bonds", rows=rows))


# ---------------------------------------------------------------------------
# Kill switches & policy flags
# ---------------------------------------------------------------------------
EDITABLE_POLICY_FLAGS = ["identity.company_domains", "identity.company_email_grace_days",
                         "onboarding.invite_valid_hours", "onboarding.max_code_attempts",
                         "sync.policy_offline_grace_hours"]


@admin_required
@require_http_methods(["GET", "POST"])
def controls(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        if not _step_up_ok(request):
            return redirect("backoffice:controls")
        action = request.POST.get("action")
        reason = request.POST.get("reason", "").strip()
        try:
            if action == "switch":
                key = request.POST.get("key", "")
                value = request.POST.get("value") == "on"
                extra = {"message": request.POST.get("maintenance_message", ""),
                         "title": request.POST.get("maintenance_title", "")}
                switches.set_switch(key, value, actor=request.user, reason=reason, request=request, extra=extra)
                messages.success(request, f"{switches.SWITCHES[key]['label']} is now {'ON' if value else 'OFF'}.")
            elif action == "policy":
                key = request.POST.get("key", "")
                if key not in EDITABLE_POLICY_FLAGS:
                    raise ValueError("That setting can't be edited here.")
                raw = request.POST.get("value", "").strip()
                default = flags.FLAG_REGISTRY[key][0]
                if isinstance(default, list):
                    value = [v.strip().lower().lstrip("@") for v in raw.split(",") if v.strip()]
                    if not value:
                        raise ValueError("Enter at least one value.")
                else:
                    value = int(raw)
                    if value <= 0:
                        raise ValueError("Enter a positive number.")
                flags.set_flag(key, value, actor=request.user, reason=reason, request=request)
                messages.success(request, "Policy updated.")
        except (ValueError, KeyError) as exc:
            messages.error(request, str(exc))
        return redirect("backoffice:controls")

    policy = [{"key": k, "value": flags.get_flag(k), "help": flags.FLAG_REGISTRY[k][1],
               "is_list": isinstance(flags.FLAG_REGISTRY[k][0], list)} for k in EDITABLE_POLICY_FLAGS]
    from core.services import config_service
    return render(request, "backoffice/controls.html", _ctx(
        request, "controls", switches=switches.all_switches(), policy=policy,
        maintenance=config_service.get_maintenance_config(),
        flag_rows=FeatureFlag.objects.select_related("updated_by")[:100],
    ))


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
@admin_required
@require_http_methods(["GET", "POST"])
def devices(request: HttpRequest) -> HttpResponse:
    from core.models import AuthorizedDevice
    from core.services import device_access_service

    if request.method == "POST":
        action = request.POST.get("action")
        target = request.POST.get("target", "").strip()
        reason = request.POST.get("reason", "").strip() or "Blocked from backoffice"
        if action == "block":
            ok, msg, _ = device_access_service.block_device_or_network_target(target, reason=reason, admin_user=request.user)
        elif action == "unblock":
            ok, msg, *_ = device_access_service.unblock_device_or_target(target)
        else:
            ok, msg = False, "Unknown action."
        audit.record(request.user, f"device.{action}", target_type="device", target_id=target,
                     target_label=target, reason=reason, after={"ok": ok, "message": msg}, request=request)
        (messages.success if ok else messages.error)(request, msg)
        return redirect("backoffice:devices")

    status = (request.GET.get("status") or "").upper()
    qs = AuthorizedDevice.objects.select_related("user").order_by("-last_seen_at")
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "backoffice/devices.html", _ctx(
        request, "devices", page=page, status=status,
        statuses=["", "APPROVED", "PENDING", "BLOCKED", "REVOKED", "EXPIRED"],
    ))


# ---------------------------------------------------------------------------
# Reports (cross-bond)
# ---------------------------------------------------------------------------
@admin_required
def reports(request: HttpRequest) -> HttpResponse:
    from core.models import QualityFlag, VehicleInstallationPair

    try:
        days = max(1, min(90, int(request.GET.get("days", 1))))
    except ValueError:
        days = 1
    since = timezone.now() - timedelta(days=days)
    pairs = VehicleInstallationPair.objects.filter(updated_at__gte=since)
    by_bond = (pairs.values("facility_code", "verification_status").annotate(n=Count("id"))
               .order_by("facility_code"))
    table = {}
    for row in by_bond:
        b = row["facility_code"] or "-"
        table.setdefault(b, {})[row["verification_status"]] = row["n"]
    flags_by_bond = dict(QualityFlag.objects.filter(raised_at__gte=since).values_list("bond_code")
                         .annotate(n=Count("id")))
    statuses = ["PENDING_REVIEW", "CONFLICT", "APPROVED", "SUBMITTED", "FAILED"]
    rows = [{"bond": b, "counts": [table[b].get(s, 0) for s in statuses], "flags": flags_by_bond.get(b, 0)}
            for b in sorted(table)]
    return render(request, "backoffice/reports.html", _ctx(
        request, "reports", rows=rows, statuses=statuses, days=days,
    ))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@admin_required
def audit_log(request: HttpRequest) -> HttpResponse:
    qs = AdminAuditLog.objects.select_related("actor")
    action = (request.GET.get("action") or "").strip()
    who = (request.GET.get("who") or "").strip()
    if action:
        qs = qs.filter(action__startswith=action)
    if who:
        qs = qs.filter(Q(actor_label__icontains=who) | Q(target_label__icontains=who))
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    actions = sorted(set(AdminAuditLog.objects.values_list("action", flat=True)))
    return render(request, "backoffice/audit.html", _ctx(
        request, "audit", page=page, action=action, who=who, actions=actions,
    ))


# ---------------------------------------------------------------------------
# Technical
# ---------------------------------------------------------------------------
@admin_required
@require_http_methods(["GET", "POST"])
def technical(request: HttpRequest) -> HttpResponse:
    from core.services import config_service, maintenance_service
    from core.version import get_version_info

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "optimize_db":
            result = maintenance_service.optimize_database()
        elif action == "clean_storage":
            result = maintenance_service.clean_storage_lifecycle()
        elif action == "check_updates":
            from core.services import update_service
            result = update_service.check_for_updates(force=True)
        else:
            result = {"error": "Unknown action."}
        audit.record(request.user, f"technical.{action}", target_type="system", target_label=action,
                     after={"result": str(result)[:500]}, request=request)
        messages.info(request, f"{action}: {str(result)[:300]}")
        return redirect("backoffice:technical")

    db = config_service.get_active_database_info()
    return render(request, "backoffice/technical.html", _ctx(
        request, "technical", version=get_version_info(), db=db,
        burned=BurnedIdentity.objects.count(),
        governance_enforced=authz.enforcement_enabled(),
    ))


# ---------------------------------------------------------------------------
# Installs (central policy sync)
# ---------------------------------------------------------------------------
@admin_required
@require_http_methods(["GET", "POST"])
def installs(request: HttpRequest) -> HttpResponse:
    from governance.models import InstallRegistration
    from governance.services import policy_sync

    new_code = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            inst, code = policy_sync.create_install(request.POST.get("name", ""), request.user, request=request)
            new_code = {"name": inst.name, "code": code}
            messages.success(request, f"Install '{inst.name}' created. Enroll it within 24 hours.")
        else:
            if not _step_up_ok(request):
                return redirect("backoffice:installs")
            inst = get_object_or_404(InstallRegistration, pk=request.POST.get("install_pk"))
            reason = request.POST.get("reason", "").strip()
            before = {"status": inst.status, "locked": inst.locked}
            if action == "lock":
                inst.locked, inst.lock_reason = True, reason or "Locked by administrator"
            elif action == "unlock":
                inst.locked, inst.lock_reason = False, ""
            elif action == "revoke":
                inst.status, inst.secret = InstallRegistration.Status.REVOKED, ""
            inst.save()
            audit.record(request.user, f"install.{action}", target_type="install", target_id=inst.install_id,
                         target_label=inst.name, before=before, after={"status": inst.status, "locked": inst.locked},
                         reason=reason, request=request)
            messages.success(request, f"{inst.name}: {action} done. It takes effect on the next check-in.")
        if new_code is None:
            return redirect("backoffice:installs")

    now = timezone.now()
    rows = []
    for inst in InstallRegistration.objects.all():
        age = (now - inst.last_seen_at).total_seconds() if inst.last_seen_at else None
        rows.append({"inst": inst, "online": age is not None and age < 180,
                     "behind": inst.status == "ACTIVE" and inst.policy_version_acked < policy_sync.policy_version()})
    return render(request, "backoffice/installs.html", _ctx(
        request, "installs", rows=rows, new_code=new_code, policy_version=policy_sync.policy_version(),
        central_url=request.build_absolute_uri("/").rstrip("/"),
    ))

