"""
Central feature flags & kill switches.

Flags live in governance.FeatureFlag (edited only from the backoffice and synced to installs).
A bond-specific row overrides the global row. Unknown keys fall back to the registered default.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from django.db.utils import OperationalError, ProgrammingError

# key -> (default value, description)
FLAG_REGISTRY: Dict[str, tuple] = {
    "governance.enforce": (True, "Enforce role/capability checks (False = legacy: any signed-in user may act)."),
    "identity.company_domains": (["itms-ug.com"], "Company email domains accepted for TL / QI / Supervisor."),
    "identity.company_email_grace_days": (14, "Days existing users have to attach a company email after upgrade."),
    "onboarding.invite_valid_hours": (72, "Hours an access grant invite (code + link) stays valid."),
    "onboarding.max_code_attempts": (5, "Wrong signup codes allowed before an invite is locked."),
    "sync.policy_offline_grace_hours": (24, "Hours an install may run on cached policy without reaching the central server."),
}


def _lookup(key: str, bond_code: str) -> Optional[Any]:
    from governance.models import FeatureFlag

    try:
        if bond_code:
            row = FeatureFlag.objects.filter(key=key, bond_code=bond_code.upper()).values_list("value", flat=True).first()
            if row is not None:
                return row
        return FeatureFlag.objects.filter(key=key, bond_code="").values_list("value", flat=True).first()
    except (OperationalError, ProgrammingError):
        # Table not migrated yet (first boot / during migrate): use defaults.
        return None


def get_flag(key: str, bond_code: str = "", default: Any = None) -> Any:
    value = _lookup(key, bond_code or "")
    if value is not None:
        return value
    if default is not None:
        return default
    return FLAG_REGISTRY.get(key, (None, ""))[0]


def is_enabled(key: str, bond_code: str = "", default: bool = False) -> bool:
    return bool(get_flag(key, bond_code, default))


def set_flag(key: str, value: Any, actor=None, bond_code: str = "", reason: str = "", request=None):
    from governance.models import FeatureFlag
    from governance.services import audit

    bond_code = (bond_code or "").strip().upper()
    row, _ = FeatureFlag.objects.get_or_create(
        key=key, bond_code=bond_code,
        defaults={"value": value, "description": FLAG_REGISTRY.get(key, (None, ""))[1]},
    )
    before = row.value
    row.value = value
    row.updated_by = actor if getattr(actor, "pk", None) else None
    row.save()
    audit.record(
        actor, "flag.set", target_type="flag", target_id=key, target_label=key,
        bond_code=bond_code, before={"value": before}, after={"value": value}, reason=reason, request=request,
    )
    return row
