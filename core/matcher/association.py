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
from typing import Dict, List, Optional, Tuple

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
    discrepancies: List[str] = field(default_factory=list)
    missing_photos: List[Dict] = field(default_factory=list)
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

    from core.services.itms_web_client import get_current_itms_account
    curr_acc = get_current_itms_account()

    pair, _created = VehicleInstallationPair.objects.get_or_create(
        registration_number_detected=plate,
        defaults={
            "verification_status": VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            "account_email": curr_acc,
        },
    )
    if curr_acc and not pair.account_email:
        pair.account_email = curr_acc

    if len(fronts) == 1 and len(rears) == 1:
        pair.front_image = fronts[0]
        pair.rear_image = rears[0]
        pair.is_complete = True
        pair.operator_note = f"Auto-paired via Plate String Equality ('{plate}') | 1 Front + 1 Rear detected"
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
            pair.operator_note = f"Incomplete Pair: Only 1 FRONT photo detected for plate '{plate}' (Rear photo missing)"
        else:
            pair.rear_image = only
            pair.operator_note = f"Incomplete Pair: Only 1 REAR photo detected for plate '{plate}' (Front photo missing)"
        only.status = EvidenceImage.Status.INCOMPLETE
        only.save(update_fields=["status"])

    else:
        # 2+ fronts, 2+ rears, or some other ambiguous mix
        pair.is_complete = False
        pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
        pair.operator_note = f"Conflict: Ambiguous orientation makeup ({len(fronts)} Fronts, {len(rears)} Rears) detected for plate '{plate}'"
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


def _clean_stem_name(path_str: str) -> str:
    """Strips common orientation tokens and prefixes to find canonical base matching stem."""
    if not path_str:
        return ""
    base = os.path.basename(path_str)
    stem, _ = os.path.splitext(base)
    stem_clean = stem.lower().strip()
    # Remove leading common prefixes
    for pfx in ("front_", "rear_", "front-", "rear-", "f_", "r_", "img_", "image_", "dsc_", "pic_", "cam01_", "cam02_", "cam1_", "cam2_"):
        if stem_clean.startswith(pfx):
            stem_clean = stem_clean[len(pfx):]
            break
    # Remove trailing common orientation suffixes
    for sfx in ("_front", "_rear", "-front", "-rear", "_f", "_r", "-f", "-r"):
        if stem_clean.endswith(sfx):
            stem_clean = stem_clean[:-len(sfx)]
            break
    return stem_clean.strip()


