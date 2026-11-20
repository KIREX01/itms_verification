"""
Authorization: the one place that decides whether a person may do something at a bond.

    has_capability(user, CAP, bond_code=None)  -> bool
    require(user, CAP, bond_code=None)          -> raises CapabilityDenied
    bonds_for(user, CAP)                        -> set of bond codes
    capabilities_at(user, bond_code)            -> frozenset of capabilities

Rules:
  * Signed-out, inactive, suspended or burned accounts have no capabilities.
  * Capabilities come from ACTIVE BondMemberships that also satisfy the identity rules
    (company email for TL/QI/Supervisor; TL bonds within the company email's ITMS access).
  * The platform administrator (superuser) passes every check (break-glass); on the central
    server the operator UI sends administrators to the backoffice.
  * When enforcement is off (settings.GOVERNANCE_ENFORCE=False or flag governance.enforce=False)
    any signed-in active account is allowed (legacy behaviour, used only during rollout/tests).
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional, Set

from django.conf import settings
from django.core.exceptions import PermissionDenied

from governance.capabilities import ALL_CAPABILITIES

ANY_MEMBER = "__any_member__"  # pseudo-capability: holds at least one effective role anywhere


class CapabilityDenied(PermissionDenied):
    def __init__(self, capability: str, bond_code: str = "", message: str = ""):
        self.capability = capability
        self.bond_code = bond_code
        super().__init__(message or f"You don't have permission for this action ({capability}"
                         + (f" at {bond_code}" if bond_code else "") + ").")


def _is_authenticated(user) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_active", False))


def account_status(user) -> str:
    from governance.models import AccountState

    if not getattr(user, "pk", None):
        return AccountState.Status.ACTIVE
    status = AccountState.objects.filter(user_id=user.pk).values_list("status", flat=True).first()
    return status or AccountState.Status.ACTIVE


def account_ok(user) -> bool:
    from governance.models import AccountState

    return _is_authenticated(user) and account_status(user) == AccountState.Status.ACTIVE


def is_platform_admin(user) -> bool:
    return account_ok(user) and bool(getattr(user, "is_superuser", False))


def enforcement_enabled() -> bool:
    if not getattr(settings, "GOVERNANCE_ENFORCE", True):
        return False
    from governance.services.flags import is_enabled

    return is_enabled("governance.enforce", default=True)


def effective_memberships(user) -> List:
    """ACTIVE memberships that satisfy identity rules, for an account in good standing."""
    from governance.models import BondMembership
    from governance.services.identity import membership_identity_ok

    if not account_ok(user):
        return []
    cache: Dict = {}
    rows = list(BondMembership.objects.filter(user_id=user.pk, status=BondMembership.Status.ACTIVE))
    return [m for m in rows if membership_identity_ok(user, m.role, m.bond_code, cache)]


def roles_by_bond(user) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for m in effective_memberships(user):
        out.setdefault(m.bond_code, set()).add(m.role)
    return out


def capabilities_at(user, bond_code: str) -> FrozenSet[str]:
    if is_platform_admin(user):
        return ALL_CAPABILITIES
    from governance.services.groups import role_capabilities
    role_caps = role_capabilities()
    roles = roles_by_bond(user).get((bond_code or "").upper(), set())
    caps: Set[str] = set()
    for role in roles:
        caps |= role_caps.get(role, frozenset())
    return frozenset(caps)


def bonds_for(user, capability: str) -> Set[str]:
    """All bonds where the user holds `capability` (or any role, for ANY_MEMBER)."""
    from governance.services.groups import role_capabilities
    role_caps = role_capabilities()
    out: Set[str] = set()
    for bond, roles in roles_by_bond(user).items():
        if capability == ANY_MEMBER:
            out.add(bond)
            continue
        for role in roles:
            if capability in role_caps.get(role, frozenset()):
                out.add(bond)
                break
    return out


def active_bond_code(user) -> str:
    """The bond the user is currently working at: their selected bond if they hold a role there,
    else the configured bond if they hold a role there, else their first bond."""
    selected = ""
    try:
        from core.models import OperatorProfile

        selected = (OperatorProfile.objects.filter(user_id=user.pk).values_list("active_facility", flat=True).first() or "")
    except Exception:
        selected = ""
    configured = ""
    try:
        from core.services import config_service

        configured = (config_service.get_setting("bond.active_bond_code", "") or "")
    except Exception:
        configured = ""

    member_bonds = sorted(roles_by_bond(user).keys()) if getattr(user, "pk", None) else []
    for candidate in (selected, configured):
        if candidate and candidate.strip().upper() in member_bonds:
            return candidate.strip().upper()
    if member_bonds:
        return member_bonds[0]
    return (selected or configured or "").strip().upper()


def has_capability(user, capability: str, bond_code: Optional[str] = None) -> bool:
    if not _is_authenticated(user):
        return False
    if not enforcement_enabled():
        return True
    if not account_ok(user):
        return False
    if is_platform_admin(user):
        return True
    if capability == ANY_MEMBER:
        return bool(roles_by_bond(user))
    bond = (bond_code or active_bond_code(user) or "").upper()
    return capability in capabilities_at(user, bond)


def has_any(user, capabilities: Iterable[str], bond_code: Optional[str] = None) -> bool:
    return any(has_capability(user, c, bond_code) for c in capabilities)


def require(user, capability: str, bond_code: Optional[str] = None) -> None:
    if not has_capability(user, capability, bond_code):
        raise CapabilityDenied(capability, (bond_code or "").upper())


def capability_summary(user) -> Dict:
    """What the UI needs to render: roles per bond, capabilities at the active bond."""
    active = active_bond_code(user) if _is_authenticated(user) else ""
    by_bond = roles_by_bond(user) if _is_authenticated(user) else {}
    if not enforcement_enabled() and _is_authenticated(user):
        caps = sorted(ALL_CAPABILITIES)
    else:
        caps = sorted(capabilities_at(user, active)) if _is_authenticated(user) else []
    return {
        "enforced": enforcement_enabled(),
        "is_platform_admin": is_platform_admin(user),
        "active_bond": active,
        "bonds": {b: sorted(r) for b, r in sorted(by_bond.items())},
        "capabilities": caps,
        "account_status": account_status(user) if _is_authenticated(user) else "",
    }
