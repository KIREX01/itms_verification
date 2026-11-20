"""
Identity rules: login email + attached company email.

- Anyone may sign in with any login email.
- TL / QI / Supervisor roles only count once a VERIFIED company email (allowed domain) is attached,
  or while the account is inside its migration grace period.
- A Team Leader's bonds must be reachable by their company email in ITMS (once ITMS access is known).
- Burned emails can never be attached, invited or registered again.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set

from django.utils import timezone

from governance.capabilities import COMPANY_EMAIL_ROLES, DEFAULT_COMPANY_DOMAINS, Role


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def allowed_company_domains() -> List[str]:
    from governance.services.flags import get_flag

    domains = get_flag("identity.company_domains", default=list(DEFAULT_COMPANY_DOMAINS))
    if isinstance(domains, str):
        domains = [domains]
    return [d.strip().lower().lstrip("@") for d in (domains or []) if str(d).strip()]


def is_company_email(email: str) -> bool:
    email = normalize_email(email)
    if "@" not in email:
        return False
    return email.rsplit("@", 1)[1] in allowed_company_domains()


def is_burned_email(email: str) -> bool:
    from governance.models import BurnedIdentity

    email = normalize_email(email)
    return bool(email) and BurnedIdentity.objects.filter(email=email).exists()


def verified_company_email(user) -> Optional[str]:
    """Returns the user's verified company email in an allowed domain, else None."""
    from governance.models import CompanyEmailLink

    link = CompanyEmailLink.objects.filter(user=user, status=CompanyEmailLink.Status.VERIFIED).first()
    if link and is_company_email(link.email) and not is_burned_email(link.email):
        return link.email
    return None


def in_company_email_grace(user) -> bool:
    from governance.models import AccountState

    grace = AccountState.objects.filter(user=user).values_list("company_email_grace_until", flat=True).first()
    return bool(grace and grace > timezone.now())


def itms_bonds_for_email(email: str) -> Optional[Set[str]]:
    """
    Bonds the company email can reach in ITMS. None means "not known yet" (never synced),
    which does not restrict; an empty set means ITMS returned no warehouses for it.
    """
    from governance.models import ItmsWarehouseAccess

    rows = ItmsWarehouseAccess.objects.filter(company_email=normalize_email(email))
    if not rows.exists():
        return None
    return set(rows.filter(active=True).values_list("bond_code", flat=True))


def membership_identity_ok(user, role: str, bond_code: str, _cache: Optional[dict] = None) -> bool:
    """Whether a membership (role @ bond) satisfies the company-email identity rules."""
    if role not in COMPANY_EMAIL_ROLES:
        return True

    cache = _cache if _cache is not None else {}
    if "company_email" not in cache:
        cache["company_email"] = verified_company_email(user)
        cache["grace"] = in_company_email_grace(user)
    company_email = cache["company_email"]

    if not company_email:
        return bool(cache["grace"])

    if role == Role.TEAM_LEADER:
        key = "itms_bonds"
        if key not in cache:
            cache[key] = itms_bonds_for_email(company_email)
        itms_bonds = cache[key]
        if itms_bonds is not None and bond_code.upper() not in itms_bonds:
            return False
    return True


def record_itms_warehouse_access(company_email: str, bond_codes: Iterable[str], warehouse_uuids: Optional[dict] = None):
    """
    Called after each ITMS warehouse sync: marks the returned bonds active for that email and
    the rest inactive (upstream removal propagates), and re-confirms the company email link.
    """
    from governance.models import CompanyEmailLink, ItmsWarehouseAccess

    email = normalize_email(company_email)
    if not email:
        return
    now = timezone.now()
    codes = {c.strip().upper() for c in bond_codes if c and str(c).strip()}
    warehouse_uuids = warehouse_uuids or {}
    for code in codes:
        obj, created = ItmsWarehouseAccess.objects.get_or_create(
            company_email=email, bond_code=code,
            defaults={"warehouse_uuid": warehouse_uuids.get(code, ""), "active": True,
                      "first_seen_at": now, "last_seen_at": now},
        )
        if not created:
            obj.active = True
            obj.last_seen_at = now
            if warehouse_uuids.get(code):
                obj.warehouse_uuid = warehouse_uuids[code]
            obj.save()
    if codes:
        ItmsWarehouseAccess.objects.filter(company_email=email).exclude(bond_code__in=codes).update(active=False)

    link = CompanyEmailLink.objects.filter(email=email).first()
    if link and link.status != CompanyEmailLink.Status.REVOKED:
        if link.status != CompanyEmailLink.Status.VERIFIED:
            link.status = CompanyEmailLink.Status.VERIFIED
            link.verified_via = CompanyEmailLink.VerifiedVia.ITMS_LOGIN
            link.verified_at = now
        link.last_itms_seen_at = now
        link.save()
    if link and link.status == CompanyEmailLink.Status.VERIFIED:
        try:
            from governance.services.memberships import sync_itms_memberships
            sync_itms_memberships(link.user)
        except Exception:
            pass


def attach_company_email(user, email: str, verified_via: str, actor=None, request=None):
    """Attaches (or replaces) a user's company email. Raises ValueError on policy violations."""
    from governance.models import CompanyEmailLink
    from governance.services import audit

    email = normalize_email(email)
    if not is_company_email(email):
        raise ValueError(f"{email} is not an approved company email ({', '.join(allowed_company_domains())}).")
    if is_burned_email(email):
        raise ValueError("This email can no longer be used.")
    other = CompanyEmailLink.objects.filter(email=email).exclude(user=user).first()
    if other:
        raise ValueError("This company email is already attached to another account.")

    link, _ = CompanyEmailLink.objects.get_or_create(user=user, defaults={"email": email})
    before = {"email": link.email, "status": link.status}
    link.email = email
    link.status = CompanyEmailLink.Status.VERIFIED
    link.verified_via = verified_via
    link.verified_at = timezone.now()
    link.save()
    audit.record(actor or user, "identity.company_email_attached", target_type="user", target_id=user.pk,
                 target_label=user.username, before=before, after={"email": email, "via": verified_via},
                 request=request)
    return link
