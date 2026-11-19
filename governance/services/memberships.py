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
