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


def _active_registry():
    """Orders still awaiting evidence -- the bounded search space."""
    return list(
        InstallationOrder.objects.exclude(status=InstallationOrder.Status.SUBMITTED)
        .exclude(status=InstallationOrder.Status.CANCELLED)
        .values_list("id", "registration_number")
    )


def find_best_match(detected_plate: str) -> MatchOutcome:
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

    order = InstallationOrder.objects.filter(id=order_id).first() if match_type != VehicleInstallationPair.MatchType.NONE else None
    return MatchOutcome(order=order, score=float(score), match_type=match_type)


def match_pair_to_order(pair: VehicleInstallationPair) -> VehicleInstallationPair:
    """
    Run fuzzy matching for a single pair and update its order/match_type/
    match_score/verification_status fields (does not save related images).
    """
    outcome = find_best_match(pair.registration_number_detected)
    pair.match_score = outcome.score
    pair.match_type = outcome.match_type

    if outcome.match_type in (VehicleInstallationPair.MatchType.EXACT, VehicleInstallationPair.MatchType.FUZZY):
        pair.order = outcome.order
        if pair.is_complete:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
    elif outcome.score < _REJECT:
        pair.order = None
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
    count = 0
    for pair in qs:
        match_pair_to_order(pair)
        count += 1
    return count
