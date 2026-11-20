"""Bond role assignments (administrator only)."""
from __future__ import annotations

from typing import List

from governance.capabilities import COMPANY_EMAIL_ROLES, Role
from governance.models import BondMembership
from governance.services import audit, authz, identity


class MembershipError(ValueError):
    pass


def _require_admin(actor) -> None:
    if not authz.is_platform_admin(actor):
        raise authz.CapabilityDenied("platform.roles")


def assign_role(user, bond_code: str, role: str, actor, reason: str = "", request=None) -> BondMembership:
    _require_admin(actor)
    bond = (bond_code or "").strip().upper()
    if role not in Role.values:
        raise MembershipError("Unknown role.")
    if not bond:
        raise MembershipError("Choose a bond.")
    if identity.is_burned_email(user.email or ""):
        raise MembershipError("This account is burned.")
    m, created = BondMembership.objects.get_or_create(
        user=user, bond_code=bond, role=role,
        defaults={"status": BondMembership.Status.ACTIVE, "granted_by": actor, "status_reason": reason},
    )
    before = {} if created else {"status": m.status}
    if not created:
        m.status = BondMembership.Status.ACTIVE
        m.granted_by = actor
        m.status_reason = reason
        m.save()
    audit.record(actor, "role.assign", target_type="user", target_id=user.pk, target_label=user.username,
                 bond_code=bond, before=before, after={"role": role, "status": m.status}, reason=reason,
                 request=request)
    return m


def remove_role(membership: BondMembership, actor, reason: str = "", request=None) -> BondMembership:
    _require_admin(actor)
    before = {"status": membership.status}
    membership.status = BondMembership.Status.REMOVED
    membership.status_reason = reason or "Removed by administrator"
    membership.save()
    audit.record(actor, "role.remove", target_type="user", target_id=membership.user_id,
                 target_label=membership.user.username, bond_code=membership.bond_code, before=before,
                 after={"role": membership.role, "status": membership.status}, reason=reason, request=request)
    return membership


def set_roles(user, bond_code: str, roles, actor, reason: str = "", request=None):
    """
    Role switch: make the person's active roles at one bond exactly `roles` (adds missing, removes the rest).
    Their Django groups follow automatically. Returns (added, removed) role lists.
    """
    _require_admin(actor)
    bond = (bond_code or "").strip().upper()
    if not bond:
        raise MembershipError("Choose a bond.")
    wanted = {r for r in roles if r in Role.values}
    if len(wanted) != len(set(roles)):
        raise MembershipError("Unknown role.")
    current = {m.role: m for m in BondMembership.objects.filter(user=user, bond_code=bond,
                                                                 status=BondMembership.Status.ACTIVE)}
    added = [r for r in Role.values if r in wanted and r not in current]
    removed = [r for r in Role.values if r in current and r not in wanted]
    for role in added:
        assign_role(user, bond, role, actor, reason=reason, request=request)
    for role in removed:
        remove_role(current[role], actor, reason=reason or "Changed with the role switch", request=request)
    return added, removed


def membership_warnings(user) -> List[str]:
    """Human-readable reasons why some of a user's roles are not effective yet."""
    warnings = []
    active = BondMembership.objects.filter(user=user, status=BondMembership.Status.ACTIVE)
    company = identity.verified_company_email(user)
    grace = identity.in_company_email_grace(user)
    for m in active:
        if m.role in COMPANY_EMAIL_ROLES and not company:
            warnings.append(
                f"{m.get_role_display()} @ {m.bond_code}: needs a verified company email"
                + (" (currently in grace period)." if grace else " - role is inactive until attached.")
            )
        if m.role == Role.TEAM_LEADER and company:
            bonds = identity.itms_bonds_for_email(company)
            if bonds is not None and m.bond_code not in bonds:
                warnings.append(f"Team Leader @ {m.bond_code}: {company} has no ITMS access to this bond.")
    return warnings


# ---------------------------------------------------------------------------
# Person-level roles; bonds come from ITMS warehouse access
# ---------------------------------------------------------------------------
ROLE_ORDER = [Role.HELPER, Role.QUALITY_INSPECTOR, Role.TEAM_LEADER, Role.SUPERVISOR]
ITMS_REASON = "From ITMS warehouse access"


def assigned_roles(user) -> List[str]:
    from governance.models import AccountState
    roles = AccountState.objects.filter(user=user).values_list("assigned_roles", flat=True).first() or []
    return [r for r in ROLE_ORDER if r in roles]


def itms_bonds(user) -> List[str]:
    """Bonds the person's verified company email can reach in ITMS (active rows only)."""
    email = identity.verified_company_email(user)
    if not email:
        return []
    from governance.models import ItmsWarehouseAccess
    return sorted(set(ItmsWarehouseAccess.objects.filter(company_email=email, active=True)
                      .values_list("bond_code", flat=True)))


