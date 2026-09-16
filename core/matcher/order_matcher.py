"""
Fuzzy order matcher.

Matches a VehicleInstallationPair's detected registration number against
the bounded set of active InstallationOrder rows using RapidFuzz. This is
closed-set hypothesis testing (per the proposal), not open recognition:
we already know the finite list of registrations that SHOULD appear.

Thresholds (configurable via settings / .env):
    score == FUZZY_EXACT_THRESHOLD (100)          -> MatchType.EXACT
    score >= FUZZY_ACCEPT_THRESHOLD (85)           -> MatchType.FUZZY (auto-linked)
    FUZZY_REJECT_THRESHOLD <= score < ACCEPT (75-85) -> ambiguous, needs operator review
    score < FUZZY_REJECT_THRESHOLD (75)            -> UNREGISTERED_VEHICLE
"""
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
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


def _active_registry(account_email: Optional[str] = None):
    """Orders still awaiting evidence -- the bounded search space."""
    from core.services.itms_web_client import get_current_itms_account
    curr_email = (account_email or get_current_itms_account() or "").strip().lower()

    qs = InstallationOrder.objects.exclude(status=InstallationOrder.Status.SUBMITTED).exclude(status=InstallationOrder.Status.CANCELLED)
    if curr_email:
        from django.db.models import Q
        qs = qs.filter(Q(account_email__iexact=curr_email) | Q(account_email=""))

    return list(qs.values_list("id", "registration_number"))


def find_best_match(
    detected_plate: str,
    active_orders: Optional[List[Dict]] = None,
    registry: Optional[List] = None,
    order_map: Optional[Dict[int, InstallationOrder]] = None,
) -> MatchOutcome:
    if not detected_plate or detected_plate.startswith("PAIR-") or detected_plate.startswith("MISSING-") or detected_plate == "MANUAL_LINK":
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    from core.matcher.prior_guided import disambiguate_plate_with_orders
    prior_res = disambiguate_plate_with_orders(detected_plate, detected_plate, active_orders=active_orders)
    if prior_res["order_id"]:
        oid = prior_res["order_id"]
        order = order_map.get(oid) if order_map is not None else InstallationOrder.objects.filter(id=oid).first()
        if order:
            m_type = (
                VehicleInstallationPair.MatchType.EXACT
                if prior_res["match_type"] == "EXACT"
                else VehicleInstallationPair.MatchType.FUZZY
            )
            return MatchOutcome(order=order, score=float(prior_res["score"]), match_type=m_type)

    if registry is None:
        registry = _active_registry()
    if not registry:
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    choices = {order_id: reg for order_id, reg in registry}
    best = process.extractOne(
        detected_plate, choices, scorer=fuzz.ratio,
    )

    if best is None:
        return MatchOutcome(order=None, score=0.0, match_type=VehicleInstallationPair.MatchType.NONE)

    _matched_reg, score, order_id = best

    if score >= _EXACT:
        match_type = VehicleInstallationPair.MatchType.EXACT
    elif score >= _ACCEPT:
        match_type = VehicleInstallationPair.MatchType.FUZZY
    else:
        # Below accept threshold: caller decides (needs_review band vs unregistered)
        match_type = VehicleInstallationPair.MatchType.NONE

    if match_type != VehicleInstallationPair.MatchType.NONE:
        order = order_map.get(order_id) if order_map is not None else InstallationOrder.objects.filter(id=order_id).first()
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
    Run fuzzy matching for a single pair and update its order/match_type/
    match_score/verification_status fields (does not save related images).
    """
    # If pair is incomplete, keep INCOMPLETE status
    if not pair.is_complete:
        pair.verification_status = VehicleInstallationPair.VerificationStatus.INCOMPLETE
        pair.match_type = VehicleInstallationPair.MatchType.NONE
        pair.match_score = None
        pair.order = None
        pair.save()
        return pair

    outcome = find_best_match(pair.registration_number_detected, active_orders=active_orders, registry=registry, order_map=order_map)
    pair.match_score = outcome.score
    pair.match_type = outcome.match_type

    if outcome.match_type in (VehicleInstallationPair.MatchType.EXACT, VehicleInstallationPair.MatchType.FUZZY):
        pair.order = outcome.order
        pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        if outcome.order and pair.registration_number_detected != outcome.order.registration_number:
            pair.matched_via = VehicleInstallationPair.MatchedVia.ORDER_PRIOR
            # Align pair plate with confirmed canonical order plate
            pair.registration_number_detected = outcome.order.registration_number
            if pair.front_image and not pair.front_image.detected_plate:
                pair.front_image.detected_plate = outcome.order.registration_number
                pair.front_image.save(update_fields=["detected_plate"])
            if pair.rear_image and not pair.rear_image.detected_plate:
                pair.rear_image.detected_plate = outcome.order.registration_number
                pair.rear_image.save(update_fields=["detected_plate"])
    elif outcome.score < _REJECT:
        pair.order = None
        # Don't mark UNREGISTERED if plate is a provisional tag
        if not pair.registration_number_detected.startswith("PAIR-"):
            pair.verification_status = VehicleInstallationPair.VerificationStatus.UNREGISTERED
    else:
        # In the ambiguous 75-85 band: leave for manual operator review, don't auto-link
        pair.order = None
        if pair.verification_status not in (VehicleInstallationPair.VerificationStatus.CONFLICT,):
            pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW

    pair.save()
    return pair


def match_all_pending_pairs() -> int:
    """Runs the matcher over every pair that doesn't yet have a confirmed order. Returns count processed."""
    qs = VehicleInstallationPair.objects.filter(
        order__isnull=True
    ).exclude(
        verification_status__in=[
            VehicleInstallationPair.VerificationStatus.SUBMITTED,
            VehicleInstallationPair.VerificationStatus.FAILED,
        ]
    )
    from core.matcher.prior_guided import get_active_orders_cache
    active_orders = get_active_orders_cache()
    registry = _active_registry()
    if not registry and not active_orders:
        return 0

    order_ids = [r[0] for r in registry]
    order_map = {o.id: o for o in InstallationOrder.objects.filter(id__in=order_ids)}

    count = 0
    for pair in qs:
        match_pair_to_order(pair, active_orders=active_orders, registry=registry, order_map=order_map)
        count += 1
    return count
