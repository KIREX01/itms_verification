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
from core.services.camera_naming import extract_chronological_sort_key, parse_camera_filename


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


import os
from difflib import SequenceMatcher
from typing import Optional


def _plate_similarity(p1: str, p2: str) -> float:
    """Returns character similarity between two plates (0.0 to 1.0)."""
    if not p1 or not p2:
        return 0.0
    p1_clean = "".join(c for c in p1.upper() if c.isalnum())
    p2_clean = "".join(c for c in p2.upper() if c.isalnum())
    if p1_clean == p2_clean:
        return 1.0
    if p1_clean in p2_clean or p2_clean in p1_clean:
        return 0.85
    return SequenceMatcher(None, p1_clean, p2_clean).ratio()


def _associate_batch_sequences(batch, summary: AssociationSummary) -> set:
    """Associates photos within a batch using camera timestamp sequences.

    Handles:
      1. Clustering batch photos into distinct walk sessions based on timestamp gaps (>10 minutes).
      2. Handling unknown orientation fallback when one orientation is deficient.
      3. Automatic detection of U-Turn / Snake traversal:
           - Technician walks forward shooting REARS (Bike 1 -> Bike N) or FRONTS
           - Steps around the last bike (turnaround point)
           - Walks backward shooting the opposite side (Bike N -> Bike 1)
      4. Cleans up any stale single-image incomplete pairs.
    """
    paired_image_ids = set()
    excluded_status = {EvidenceImage.Status.SUBMITTED, EvidenceImage.Status.DUPLICATE_SKIPPED}

    # Fetch all eligible images ordered by composite chronological key (EXIF + Camera Sequential)
    all_imgs = list(batch.images.exclude(status__in=excluded_status))
    if not all_imgs:
        return paired_image_ids

    all_imgs.sort(key=lambda img: extract_chronological_sort_key(
        img.original_source_path or img.vault_file,
        img.captured_at,
        img.ingested_at,
    ))

    # 1. Cluster into walk sessions (split if time delta > 600 seconds)
    sessions = []
    current_session = []
    for img in all_imgs:
        if not current_session:
            current_session.append(img)
        else:
            prev_time = (current_session[-1].captured_at or current_session[-1].ingested_at).timestamp()
            curr_time = (img.captured_at or img.ingested_at).timestamp()
            if abs(curr_time - prev_time) > 600:
                sessions.append(current_session)
                current_session = [img]
            else:
                current_session.append(img)
    if current_session:
        sessions.append(current_session)

    from django.db.models import Q
    from core.matcher.order_matcher import match_pair_to_order

    # 2. Process each session independently
    for session in sessions:
        rears = [img for img in session if img.orientation == EvidenceImage.Orientation.REAR]
        fronts = [img for img in session if img.orientation == EvidenceImage.Orientation.FRONT]
        unknowns = [img for img in session if img.orientation not in (EvidenceImage.Orientation.REAR, EvidenceImage.Orientation.FRONT)]

        # If unknowns exist and one side has fewer images, assign unknowns to the deficit side
        if unknowns:
            diff = len(rears) - len(fronts)
            if diff > 0:
                take = min(diff, len(unknowns))
                for u_img in unknowns[:take]:
                    u_img.orientation = EvidenceImage.Orientation.FRONT
                    u_img.save(update_fields=["orientation"])
                    fronts.append(u_img)
            elif diff < 0:
                take = min(-diff, len(unknowns))
                for u_img in unknowns[:take]:
                    u_img.orientation = EvidenceImage.Orientation.REAR
                    u_img.save(update_fields=["orientation"])
                    rears.append(u_img)

        # Re-sort rears and fronts by composite chronological key (EXIF + sequential counter)
        rears.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))
        fronts.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))

        if not rears or not fronts:
            continue

        r_first_time = (rears[0].captured_at or rears[0].ingested_at).timestamp()
        r_last_time = (rears[-1].captured_at or rears[-1].ingested_at).timestamp()
        f_first_time = (fronts[0].captured_at or fronts[0].ingested_at).timestamp()
        f_last_time = (fronts[-1].captured_at or fronts[-1].ingested_at).timestamp()

        # Turnaround gap vs Parallel gap
        gap_uturn = min(abs(f_first_time - r_last_time), abs(r_first_time - f_last_time))
        gap_parallel = abs(f_first_time - r_first_time)

        is_uturn = gap_uturn <= gap_parallel

        if is_uturn:
            # Whichever orientation started later was walked in reverse
            if f_first_time >= r_first_time:
                aligned_rears = list(rears)
                aligned_fronts = list(reversed(fronts))
            else:
                aligned_rears = list(reversed(rears))
                aligned_fronts = list(fronts)
        else:
            aligned_rears = list(rears)
            aligned_fronts = list(fronts)

        pair_count = min(len(aligned_rears), len(aligned_fronts))
        for i in range(pair_count):
            r_img = aligned_rears[i]
            f_img = aligned_fronts[i]

            # Use rear plate as the canonical truth; fallback to front plate
            canonical_plate = r_img.detected_plate or f_img.detected_plate
            if not canonical_plate:
                continue

            # Delete any stale single-image incomplete pairs referencing these images
            VehicleInstallationPair.objects.filter(is_complete=False).filter(
                Q(front_image=f_img) | Q(rear_image=r_img) | Q(front_image=r_img) | Q(rear_image=f_img)
            ).delete()

            pair, _created = VehicleInstallationPair.objects.get_or_create(
                registration_number_detected=canonical_plate,
                defaults={"verification_status": VehicleInstallationPair.VerificationStatus.INCOMPLETE},
            )
            pair.front_image = f_img
            pair.rear_image = r_img
            pair.is_complete = True

            if pair.verification_status in (
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
            ):
                pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW

            r_img.status = EvidenceImage.Status.MATCHED
            f_img.status = EvidenceImage.Status.MATCHED
            r_img.save(update_fields=["status"])
            f_img.save(update_fields=["status"])
            pair.save()

            # Attempt order match
            match_pair_to_order(pair)

            paired_image_ids.add(r_img.id)
            paired_image_ids.add(f_img.id)
            summary.complete_pairs += 1
            walk_str = "U-turn" if is_uturn else "Parallel"
            summary.details.append(f"Seq ({walk_str}): {canonical_plate} linked Front[{f_img.id.hex[:6]}] + Rear[{r_img.id.hex[:6]}]")

    return paired_image_ids