def set_person_roles(user, roles, actor, reason: str = "", request=None) -> List[str]:
    """Administrator assigns the person's roles. Bonds follow from ITMS (plus any manually added bonds)."""
    from governance.models import AccountState

    _require_admin(actor)
    wanted = [r for r in ROLE_ORDER if r in set(roles)]
    if len(wanted) != len(set(roles)):
        raise MembershipError("Unknown role.")
    if identity.is_burned_email(user.email or ""):
        raise MembershipError("This account is burned.")
    state, _ = AccountState.objects.get_or_create(user=user)
    before = [r for r in ROLE_ORDER if r in (state.assigned_roles or [])]
    state.assigned_roles = wanted
    state.save(update_fields=["assigned_roles"])
    # Roles taken away end everywhere (ITMS and manual bonds).
    BondMembership.objects.filter(user=user, status=BondMembership.Status.ACTIVE).exclude(role__in=wanted) \
        .update(status=BondMembership.Status.REMOVED, status_reason=reason or "Role removed by administrator")
    # Manual bonds get the new roles too.
    manual_bonds = set(BondMembership.objects.filter(user=user, source="MANUAL").values_list("bond_code", flat=True))
    for bond in manual_bonds:
        for role in wanted:
            m, created = BondMembership.objects.get_or_create(
                user=user, bond_code=bond, role=role,
                defaults={"status": BondMembership.Status.ACTIVE, "source": "MANUAL", "granted_by": actor})
            if not created and m.status != BondMembership.Status.ACTIVE and m.source == "MANUAL":
                m.status, m.status_reason = BondMembership.Status.ACTIVE, ""
                m.save(update_fields=["status", "status_reason", "updated_at"])
    sync_itms_memberships(user)
    audit.record(actor, "role.person_roles", target_type="user", target_id=user.pk, target_label=user.username,
                 before={"roles": before}, after={"roles": wanted, "itms_bonds": itms_bonds(user)},
                 reason=reason, request=request)
    _touch_groups(user)
    return wanted


def add_manual_bond(user, bond_code: str, actor, request=None) -> List[str]:
    """For people without ITMS (e.g. Helpers): give their assigned roles at a bond chosen by the administrator."""
    _require_admin(actor)
    bond = (bond_code or "").strip().upper()
    if not bond:
        raise MembershipError("Choose a bond.")
    roles = assigned_roles(user)
    if not roles:
        raise MembershipError("Assign a role first.")
    for role in roles:
        m, created = BondMembership.objects.get_or_create(
            user=user, bond_code=bond, role=role,
            defaults={"status": BondMembership.Status.ACTIVE, "source": "MANUAL", "granted_by": actor})
        if not created:
            m.status, m.source, m.status_reason = BondMembership.Status.ACTIVE, "MANUAL", ""
            m.save(update_fields=["status", "source", "status_reason", "updated_at"])
    audit.record(actor, "role.manual_bond_added", target_type="user", target_id=user.pk,
                 target_label=user.username, bond_code=bond, after={"roles": roles}, request=request)
    _touch_groups(user)
    return roles


def remove_manual_bond(user, bond_code: str, actor, request=None) -> None:
    _require_admin(actor)
    bond = (bond_code or "").strip().upper()
    BondMembership.objects.filter(user=user, bond_code=bond, source="MANUAL",
                                  status=BondMembership.Status.ACTIVE).update(
        status=BondMembership.Status.REMOVED, status_reason="Bond removed by administrator")
    audit.record(actor, "role.manual_bond_removed", target_type="user", target_id=user.pk,
                 target_label=user.username, bond_code=bond, request=request)
    sync_itms_memberships(user)
    _touch_groups(user)


def sync_itms_memberships(user) -> dict:
    """
    Role x ITMS bond memberships: added for bonds ITMS now shows, removed for bonds ITMS no longer shows.
    Runs after role changes, company email changes and every ITMS warehouse sync.
    """
    roles = assigned_roles(user)
    bonds = set(itms_bonds(user))
    added = removed = 0
    for role in roles:
        for bond in bonds:
            m, created = BondMembership.objects.get_or_create(
                user=user, bond_code=bond, role=role,
                defaults={"status": BondMembership.Status.ACTIVE, "source": "ITMS", "status_reason": ITMS_REASON})
            if created:
                added += 1
            elif m.status != BondMembership.Status.ACTIVE and m.source == "ITMS":
                m.status, m.status_reason = BondMembership.Status.ACTIVE, ITMS_REASON
                m.save(update_fields=["status", "status_reason", "updated_at"])
                added += 1
    stale = BondMembership.objects.filter(user=user, source="ITMS", status=BondMembership.Status.ACTIVE) \
        .exclude(bond_code__in=bonds)
    removed = stale.update(status=BondMembership.Status.REMOVED, status_reason="No longer in ITMS warehouse access")
    if added or removed:
        audit.record("system", "role.itms_sync", target_type="user", target_id=user.pk, target_label=user.username,
                     after={"added": added, "removed": removed, "bonds": sorted(bonds)}, actor_label="itms-sync")
        _touch_groups(user)
    return {"added": added, "removed": removed}


def _touch_groups(user) -> None:
    """QuerySet.update() skips signals, so refresh Django groups and bump policy explicitly."""
    try:
        from governance.services.groups import sync_user_groups
        sync_user_groups(user)
        from governance.services.policy_sync import bump_policy_version
        bump_policy_version()
    except Exception:
        pass
