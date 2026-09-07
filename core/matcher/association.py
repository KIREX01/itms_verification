"""
Pairwise association engine.

Groups EvidenceImage rows by their normalized `detected_plate`, then
evaluates the orientation makeup of each group against the rules from
the roadmap:

    1 FRONT + 1 REAR  -> is_complete=True,  verification_status=PENDING_REVIEW
    only 1 photo       -> is_complete=False, verification_status=INCOMPLETE
    2 FRONTs or 2 REARs -> verification_status=CONFLICT (operator must pick)

This module only touches EvidenceImage + VehicleInstallationPair; order
matching against InstallationOrder is handled separately in
core/matcher/order_matcher.py so the two concerns stay independently
testable.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from django.db import transaction

from core.models import EvidenceImage, VehicleInstallationPair


@dataclass
class AssociationSummary:
    groups_processed: int = 0
    complete_pairs: int = 0
    incomplete: int = 0
    conflicts: int = 0
    pairs_created: int = 0
    pairs_updated: int = 0
    details: List[str] = field(default_factory=list)


def _group_eligible_images() -> Dict[str, List[EvidenceImage]]:
    """
    Group images that have a plate detected and aren't already terminal
    (submitted/duplicate) by their canonical plate string.
    """
    excluded_status = {EvidenceImage.Status.SUBMITTED, EvidenceImage.Status.DUPLICATE_SKIPPED}
    qs = (
        EvidenceImage.objects.exclude(status__in=excluded_status)
        .exclude(detected_plate="")
        .order_by("ingested_at")
    )
    groups: Dict[str, List[EvidenceImage]] = defaultdict(list)
    for image in qs:
        groups[image.detected_plate].append(image)
    return groups


def _resolve_group(plate: str, images: List[EvidenceImage]) -> VehicleInstallationPair:
    fronts = [img for img in images if img.orientation == EvidenceImage.Orientation.FRONT]
    rears = [img for img in images if img.orientation == EvidenceImage.Orientation.REAR]

    pair, _created = VehicleInstallationPair.objects.get_or_create(
        registration_number_detected=plate,
        defaults={"verification_status": VehicleInstallationPair.VerificationStatus.INCOMPLETE},
    )

    if len(fronts) == 1 and len(rears) == 1:
        pair.front_image = fronts[0]
        pair.rear_image = rears[0]
        pair.is_complete = True
        # Don't clobber a status an operator/matcher already advanced past review
        if pair.verification_status in (
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
        ):
            pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        for img in (fronts[0], rears[0]):
            img.status = EvidenceImage.Status.MATCHED
            img.save(update_fields=["status"])

    elif len(fronts) + len(rears) == 1:
        pair.is_complete = False
        pair.verification_status = VehicleInstallationPair.VerificationStatus.INCOMPLETE
        only = (fronts + rears)[0]
        if fronts:
            pair.front_image = only
        else:
            pair.rear_image = only
        only.status = EvidenceImage.Status.INCOMPLETE
        only.save(update_fields=["status"])

    else:
        # 2+ fronts, 2+ rears, or some other ambiguous mix
        pair.is_complete = False
        pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
        for img in images:
            img.status = EvidenceImage.Status.NEEDS_REVIEW
            img.save(update_fields=["status"])

    pair.save()
    return pair


@transaction.atomic
def run_association() -> AssociationSummary:
    summary = AssociationSummary()
    groups = _group_eligible_images()

    for plate, images in groups.items():
        pair = _resolve_group(plate, images)
        summary.groups_processed += 1

        if pair.verification_status == VehicleInstallationPair.VerificationStatus.CONFLICT:
            summary.conflicts += 1
        elif pair.is_complete:
            summary.complete_pairs += 1
        else:
            summary.incomplete += 1

        summary.details.append(f"{plate}: {pair.verification_status} (images={len(images)})")

    return summary