def get_closest_candidates(target_image: EvidenceImage, top_n: int = 10) -> List[Dict]:
    """Ranks and returns the closest candidate opposite-orientation photos for a given image.

    Used by the operator in the TUI to manually pick or verify matches.
    Ranked by:
      1. Capture timestamp proximity (closest in time)
      2. Camera filename sequential counter proximity (iPhone IMG_0041, WhatsApp WA0001)
      3. Plate text / substring similarity
      4. Same-batch affinity
    """
    if not target_image:
        return []

    target_time = target_image.captured_at or target_image.ingested_at
    target_orient = target_image.orientation
    desired_orient = (
        EvidenceImage.Orientation.FRONT if target_orient == EvidenceImage.Orientation.REAR
        else EvidenceImage.Orientation.REAR
    )

    qs = EvidenceImage.objects.exclude(id=target_image.id).exclude(
        status__in=[EvidenceImage.Status.SUBMITTED, EvidenceImage.Status.DUPLICATE_SKIPPED]
    )

    # Filter to desired orientation or unknown
    orient_candidates = qs.filter(orientation__in=[desired_orient, EvidenceImage.Orientation.UNKNOWN])
    if orient_candidates.exists():
        qs = orient_candidates

    target_sig = parse_camera_filename(target_image.original_source_path or target_image.vault_file)
    candidates = []
    target_ts = target_time.timestamp() if target_time else 0.0

    for cand in qs:
        cand_time = cand.captured_at or cand.ingested_at
        cand_ts = cand_time.timestamp() if cand_time else 0.0
        time_diff = abs(cand_ts - target_ts) if target_ts and cand_ts else 999999.0

        sim = _plate_similarity(target_image.detected_plate, cand.detected_plate)

        cand_sig = parse_camera_filename(cand.original_source_path or cand.vault_file)

        # Sequence number proximity bonus (if both have sequential counters, e.g. iPhone, WA, DCF)
        seq_bonus = 0.0
        if target_sig.sequence_number is not None and cand_sig.sequence_number is not None:
            seq_diff = abs(cand_sig.sequence_number - target_sig.sequence_number)
            if seq_diff <= 15:
                seq_bonus = max(0.0, 15.0 - (seq_diff * 1.0))

        # Ranking score: higher is better
        time_score = max(0.0, 50.0 - (time_diff / 10.0))
        text_score = sim * 40.0
        batch_bonus = 10.0 if (target_image.batch_id and cand.batch_id == target_image.batch_id) else 0.0
        total_score = time_score + text_score + batch_bonus + seq_bonus

        # Format human-friendly time delta
        if time_diff < 60:
            diff_str = f"{int(time_diff)}s away"
        elif time_diff < 3600:
            diff_str = f"{int(time_diff // 60)}m {int(time_diff % 60)}s away"
        elif time_diff < 86400:
            diff_str = f"{int(time_diff // 3600)}h away"
        else:
            diff_str = f"{int(time_diff // 86400)}d away"

        fname = os.path.basename(cand.original_source_path or cand.vault_file)
        candidates.append({
            "image": cand,
            "id": str(cand.id),
            "filename": fname,
            "plate": cand.detected_plate or "—",
            "orientation": cand.orientation,
            "camera_tag": cand_sig.display_tag,
            "sequence_number": cand_sig.sequence_number,
            "time_diff_seconds": time_diff,
            "time_diff_display": diff_str,
            "similarity_pct": round(sim * 100, 1),
            "score": round(total_score, 1),
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:top_n]


def link_pair_manually(
    front_image: EvidenceImage,
    rear_image: EvidenceImage,
    canonical_plate: Optional[str] = None,
    target_pair: Optional[VehicleInstallationPair] = None,
) -> VehicleInstallationPair:
    """Manually links a front photo and a rear photo into a complete pair.

    Updates target_pair if provided, otherwise gets or creates a pair for the canonical plate.
    Cleans up any orphaned incomplete pairs that previously referenced either photo.
    """
    from django.db.models import Q
    from core.models import SubmissionAuditLog
    from core.matcher.order_matcher import match_pair_to_order

    plate = canonical_plate or rear_image.detected_plate or front_image.detected_plate or "MANUAL_LINK"

    if target_pair:
        pair = target_pair
        if canonical_plate:
            pair.registration_number_detected = canonical_plate
    else:
        pair, _ = VehicleInstallationPair.objects.get_or_create(
            registration_number_detected=plate,
            defaults={"verification_status": VehicleInstallationPair.VerificationStatus.PENDING_REVIEW},
        )

    # Clean up other incomplete pairs that previously held either image
    VehicleInstallationPair.objects.filter(is_complete=False).filter(
        Q(front_image=front_image) | Q(rear_image=rear_image) | Q(front_image=rear_image) | Q(rear_image=front_image)
    ).exclude(id=pair.id).delete()

    pair.front_image = front_image
    pair.rear_image = rear_image
    pair.is_complete = True
    pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW

    # Match order
    match_pair_to_order(pair)
    pair.save()

    front_image.status = EvidenceImage.Status.MATCHED
    rear_image.status = EvidenceImage.Status.MATCHED
    front_image.save(update_fields=["status"])
    rear_image.save(update_fields=["status"])

    SubmissionAuditLog.objects.create(
        pair=pair,
        action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
        result=SubmissionAuditLog.ResultStatus.SUCCESS,
        message=f"Operator manually linked Front [{front_image.id.hex[:6]}] + Rear [{rear_image.id.hex[:6]}]",
    )

    return pair


@transaction.atomic
def run_association() -> AssociationSummary:
    """Runs full pairwise association:

    1. Batch-level U-Turn & timestamp sequence alignment (for front/rear folders).
    2. Plate-driven grouping for remaining unassociated photos.
    """
    from core.models import IngestionBatch
    summary = AssociationSummary()
    already_paired = set()

    # Pass 1: U-Turn & Sequence Association across batches
    for batch in IngestionBatch.objects.all():
        paired_in_batch = _associate_batch_sequences(batch, summary)
        already_paired.update(paired_in_batch)

    # Pass 2: Plate-driven grouping for remaining photos
    excluded_status = {EvidenceImage.Status.SUBMITTED, EvidenceImage.Status.DUPLICATE_SKIPPED}
    qs = (
        EvidenceImage.objects.exclude(status__in=excluded_status)
        .exclude(id__in=already_paired)
        .exclude(detected_plate="")
        .order_by("ingested_at")
    )
    groups: Dict[str, List[EvidenceImage]] = defaultdict(list)
    for image in qs:
        groups[image.detected_plate].append(image)

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
