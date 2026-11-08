"""
Fuzzy order matcher with strict One-to-One order assignment invariants.

Matches a VehicleInstallationPair's detected registration number against
the bounded set of active InstallationOrder rows using RapidFuzz. This is
closed-set hypothesis testing (per the proposal), not open recognition:
we already know the finite list of registrations that SHOULD appear.

Invariants:
1. One-to-One Mapping: An active InstallationOrder can be assigned to at most ONE active/submitted pair.
2. Complete valid plates (e.g. 'UMA198QP') are NEVER fuzzy-matched to different plates in the series (e.g. 'UMA199QP').
3. Any pair without an active, verified, unassigned pending order MUST be assigned CONFLICT status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from django.conf import settings
from django.db.models import Q
from rapidfuzz import fuzz, process

from core.models import InstallationOrder, VehicleInstallationPair

_EXACT = getattr(settings, "FUZZY_EXACT_THRESHOLD", 100)
_ACCEPT = getattr(settings, "FUZZY_ACCEPT_THRESHOLD", 85)
_REJECT = getattr(settings, "FUZZY_REJECT_THRESHOLD", 75)


@dataclass
class MatchOutcome:
    order: Optional[InstallationOrder]
    score: float
    match_type: str  # VehicleInstallationPair.MatchType value


def get_pending_orders_queryset(account_email: Optional[str] = None, exclude_pair_id: Optional[int] = None):
    """
    Returns only truly PENDING orders awaiting installation evidence.
    Orders that are SUBMITTED, INSTALLED, CANCELLED, archived, closed,
    or already claimed by an active unsubmitted or submitted pair are strictly excluded.
    """
    from core.services.itms_web_client import get_current_itms_account
    curr_email = (account_email or get_current_itms_account() or "").strip().lower()

    # 1 Order = Exactly 1 Active Pair (One-to-One Invariant)
    # Exclude orders already assigned to another active, non-deleted pair
    claimed_orders_qs = VehicleInstallationPair.objects.filter(
        is_deleted=False,
        order__isnull=False,
    )
    if exclude_pair_id:
        claimed_orders_qs = claimed_orders_qs.exclude(id=exclude_pair_id)

    claimed_order_ids = claimed_orders_qs.values_list("order_id", flat=True)

    qs = InstallationOrder.objects.filter(
        status=InstallationOrder.Status.PENDING,
        is_archived=False,
        is_active_on_itms=True,
    ).exclude(
        order_status__in=["Installed", "Ready for approve", "Closed", "Archived"]
    ).exclude(
        itms_stage__in=["STAGE_2_APPROVE", "STAGE_3_CONFIRMATION", "ARCHIVED"]
    ).exclude(
        id__in=claimed_order_ids
    )

    if curr_email:
        qs = qs.filter(Q(account_email__iexact=curr_email) | Q(account_email=""))

    return qs


def _active_registry(account_email: Optional[str] = None, exclude_pair_id: Optional[int] = None):
    """Orders still awaiting evidence -- bounded search space strictly containing PENDING and unclaimed orders."""
    qs = get_pending_orders_queryset(account_email=account_email, exclude_pair_id=exclude_pair_id)
    return list(qs.values_list("id", "registration_number"))


def find_best_match(
    detected_plate: str,
    active_orders: Optional[List[Dict]] = None,
    registry: Optional[List] = None,
    order_map: Optional[Dict[int, InstallationOrder]] = None,
    exclude_pair_id: Optional[int] = None,
) -> MatchOutcome:
    if not detected_plate or detected_plate.startswith("PAIR-") or detected_plate.startswith("MISSING-") or detected_plate == "MANUAL_LINK":
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    from core.vision import normalizer
    from core.matcher.prior_guided import disambiguate_plate_with_orders

    prior_res = disambiguate_plate_with_orders(detected_plate, detected_plate, active_orders=active_orders)
    if prior_res["order_id"]:
        oid = prior_res["order_id"]
        order = order_map.get(oid) if order_map is not None else get_pending_orders_queryset(exclude_pair_id=exclude_pair_id).filter(id=oid).first()
        if order:
            m_type = (
                VehicleInstallationPair.MatchType.EXACT
                if prior_res["match_type"] == "EXACT"
                else VehicleInstallationPair.MatchType.FUZZY
            )
            return MatchOutcome(order=order, score=float(prior_res["score"]), match_type=m_type)

    # If the candidate plate is ALREADY a valid complete plate (e.g. UMA198QP),
    # do NOT allow arbitrary fuzzy ratio matching against other valid plates in the batch!
    clean_candidate = normalizer.canonicalize(detected_plate)
    if normalizer.is_valid_plate(clean_candidate):
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    if registry is None:
        registry = _active_registry(exclude_pair_id=exclude_pair_id)
    if not registry:
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    choices = {order_id: reg for order_id, reg in registry}
    best = process.extractOne(
        clean_candidate or detected_plate, choices, scorer=fuzz.ratio,
    )

    if best is None:
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    _matched_reg, score, order_id = best

    if score >= _EXACT:
        match_type = VehicleInstallationPair.MatchType.EXACT
    elif score >= _ACCEPT:
        match_type = VehicleInstallationPair.MatchType.FUZZY
    else:
        match_type = VehicleInstallationPair.MatchType.NONE

    if match_type != VehicleInstallationPair.MatchType.NONE:
        order = order_map.get(order_id) if order_map is not None else get_pending_orders_queryset(exclude_pair_id=exclude_pair_id).filter(id=order_id).first()
    else:
        order = None
    return MatchOutcome(order=order, score=float(score), match_type=match_type)


def match_pair_to_order(
    pair: VehicleInstallationPair,
    active_orders: Optional[List[Dict]] = None,
    registry: Optional[List] = None,
    order_map: Optional[Dict[int, InstallationOrder]] = None,
) -> VehicleInstallationPair:
    """
    Run matching for a single pair and enforce strict state transitions:
    - If pair is incomplete -> INCOMPLETE
    - If no pending unassigned order matches -> CONFLICT
    - If valid unassigned order matches -> PENDING_REVIEW
    """
    # Once a pair is SUBMITTED, it must ALWAYS remain SUBMITTED
    if pair.verification_status == VehicleInstallationPair.VerificationStatus.SUBMITTED:
        return pair

    # If pair is incomplete, keep INCOMPLETE status and clear order
    if not pair.is_complete:
        pair.verification_status = VehicleInstallationPair.VerificationStatus.INCOMPLETE
        pair.match_type = VehicleInstallationPair.MatchType.NONE
        pair.match_score = None
        pair.order = None
        pair.save(update_fields=["verification_status", "match_type", "match_score", "order"])
        return pair

    if registry is None:
        registry = _active_registry(account_email=pair.account_email, exclude_pair_id=pair.id)
    if active_orders is None:
        from core.matcher.prior_guided import get_active_orders_cache
        active_orders = get_active_orders_cache(account_email=pair.account_email, exclude_pair_id=pair.id)

    outcome = find_best_match(
        pair.registration_number_detected,
        active_orders=active_orders,
        registry=registry,
        order_map=order_map,
        exclude_pair_id=pair.id,
    )
    pair.match_score = outcome.score
    pair.match_type = outcome.match_type

    if outcome.match_type in (VehicleInstallationPair.MatchType.EXACT, VehicleInstallationPair.MatchType.FUZZY) and outcome.order:
        # Extra safeguard: ensure this order is not already claimed by another active pair
        other_pair = (
            VehicleInstallationPair.objects.filter(
                is_deleted=False,
                order=outcome.order,
            ).exclude(id=pair.id).first()
        )
        if other_pair:
            pair.order = None
            pair.match_type = VehicleInstallationPair.MatchType.NONE
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            pair.operator_note = (
                f"Order Conflict: Order #{outcome.order.order_number} ({outcome.order.registration_number}) "
                f"is already assigned to Pair #{other_pair.id}. Duplicate plate in batch."
            )
        else:
            pair.order = outcome.order
            pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
            if outcome.order and pair.registration_number_detected != outcome.order.registration_number:
                pair.matched_via = VehicleInstallationPair.MatchedVia.ORDER_PRIOR
                pair.registration_number_detected = outcome.order.registration_number
                if pair.front_image and not pair.front_image.detected_plate:
                    pair.front_image.detected_plate = outcome.order.registration_number
                    pair.front_image.save(update_fields=["detected_plate"])
                if pair.rear_image and not pair.rear_image.detected_plate:
                    pair.rear_image.detected_plate = outcome.order.registration_number
                    pair.rear_image.save(update_fields=["detected_plate"])
    else:
        # No unassigned pending order is available for this plate -> MUST BE CONFLICT
        pair.order = None
        pair.match_type = VehicleInstallationPair.MatchType.NONE
        pair.match_score = outcome.score if outcome else None
        pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT

        from core.vision import normalizer
        clean_plate = normalizer.canonicalize(pair.registration_number_detected) or pair.registration_number_detected

        # Check if an order exists in local DB but is closed/installed/submitted/archived
        closed_order = (
            InstallationOrder.objects.filter(
                Q(registration_number__iexact=clean_plate) | Q(registration_number__iexact=pair.registration_number_detected)
            ).order_by("-updated_at").first()
        )
        if closed_order:
            order_stat = closed_order.order_status or closed_order.status
            pair.operator_note = (
                f"Order Conflict: Order #{closed_order.order_number} for plate '{clean_plate}' is already "
                f"{order_stat} (closed/submitted). No active pending order available."
            )
        else:
            # Check if kit exists in stock but unallocated
            kit_note = ""
            try:
                from core.models import InstallationKit
                kit = InstallationKit.objects.filter(
                    Q(registration_number__iexact=pair.registration_number_detected)
                    | Q(registration_number__iexact=clean_plate)
                    | Q(kit_code__iexact=f"IK-{clean_plate}")
                ).first()
                if kit and (kit.status or "").strip().lower() == "new":
                    kit_note = f" (Kit {kit.kit_code} is in stock with status 'New' at {kit.warehouse or 'warehouse'})"
            except Exception:
                pass

            existing_note = pair.operator_note or ""
            conflict_msg = (
                f"Order Conflict: No pending installation order found for plate '{clean_plate}'{kit_note}. "
                f"Vehicle plate not in active pending order list."
            )
            pair.operator_note = f"{existing_note} | {conflict_msg}".strip(" |") if existing_note else conflict_msg

    pair.save()
    return pair


def match_all_pending_pairs() -> int:
    """Runs the matcher sequentially over unsubmitted pairs, enforcing 1:1 order assignment."""
    unsubmitted_qs = VehicleInstallationPair.objects.filter(
        is_deleted=False,
    ).exclude(
        verification_status__in=[
            VehicleInstallationPair.VerificationStatus.SUBMITTED,
            VehicleInstallationPair.VerificationStatus.FAILED,
        ]
    ).order_by("id")

    count = 0
    for pair in unsubmitted_qs:
        match_pair_to_order(pair)
        count += 1
    return count
