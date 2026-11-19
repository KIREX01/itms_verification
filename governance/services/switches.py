"""
Kill switches & safety modes, controlled from the backoffice.

The central FeatureFlag row is the source of truth (it is what policy sync pushes to installs);
`apply_locally` writes the value into this machine's runtime config so it takes effect now.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from governance.services import flags

# key -> metadata. "config_key" is the local config.json key the running app reads.
SWITCHES: Dict[str, Dict[str, Any]] = {
    "itms.enabled": {
        "label": "ITMS master switch",
        "help": "Off = nothing is sent to or fetched from the ITMS portal; work is held locally.",
        "default": True, "danger_when": False,
    },
    "maintenance.enabled": {
        "label": "Maintenance mode",
        "help": "On = everyone except the administrator sees the maintenance page.",
        "default": False, "danger_when": True,
    },
    "submission.dry_run_mode": {
        "label": "Dry run (simulate submissions)",
        "help": "On = submissions are simulated and never reach ITMS.",
        "default": True, "danger_when": None,
    },
    "sync.auto_sync_enabled": {
        "label": "Automatic ITMS sync",
        "help": "Background order / kit synchronisation daemons.",
        "default": True, "danger_when": None,
    },
    "outbox.enabled": {
        "label": "Offline outbox",
        "help": "Queue submissions while offline and send them automatically later.",
        "default": True, "danger_when": None,
    },
}


def current_value(key: str) -> bool:
    """Effective value: central flag if set, else this machine's config."""
    from core.services import config_service

    meta = SWITCHES[key]
    central = flags.get_flag(f"switch.{key}", default=None)
    if central is not None and not isinstance(central, dict):
        return bool(central)
    if key == "maintenance.enabled":
        return bool(config_service.is_maintenance_enabled())
    return bool(config_service.get_setting(key, default=meta["default"]))


def apply_locally(key: str, value: bool, actor_label: str = "", reason: str = "", extra: Optional[dict] = None) -> None:
    from core.services import config_service

    extra = extra or {}
    if key == "itms.enabled":
        config_service.set_itms_enabled(bool(value), paused_by=actor_label, reason=reason)
    elif key == "maintenance.enabled":
        config_service.set_maintenance_mode(
            enabled=bool(value),
            title=extra.get("title", ""),
            message=extra.get("message", ""),
            estimated_duration=extra.get("estimated_duration", ""),
        )
    else:
        config_service.set_setting(key, bool(value))
        if key == "submission.dry_run_mode":
            try:
                from django.conf import settings
                settings.ITMS_WEB_DRY_RUN = bool(value)
            except Exception:
                pass


def set_switch(key: str, value: bool, actor, reason: str = "", request=None, extra: Optional[dict] = None) -> None:
    if key not in SWITCHES:
        raise ValueError(f"Unknown switch {key}")
    flags.set_flag(f"switch.{key}", bool(value), actor=actor, reason=reason, request=request)
    apply_locally(key, bool(value), actor_label=getattr(actor, "username", "admin"), reason=reason, extra=extra)


def all_switches() -> List[Dict[str, Any]]:
    out = []
    for key, meta in SWITCHES.items():
        value = current_value(key)
        out.append({
            "key": key, "label": meta["label"], "help": meta["help"], "value": value,
            "danger": meta["danger_when"] is not None and value == meta["danger_when"],
        })
    return out