def _align_and_pair_candidates(
    fronts: List[EvidenceImage],
    rears: List[EvidenceImage],
    batch,
    summary: AssociationSummary,
    paired_image_ids: set,
    label_prefix: str = "",
) -> Tuple[List[EvidenceImage], List[EvidenceImage]]:
    """Runs multi-tier association on candidate front and rear image sets.

    Tiers:
      Tier 1: Filename Synergy (Matching stems & camera shutter sequence counters)
      Tier 2: Temporal Trajectory Alignment (U-Turn Walk based on turnaround intervals)
      Tier 3: Plate OCR Direct Match (High-confidence character similarity)
      Tier 4: Temporal Proximity Alignment (Parallel Walk)

    Returns:
      (surplus_fronts, surplus_rears)
    """
    import os
    from django.db.models import Q
    from core.matcher.order_matcher import match_pair_to_order

    unpaired_fronts = list(fronts)
    unpaired_rears = list(rears)
    resolved_pairs = []

    # --- Tier 1: Filename Synergy (Matching Stems & Shutter Indices) ---
    # 1A: Exact stem match (e.g. front/01.jpg <-> rear/01.jpg or front/bike1.jpg <-> rear/bike1.jpg)
    matched_f_ids = set()
    matched_r_ids = set()
    for f_img in unpaired_fronts:
        f_clean = _clean_stem_name(f_img.original_source_path or f_img.vault_file)
        if not f_clean or f_clean in ("front", "rear"):
            continue
        for r_img in unpaired_rears:
            if r_img.id in matched_r_ids:
                continue
            r_clean = _clean_stem_name(r_img.original_source_path or r_img.vault_file)
            if r_clean == f_clean:
                matched_f_ids.add(f_img.id)
                matched_r_ids.add(r_img.id)
                canonical_plate = r_img.detected_plate or f_img.detected_plate or f"PAIR-{f_clean.upper()}"
                f_name = os.path.basename(f_img.original_source_path or f_img.vault_file)
                r_name = os.path.basename(r_img.original_source_path or r_img.vault_file)
                note = f"Auto-paired via Filename Stem Alignment ('{f_name}' <-> '{r_name}') | Identical stem '{f_clean}'"
                resolved_pairs.append((f_img, r_img, canonical_plate, note))
                break

    unpaired_fronts = [f for f in unpaired_fronts if f.id not in matched_f_ids]
    unpaired_rears = [r for r in unpaired_rears if r.id not in matched_r_ids]

    # 1B: Exact shutter sequence index match (e.g. CAM01_0005 vs CAM02_0005)
    matched_f_ids = set()
    matched_r_ids = set()
    for f_img in unpaired_fronts:
        f_sig = parse_camera_filename(f_img.original_source_path or f_img.vault_file)
        if f_sig.sequence_number is None:
            continue
        for r_img in unpaired_rears:
            if r_img.id in matched_r_ids:
                continue
            r_sig = parse_camera_filename(r_img.original_source_path or r_img.vault_file)
            if r_sig.sequence_number == f_sig.sequence_number:
                matched_f_ids.add(f_img.id)
                matched_r_ids.add(r_img.id)
                canonical_plate = r_img.detected_plate or f_img.detected_plate or f"PAIR-SEQ{f_sig.sequence_number:02d}"
                note = f"Auto-paired via Camera Shutter Index (#{f_sig.sequence_number} <-> #{r_sig.sequence_number}) | {f_sig.vendor_convention}"
                resolved_pairs.append((f_img, r_img, canonical_plate, note))
                break

    unpaired_fronts = [f for f in unpaired_fronts if f.id not in matched_f_ids]
    unpaired_rears = [r for r in unpaired_rears if r.id not in matched_r_ids]

    # --- Tier 2: Temporal Trajectory Alignment (U-Turn Walk Detection) ---
    if unpaired_fronts and unpaired_rears and len(unpaired_fronts) > 1 and len(unpaired_rears) > 1:
        unpaired_rears.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))
        unpaired_fronts.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))

        r_first_time = (unpaired_rears[0].captured_at or unpaired_rears[0].ingested_at).timestamp()
        r_last_time = (unpaired_rears[-1].captured_at or unpaired_rears[-1].ingested_at).timestamp()
        f_first_time = (unpaired_fronts[0].captured_at or unpaired_fronts[0].ingested_at).timestamp()
        f_last_time = (unpaired_fronts[-1].captured_at or unpaired_fronts[-1].ingested_at).timestamp()

        gap_uturn = min(abs(f_first_time - r_last_time), abs(r_first_time - f_last_time))
        gap_parallel = abs(f_first_time - r_first_time)
        is_uturn = gap_uturn <= gap_parallel

        if is_uturn:
            if f_first_time >= r_first_time:
                aligned_rears = list(unpaired_rears)
                aligned_fronts = list(reversed(unpaired_fronts))
            else:
                aligned_rears = list(reversed(unpaired_rears))
                aligned_fronts = list(unpaired_fronts)
            walk_desc = f"Temporal Trajectory (U-Turn Walk, Δt turnaround = {int(gap_uturn)}s)"

            t_pair_count = min(len(aligned_rears), len(aligned_fronts))
            for i in range(t_pair_count):
                r_img = aligned_rears[i]
                f_img = aligned_fronts[i]
                canonical_plate = r_img.detected_plate or f_img.detected_plate
                if not canonical_plate:
                    batch_tag = batch.batch_id.split("-")[-1] if batch else "SEQ"
                    pair_num = summary.complete_pairs + len(resolved_pairs) + 1
                    canonical_plate = f"PAIR-{batch_tag.upper()}-{pair_num:02d}"
                note = f"Auto-paired via {walk_desc} | Walk Step #{i+1}"
                resolved_pairs.append((f_img, r_img, canonical_plate, note))

            unpaired_fronts = aligned_fronts[t_pair_count:]
            unpaired_rears = aligned_rears[t_pair_count:]

    # --- Tier 3: Plate OCR Direct Match ---
    matched_f_ids = set()
    matched_r_ids = set()
    for f_img in unpaired_fronts:
        if not f_img.detected_plate:
            continue
        best_r = None
        best_sim = 0.0
        for r_img in unpaired_rears:
            if r_img.id in matched_r_ids or not r_img.detected_plate:
                continue
            sim = _plate_similarity(f_img.detected_plate, r_img.detected_plate)
            if sim >= 0.85 and sim > best_sim:
                best_sim = sim
                best_r = r_img
        if best_r:
            matched_f_ids.add(f_img.id)
            matched_r_ids.add(best_r.id)
            canonical_plate = best_r.detected_plate or f_img.detected_plate
            sim_pct = int(best_sim * 100)
            note = f"Auto-paired via Plate OCR Match ('{canonical_plate}', {sim_pct}% similarity)"
            resolved_pairs.append((f_img, best_r, canonical_plate, note))

    unpaired_fronts = [f for f in unpaired_fronts if f.id not in matched_f_ids]
    unpaired_rears = [r for r in unpaired_rears if r.id not in matched_r_ids]

    # --- Tier 4: Temporal Proximity Alignment (Parallel Walk) ---
    if unpaired_fronts and unpaired_rears:
        unpaired_rears.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))
        unpaired_fronts.sort(key=lambda img: extract_chronological_sort_key(img.original_source_path or img.vault_file, img.captured_at, img.ingested_at))

        r_first_time = (unpaired_rears[0].captured_at or unpaired_rears[0].ingested_at).timestamp()
        f_first_time = (unpaired_fronts[0].captured_at or unpaired_fronts[0].ingested_at).timestamp()
        gap_parallel = abs(f_first_time - r_first_time)

        aligned_rears = list(unpaired_rears)
        aligned_fronts = list(unpaired_fronts)
        walk_desc = f"Temporal Proximity (Parallel Walk, Δt = {int(gap_parallel)}s)"

        t_pair_count = min(len(aligned_rears), len(aligned_fronts))
        for i in range(t_pair_count):
            r_img = aligned_rears[i]
            f_img = aligned_fronts[i]
            canonical_plate = r_img.detected_plate or f_img.detected_plate
            if not canonical_plate:
                batch_tag = batch.batch_id.split("-")[-1] if batch else "SEQ"
                pair_num = summary.complete_pairs + len(resolved_pairs) + 1
                canonical_plate = f"PAIR-{batch_tag.upper()}-{pair_num:02d}"
            note = f"Auto-paired via {walk_desc} | Walk Step #{i+1}"
            resolved_pairs.append((f_img, r_img, canonical_plate, note))

        surplus_fronts = aligned_fronts[t_pair_count:]
        surplus_rears = aligned_rears[t_pair_count:]
    else:
        surplus_fronts = list(unpaired_fronts)
        surplus_rears = list(unpaired_rears)

    # Commit resolved complete pairs
    for f_img, r_img, canonical_plate, note in resolved_pairs:
        full_note = f"{label_prefix}{note}" if label_prefix else note
        VehicleInstallationPair.objects.filter(
            Q(front_image=f_img) | Q(rear_image=r_img) | Q(front_image=r_img) | Q(rear_image=f_img)
        ).exclude(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).delete()

        pair = VehicleInstallationPair.objects.create(
            registration_number_detected=canonical_plate,
            front_image=f_img,
            rear_image=r_img,
            is_complete=True,
            operator_note=full_note,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        r_img.status = EvidenceImage.Status.MATCHED
        f_img.status = EvidenceImage.Status.MATCHED
        r_img.save(update_fields=["status"])
        f_img.save(update_fields=["status"])

        match_pair_to_order(pair)

        paired_image_ids.add(r_img.id)
        paired_image_ids.add(f_img.id)
        summary.complete_pairs += 1
        summary.details.append(f"✓ Pair [{canonical_plate}]: {full_note}")

    return surplus_fronts, surplus_rears


def _associate_batch_sequences(batch, summary: AssociationSummary) -> set:
    """Associates photos within a batch using camera timestamp sequences.

    Handles:
      1. Clustering batch photos into distinct walk sessions based on timestamp gaps (>30 minutes
         or when previous session has completed both orientations).
      2. Handling unknown orientation fallback when one orientation is deficient.
      3. Multi-tier intra-session association (stems, camera sequence, U-turn, plate OCR, parallel).
      4. Cross-session batch reconciliation to pair complementary surplus photos across sessions.
      5. Accurate reporting of true batch-level discrepancies and incomplete pairs.
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

    # 1. Cluster into walk sessions
    sessions = []
    current_session = []
    for img in all_imgs:
        if not current_session:
            current_session.append(img)
        else:
            prev_time = (current_session[-1].captured_at or current_session[-1].ingested_at).timestamp()
            curr_time = (img.captured_at or img.ingested_at).timestamp()
            dt = abs(curr_time - prev_time)

            has_front = any(i.orientation == EvidenceImage.Orientation.FRONT for i in current_session)
            has_rear = any(i.orientation == EvidenceImage.Orientation.REAR for i in current_session)
            is_complete_cycle = has_front and has_rear

            should_split = False
            if dt > 14400:  # > 4 hours: always split (different work shift)
                should_split = True
            elif dt > 1800 and is_complete_cycle:  # > 30 mins and previous session has both sides
                should_split = True

            if should_split:
                sessions.append(current_session)
                current_session = [img]
            else:
                current_session.append(img)
    if current_session:
        sessions.append(current_session)

    from django.db.models import Q
    from core.matcher.order_matcher import match_pair_to_order

    # 2. Process each session independently
    batch_surplus_fronts = []
    batch_surplus_rears = []

    for session_idx, session in enumerate(sessions, 1):
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

        # Align and pair within session
        if fronts and rears:
            s_fronts, s_rears = _align_and_pair_candidates(
                fronts, rears, batch, summary, paired_image_ids, label_prefix=""
            )
            batch_surplus_fronts.extend(s_fronts)
            batch_surplus_rears.extend(s_rears)
        elif fronts and not rears:
            batch_surplus_fronts.extend(fronts)
        elif rears and not fronts:
            batch_surplus_rears.extend(rears)

    # 3. Cross-Session Batch Reconciliation
    if batch_surplus_fronts and batch_surplus_rears:
        final_surplus_fronts, final_surplus_rears = _align_and_pair_candidates(
            batch_surplus_fronts, batch_surplus_rears, batch, summary, paired_image_ids, label_prefix="Cross-Session "
        )
    else:
        final_surplus_fronts = batch_surplus_fronts
        final_surplus_rears = batch_surplus_rears

    # 4. Handle true batch-level surplus and count discrepancies
    total_batch_fronts = len([img for img in all_imgs if img.orientation == EvidenceImage.Orientation.FRONT])
    total_batch_rears = len([img for img in all_imgs if img.orientation == EvidenceImage.Orientation.REAR])

    if final_surplus_fronts and not final_surplus_rears:
        disc_msg = (
            f"Batch {batch.batch_id}: Count discrepancy — "
            f"{total_batch_fronts} Front photo(s) vs {total_batch_rears} Rear photo(s). "
            f"{len(final_surplus_fronts)} REAR photo(s) MISSING!"
        )
        summary.discrepancies.append(disc_msg)
        summary.details.append(f"⚠️  {disc_msg}")

    elif final_surplus_rears and not final_surplus_fronts:
        disc_msg = (
            f"Batch {batch.batch_id}: Count discrepancy — "
            f"{total_batch_fronts} Front photo(s) vs {total_batch_rears} Rear photo(s). "
            f"{len(final_surplus_rears)} FRONT photo(s) MISSING!"
        )
        summary.discrepancies.append(disc_msg)
        summary.details.append(f"⚠️  {disc_msg}")

    elif final_surplus_fronts and final_surplus_rears:
        disc_msg = (
            f"Batch {batch.batch_id}: Count discrepancy — "
            f"{total_batch_fronts} Front photo(s) vs {total_batch_rears} Rear photo(s). "
            f"{len(final_surplus_fronts)} Front(s) and {len(final_surplus_rears)} Rear(s) could not be aligned."
        )
        summary.discrepancies.append(disc_msg)
        summary.details.append(f"⚠️  {disc_msg}")

    # Commit incomplete pairs for final surplus photos
    base_idx = summary.complete_pairs
    for s_idx, f_img in enumerate(final_surplus_fronts, base_idx + 1):
        clean_plate = f_img.detected_plate or f"MISSING-REAR-{batch.batch_id.split('-')[-1].upper()}-{s_idx:02d}"
        VehicleInstallationPair.objects.filter(
            Q(front_image=f_img) | Q(rear_image=f_img)
        ).exclude(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).delete()

        pair = VehicleInstallationPair.objects.create(
            registration_number_detected=clean_plate,
            front_image=f_img,
            rear_image=None,
            is_complete=False,
            verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            operator_note=f"Discrepancy: Rear photo missing in batch sequence ({total_batch_fronts} Fronts vs {total_batch_rears} Rears)",
        )
        f_img.status = EvidenceImage.Status.INCOMPLETE
        f_img.save(update_fields=["status"])
        match_pair_to_order(pair)
        paired_image_ids.add(f_img.id)
        summary.incomplete += 1
        fname = os.path.basename(f_img.original_source_path or f_img.vault_file)
        summary.missing_photos.append({
            "image": f_img,
            "orientation": "FRONT",
            "missing": "REAR",
            "filename": fname,
            "batch": batch.batch_id,
        })
        summary.details.append(f"⚠️  Surplus Front: {fname} (ID {f_img.id.hex[:6]}) is MISSING a Rear counterpart!")

    for s_idx, r_img in enumerate(final_surplus_rears, base_idx + 1):
        clean_plate = r_img.detected_plate or f"MISSING-FRONT-{batch.batch_id.split('-')[-1].upper()}-{s_idx:02d}"
        VehicleInstallationPair.objects.filter(
            Q(front_image=r_img) | Q(rear_image=r_img)
        ).exclude(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).delete()

        pair = VehicleInstallationPair.objects.create(
            registration_number_detected=clean_plate,
            front_image=None,
            rear_image=r_img,
            is_complete=False,
            verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            operator_note=f"Discrepancy: Front photo missing in batch sequence ({total_batch_fronts} Fronts vs {total_batch_rears} Rears)",
        )
        r_img.status = EvidenceImage.Status.INCOMPLETE
        r_img.save(update_fields=["status"])
        match_pair_to_order(pair)
        paired_image_ids.add(r_img.id)
        summary.incomplete += 1
        fname = os.path.basename(r_img.original_source_path or r_img.vault_file)
        summary.missing_photos.append({
            "image": r_img,
            "orientation": "REAR",
            "missing": "FRONT",
            "filename": fname,
            "batch": batch.batch_id,
        })
        summary.details.append(f"⚠️  Surplus Rear: {fname} (ID {r_img.id.hex[:6]}) is MISSING a Front counterpart!")

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

    if target_image.batch_id:
        same_batch_qs = qs.filter(batch_id=target_image.batch_id)
        if same_batch_qs.count() >= top_n:
            qs = same_batch_qs
        else:
            qs = qs.order_by("-ingested_at")[:200]
    else:
        qs = qs.order_by("-ingested_at")[:200]

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
def run_association(batch_id: Optional[str] = None) -> AssociationSummary:
    """Runs full pairwise association:

    1. Batch-level U-Turn & timestamp sequence alignment (for front/rear folders).
    2. Plate-driven grouping for remaining unassociated photos.
    3. Residual unassociated photo sweep to register incomplete pairs for any orphan photos.
    """
    from core.models import IngestionBatch
    from core.matcher.order_matcher import match_pair_to_order
    from django.db.models import Q

    summary = AssociationSummary()
    already_paired = set()

    # Pass 1: U-Turn & Sequence Association across batches
    if batch_id:
        batches = list(IngestionBatch.objects.filter(batch_id=batch_id))
    else:
        batches = list(IngestionBatch.objects.all())

    for batch in batches:
        paired_in_batch = _associate_batch_sequences(batch, summary)
        already_paired.update(paired_in_batch)

    # Pass 2: Plate-driven grouping for remaining photos with detected plates
    excluded_status = {EvidenceImage.Status.SUBMITTED, EvidenceImage.Status.DUPLICATE_SKIPPED}
    qs = (
        EvidenceImage.objects.exclude(status__in=excluded_status)
        .exclude(id__in=already_paired)
        .exclude(detected_plate="")
    )
    if batch_id:
        qs = qs.filter(batch__batch_id=batch_id)

    groups: Dict[str, List[EvidenceImage]] = defaultdict(list)
    for image in qs.order_by("ingested_at"):
        groups[image.detected_plate].append(image)

    for plate, images in groups.items():
        pair = _resolve_group(plate, images)
        summary.groups_processed += 1
        for img in images:
            already_paired.add(img.id)

        if pair.verification_status == VehicleInstallationPair.VerificationStatus.CONFLICT:
            summary.conflicts += 1
        elif pair.is_complete:
            summary.complete_pairs += 1
        else:
            summary.incomplete += 1
            missing_side = "REAR" if pair.front_image and not pair.rear_image else "FRONT"
            present_img = pair.front_image or pair.rear_image
            fname = os.path.basename(present_img.original_source_path or present_img.vault_file) if present_img else "unknown"
            summary.missing_photos.append({
                "image": present_img,
                "orientation": present_img.orientation if present_img else "UNKNOWN",
                "missing": missing_side,
                "filename": fname,
                "batch": present_img.batch.batch_id if present_img and present_img.batch else "",
            })

        summary.details.append(f"{plate}: {pair.verification_status} (images={len(images)})")

    # Pass 3: Residual unassociated photo sweep (ensures NO photo is left without a pair record)
    residual_qs = EvidenceImage.objects.exclude(status__in=excluded_status).exclude(id__in=already_paired)
    if batch_id:
        residual_qs = residual_qs.filter(batch__batch_id=batch_id)

    for res_img in residual_qs:
        existing_pair = VehicleInstallationPair.objects.filter(
            Q(front_image=res_img) | Q(rear_image=res_img)
        ).first()

        if not existing_pair:
            orient_tag = "FRONT" if res_img.orientation == EvidenceImage.Orientation.FRONT else ("REAR" if res_img.orientation == EvidenceImage.Orientation.REAR else "UNKNOWN")
            missing_side = "REAR" if orient_tag == "FRONT" else "FRONT"
            plate_tag = res_img.detected_plate or f"UNPAIRED-{orient_tag}-{res_img.id.hex[:6].upper()}"

            new_pair = VehicleInstallationPair.objects.create(
                registration_number_detected=plate_tag,
                front_image=res_img if orient_tag == "FRONT" else None,
                rear_image=res_img if orient_tag == "REAR" else None,
                is_complete=False,
                verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                operator_note=f"Unpaired {orient_tag} photo: Missing {missing_side} partner",
            )
            res_img.status = EvidenceImage.Status.INCOMPLETE
            res_img.save(update_fields=["status"])
            match_pair_to_order(new_pair)
            summary.incomplete += 1
            fname = os.path.basename(res_img.original_source_path or res_img.vault_file)
            summary.missing_photos.append({
                "image": res_img,
                "orientation": orient_tag,
                "missing": missing_side,
                "filename": fname,
                "batch": res_img.batch.batch_id if res_img.batch else "",
            })
            summary.details.append(f"⚠️  Residual: {fname} (ID {res_img.id.hex[:6]}) has no opposite partner -> Registered as INCOMPLETE pair.")

    return summary
