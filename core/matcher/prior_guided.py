"""
Prior-guided plate hypothesis disambiguation.

Leverages the closed-set bounded registry of active ITMS installation orders
to guide and improve computer vision OCR results.

In particular:
- Resolves suffix truncation (e.g. OCR read 'UMA123P' when active order is 'UMA123PK')
- Resolves prefix truncation (e.g. OCR read '123PK' or 'MA123PK' -> 'UMA123PK')
- Resolves confusable characters (e.g. 'UMA1238A' -> 'UMA123BA', '0' vs 'O', '1' vs 'I')
- Performs closed-set hypothesis alignment against active installation orders.
"""
import re
from typing import Any, Dict, List, Optional
from rapidfuzz import fuzz

from core.models import InstallationOrder
from core.vision import normalizer


# Character confusion maps for Ugandan plates
_CONFUSIONS = {
    "8": "B", "B": "8",
    "0": "O", "O": "0", "D": "0", "Q": "0",
    "1": "I", "I": "1", "T": "1", "L": "1",
    "5": "S", "S": "5",
    "2": "Z", "Z": "2",
    "4": "A", "A": "4",
    "6": "G", "G": "6",
}


def get_active_orders_cache(account_email: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns active orders awaiting installation evidence from the local database.
    Only includes orders that are currently active on ITMS and not yet completed.
    Filters by the active authenticated ITMS account to preserve multi-tenant isolation.
    """
    from core.services.itms_web_client import get_current_itms_account
    curr_email = (account_email or get_current_itms_account() or "").strip().lower()

    qs = InstallationOrder.objects.filter(
        is_active_on_itms=True,
    ).exclude(
        status__in=[
            InstallationOrder.Status.SUBMITTED,
            InstallationOrder.Status.INSTALLED,
            InstallationOrder.Status.CANCELLED,
        ]
    )
    if curr_email:
        from django.db.models import Q
        qs = qs.filter(Q(account_email__iexact=curr_email) | Q(account_email=""))

    orders = qs.values(
        "id",
        "order_number",
        "registration_number",
        "vin",
        "order_status",
        "itms_stage",
        "account_email",
    )
    return list(orders)


def disambiguate_plate_with_orders(
    raw_ocr: str,
    canonical_plate: str,
    active_orders: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Evaluates a candidate plate text against active ITMS orders.

    Returns:
        {
            "resolved_plate": str,      # Disambiguated / corrected plate string
            "order_id": int or None,    # Matching InstallationOrder ID if found
            "order_number": str,        # Matching order number
            "score": float,             # Match confidence score (0-100)
            "match_type": str,          # EXACT, ORDER_PRIOR, FUZZY, or NONE
            "was_corrected": bool,      # True if order prior corrected the text
            "reason": str,              # Detailed rationale for the decision
        }
    """
    clean_candidate = normalizer.canonicalize(canonical_plate or raw_ocr)
    if not clean_candidate or clean_candidate.startswith("PAIR-") or clean_candidate.startswith("MISSING-"):
        return {
            "resolved_plate": clean_candidate,
            "order_id": None,
            "order_number": "",
            "score": 0.0,
            "match_type": "NONE",
            "was_corrected": False,
            "reason": "Invalid or placeholder plate string",
        }

    if active_orders is None:
        active_orders = get_active_orders_cache()

    if not active_orders:
        return {
            "resolved_plate": clean_candidate,
            "order_id": None,
            "order_number": "",
            "score": 0.0,
            "match_type": "NONE",
            "was_corrected": False,
            "reason": "No active orders in local registry",
        }

    # Step 1: Check for Exact Match
    for order in active_orders:
        order_reg = normalizer.canonicalize(order["registration_number"])
        if clean_candidate == order_reg:
            return {
                "resolved_plate": order_reg,
                "order_id": order["id"],
                "order_number": order["order_number"],
                "score": 100.0,
                "match_type": "EXACT",
                "was_corrected": False,
                "reason": f"Exact match with active order #{order['order_number']}",
            }

    best_match = None
    highest_score = 0.0

    # Step 2: Test Active-Order Hypotheses
    for order in active_orders:
        order_reg = normalizer.canonicalize(order["registration_number"])
        if not order_reg:
            continue

        cand_len = len(clean_candidate)
        order_len = len(order_reg)

        # Case A: Missing Trailing Suffix (e.g. UMA123P -> UMA123PK)
        if order_len in (7, 8) and cand_len in (6, 7) and cand_len < order_len:
            if order_reg.startswith(clean_candidate):
                missing_chars = order_reg[cand_len:]
                score = 96.0 if len(missing_chars) == 1 else 92.0
                if score > highest_score:
                    highest_score = score
                    best_match = {
                        "resolved_plate": order_reg,
                        "order_id": order["id"],
                        "order_number": order["order_number"],
                        "score": score,
                        "match_type": "ORDER_PRIOR",
                        "was_corrected": True,
                        "reason": f"Suffix truncation completed ('{missing_chars}') from active order #{order['order_number']}",
                    }
                    continue

        # Case B: Missing Leading Prefix (e.g. MA123PK or 123PK -> UMA123PK)
        if order_len in (7, 8) and cand_len in (5, 6, 7) and cand_len < order_len:
            if order_reg.endswith(clean_candidate):
                missing_pfx = order_reg[:order_len - cand_len]
                score = 95.0
                if score > highest_score:
                    highest_score = score
                    best_match = {
                        "resolved_plate": order_reg,
                        "order_id": order["id"],
                        "order_number": order["order_number"],
                        "score": score,
                        "match_type": "ORDER_PRIOR",
                        "was_corrected": True,
                        "reason": f"Prefix truncation completed ('{missing_pfx}') from active order #{order['order_number']}",
                    }
                    continue

        # Case C: Single Confusable Character Substitution
        if cand_len == order_len and cand_len in (7, 8):
            diff_indices = [i for i in range(cand_len) if clean_candidate[i] != order_reg[i]]
            if len(diff_indices) == 1:
                idx = diff_indices[0]
                c_char = clean_candidate[idx]
                o_char = order_reg[idx]
                if _CONFUSIONS.get(c_char) == o_char or _CONFUSIONS.get(o_char) == c_char:
                    score = 94.0
                    if score > highest_score:
                        highest_score = score
                        best_match = {
                            "resolved_plate": order_reg,
                            "order_id": order["id"],
                            "order_number": order["order_number"],
                            "score": score,
                            "match_type": "ORDER_PRIOR",
                            "was_corrected": True,
                            "reason": f"OCR confusable corrected at pos {idx} ('{c_char}' -> '{o_char}') via order #{order['order_number']}",
                        }
                        continue

        # Case D: High-Similarity Fuzzy Ratio (>= 88%)
        ratio = fuzz.ratio(clean_candidate, order_reg)
        if ratio >= 88.0 and ratio > highest_score:
            highest_score = ratio
            best_match = {
                "resolved_plate": order_reg,
                "order_id": order["id"],
                "order_number": order["order_number"],
                "score": float(ratio),
                "match_type": "FUZZY",
                "was_corrected": True,
                "reason": f"Fuzzy similarity match ({ratio:.1f}%) against order #{order['order_number']}",
            }

    if best_match and best_match["score"] >= 88.0:
        return best_match

    return {
        "resolved_plate": clean_candidate,
        "order_id": None,
        "order_number": "",
        "score": highest_score,
        "match_type": "NONE",
        "was_corrected": False,
        "reason": f"No active order matched above confidence threshold (best: {highest_score:.1f}%)",
    }
