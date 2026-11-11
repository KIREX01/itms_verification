"""
Sibling Plate Collision Detection Service.

Identifies potential character ambiguity and sibling series collisions when
vehicles share identical numeric digits (e.g., UMA 496 QV vs UMA 496 QW) across:
- Active InstallationOrders awaiting verification or submission.
- StockDispatchScans issued to the floor for the shift.
- Active/unsubmitted VehicleInstallationPairs in review/queue.

When an operator links an order or edits a plate, this service warns the operator
and requires explicit confirmation (confirm_sibling=True) before linking.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from django.db.models import Q

from core.models import InstallationOrder, StockDispatchScan, VehicleInstallationPair
from core.vision import normalizer

logger = logging.getLogger(__name__)


def extract_plate_numeric_core(plate: str) -> str:
    """
    Extracts the primary 3 or 4-digit sequence from a plate string.
    e.g. 'UMA496QV' -> '496', 'UG1234A' -> '1234'.
    """
    if not plate:
        return ""
    clean = normalizer.canonicalize(plate) or plate.strip().upper()
    # Find 3-4 consecutive digits
    digits = re.findall(r"\d{3,4}", clean)
    if digits:
        return digits[0]
    # Fallback to any digits >= 2
    digits = re.findall(r"\d{2,}", clean)
    return digits[0] if digits else ""


def detect_sibling_collision(
    plate: str,
    account_email: Optional[str] = None,
    exclude_order_id: Optional[int] = None,
    exclude_pair_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Inspects active orders, dispatch scans, and active pairs for sibling plates
    that share the same numeric digits as `plate`.

    Returns:
        {
            "has_sibling_collision": bool,
            "target_plate": str,
            "numeric_digits": str,
            "sibling_plates": List[str],
            "sibling_orders": List[Dict[str, Any]],
            "warning": str,
            "requires_confirmation": bool,
            "confirmation_type": "SIBLING_PLATE_COLLISION",
        }
    """
    target_clean = normalizer.canonicalize(plate)
    if not target_clean:
        return {
            "has_sibling_collision": False,
            "target_plate": plate,
            "numeric_digits": "",
            "sibling_plates": [],
            "sibling_orders": [],
            "warning": "",
            "requires_confirmation": False,
            "confirmation_type": "SIBLING_PLATE_COLLISION",
        }

    num_core = extract_plate_numeric_core(target_clean)
    if not num_core or len(num_core) < 2:
        return {
            "has_sibling_collision": False,
            "target_plate": target_clean,
            "numeric_digits": "",
            "sibling_plates": [],
            "sibling_orders": [],
            "warning": "",
            "requires_confirmation": False,
            "confirmation_type": "SIBLING_PLATE_COLLISION",
        }

    sibling_plates_set: Set[str] = set()
    sibling_orders_info: List[Dict[str, Any]] = []

    # 1. Search Active Orders
    orders_qs = InstallationOrder.objects.filter(
        registration_number__icontains=num_core,
    ).exclude(
        Q(status=InstallationOrder.Status.INSTALLED) | Q(is_archived=True) | Q(order_status__iexact="Installed")
    )
    if account_email:
        orders_qs = orders_qs.filter(
            Q(account_email__iexact=account_email) | Q(account_email="") | Q(account_email__isnull=True)
        )
    if exclude_order_id:
        orders_qs = orders_qs.exclude(id=exclude_order_id)

    # Regex pattern to match exact numeric segment bounded by non-digits
    num_pattern = re.compile(rf"(?<!\d){re.escape(num_core)}(?!\d)")

    for o in orders_qs[:50]:
        o_clean = normalizer.canonicalize(o.registration_number)
        if o_clean and o_clean != target_clean and num_pattern.search(o_clean):
            sibling_plates_set.add(o_clean)
            sibling_orders_info.append({
                "order_id": o.id,
                "order_number": o.order_number,
                "registration_number": o.registration_number,
                "status": o.status,
                "chassis": o.vin,
            })

    # 2. Search Dispatches from current/recent shifts
    dispatches_qs = StockDispatchScan.objects.filter(
        registration_number__icontains=num_core,
    )
    for s in dispatches_qs[:50]:
        s_clean = normalizer.canonicalize(s.registration_number)
        if s_clean and s_clean != target_clean and num_pattern.search(s_clean):
            sibling_plates_set.add(s_clean)

    # 3. Search Active/Review Pairs
    pairs_qs = VehicleInstallationPair.objects.filter(
        is_deleted=False,
        registration_number_detected__icontains=num_core,
    ).exclude(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
    if exclude_pair_id:
        pairs_qs = pairs_qs.exclude(id=exclude_pair_id)

    for p in pairs_qs[:50]:
        p_clean = normalizer.canonicalize(p.registration_number_detected)
        if p_clean and p_clean != target_clean and num_pattern.search(p_clean):
            sibling_plates_set.add(p_clean)

    sorted_siblings = sorted(sibling_plates_set)
    has_collision = len(sorted_siblings) > 0

    warning_msg = ""
    if has_collision:
        siblings_str = ", ".join(sorted_siblings)
        warning_msg = (
            f"Sibling series collision detected! The numeric sequence '{num_core}' is shared by "
            f"sibling plate(s): {siblings_str}. Please verify that the vehicle photo and physical plate "
            f"strictly match '{target_clean}' before confirming."
        )

    return {
        "has_sibling_collision": has_collision,
        "target_plate": target_clean,
        "numeric_digits": num_core,
        "sibling_plates": sorted_siblings,
        "sibling_orders": sibling_orders_info,
        "warning": warning_msg,
        "requires_confirmation": has_collision,
        "confirmation_type": "SIBLING_PLATE_COLLISION",
    }
