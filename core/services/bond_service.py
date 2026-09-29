"""
Bonded Warehouse & Operational Facility Scoping Service.

Enforces Guardrail A (Strict Scope to Bond AGM / Assigned Warehouse):
- Prevents cross-bond external orders from skewing physical safe room counts.
- Dynamically discovers bonded facilities from ITMS orders and kits.
- Manages active operating bond selection across Web Dashboard and TUI.
"""
import logging
from typing import Any, Dict, List, Optional, Set

from core.services import config_service

logger = logging.getLogger(__name__)


def get_active_bond() -> Dict[str, str]:
    """Returns currently selected operating bond facility code and name."""
    return config_service.get_active_bond()


def get_active_bond_code() -> str:
    """Returns active operating bond code (e.g. 'AGM')."""
    return config_service.get_active_bond().get("code", "AGM")


def get_active_bond_name() -> str:
    """Returns active operating bond name."""
    return config_service.get_active_bond().get("name", "AGM Bonded Warehouse")


def set_active_bond(code: str, name: Optional[str] = None) -> bool:
    """Sets active operating bond facility code and name."""
    return config_service.set_active_bond(code, name)


def get_all_discovered_warehouses() -> List[Dict[str, Any]]:
    """
    Returns unified list of bonded warehouse facilities by combining:
    1. Pre-configured standard bonds (AGM, Kampala Central, Jinja, Mbale, Mbarara).
    2. Dynamic warehouses discovered in local InstallationOrder and InstallationKit tables.
    """
    known_bonds = config_service.get_available_bonds()
    result_map: Dict[str, Dict[str, Any]] = {
        b["code"].upper(): dict(b) for b in known_bonds if "code" in b
    }

    try:
        from core.models import InstallationOrder, InstallationKit
        order_warehouses = set(
            InstallationOrder.objects.exclude(warehouse_name="")
            .values_list("warehouse_name", flat=True)
            .distinct()
        )
        kit_warehouses = set(
            InstallationKit.objects.exclude(warehouse="")
            .values_list("warehouse", flat=True)
            .distinct()
        )
        discovered_names = order_warehouses.union(kit_warehouses)

        for name in discovered_names:
            name_clean = str(name).strip()
            if not name_clean:
                continue

            # Derive uppercase code or check if already mapped
            matched_code = None
            for code, b in result_map.items():
                if code in name_clean.upper() or b["name"].upper() == name_clean.upper():
                    matched_code = code
                    break

            if not matched_code:
                # Generate slug/code
                words = name_clean.split()
                derived_code = (
                    "".join(w[0] for w in words if w.isalnum())[:8].upper()
                    or name_clean[:8].upper()
                )
                if derived_code not in result_map:
                    result_map[derived_code] = {
                        "code": derived_code,
                        "name": name_clean,
                        "warehouse_id": derived_code.lower(),
                        "is_discovered": True,
                    }
    except Exception as exc:
        logger.warning("Error discovering warehouses from database: %s", exc)

    # Ensure AGM is always at the top of the list
    all_bonds = list(result_map.values())
    all_bonds.sort(key=lambda b: (0 if b.get("code") == "AGM" else 1, b.get("name", "")))
    return all_bonds


def classify_order_bond_scope(order_or_warehouse: Any, active_code: Optional[str] = None) -> str:
    """
    Classifies an order or warehouse as 'ACTIVE_BOND' or 'CROSS_BOND_EXTERNAL'.
    If warehouse is not specified, it is treated as local/active.
    If warehouse belongs to another facility (e.g. 'Jinja' when working 'AGM'),
    it is tagged as 'CROSS_BOND_EXTERNAL' to prevent skewing AGM's floor counts.
    """
    active = active_code or get_active_bond()["code"]
    active_upper = active.strip().upper()

    wh_raw = ""
    if hasattr(order_or_warehouse, "warehouse_name"):
        wh_raw = getattr(order_or_warehouse, "warehouse_name") or ""
    elif hasattr(order_or_warehouse, "warehouse"):
        wh_raw = getattr(order_or_warehouse, "warehouse") or ""
    elif isinstance(order_or_warehouse, str):
        wh_raw = order_or_warehouse
    elif isinstance(order_or_warehouse, dict):
        wh_raw = order_or_warehouse.get("warehouse_name") or order_or_warehouse.get("warehouse") or ""

    wh = str(wh_raw).strip().upper()
    if not wh:
        # Default / unspecified orders are assumed part of active shift
        return "ACTIVE_BOND"

    # Exact or substring match for active bond code (e.g. AGM)
    if active_upper in wh:
        return "ACTIVE_BOND"

    active_name = get_active_bond()["name"].strip().upper()
    if active_name and (active_name in wh or wh in active_name):
        return "ACTIVE_BOND"

    return "CROSS_BOND_EXTERNAL"
