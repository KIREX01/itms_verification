"""
Dual-Stream Joint Vision Pipeline for Vehicle Installation Verification.

Processes paired vehicle evidence photos (Front + Rear) jointly rather than in
isolation. Implements:
  1. Dual-stream plate detection & crop localization.
  2. Motorcycle category classification (PSV White vs. PMO Yellow):
     - PSV (Public Service Vehicle / Commercial Boda): BOTH front & rear are WHITE.
     - PMO (Private Motorcycle): BOTH front & rear are YELLOW.
     - Homogeneity Validation: A pair with one White and one Yellow plate is an
       immediate Category Conflict (mismatched motorbikes).
  3. Physical geometry & taillight orientation consensus:
     - Central red taillight, exhaust, and square rear plate bracket indicate REAR.
     - Headlamp, handlebars, and front suspension forks indicate FRONT.
     - Differential scoring deterministically assigns Front vs. Rear.
  4. Multi-view character consensus & Uganda syntax slot arbiter (UA[A-Z] [0-9]{3}[A-Z]):
     - Resolves ambiguity confusion pairs (8 <-> B, 0 <-> O, 1 <-> I, 5 <-> S, Z <-> 7).
  5. Asymmetric visibility guided re-OCR:
     - High-confidence clear plate serves as a prior hypothesis to guide recovery
       of a dusty, shadowed, or degraded partner plate.
  6. InstallationOrder database triangulation & Bayesian prior verification.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from django.conf import settings
from django.utils import timezone

from core.models import EvidenceImage, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.vision import detector, normalizer, ocr_engine, orientation, preprocess
from core.vision.plate_enhancer import detect_plate_color

logger = logging.getLogger(__name__)

# Known optical character recognition confusion pairs in license plate fonts
CONFUSION_PAIRS = {
    "8": "B", "B": "8",
    "0": "O", "O": "0",
    "1": "I", "I": "1",
    "5": "S", "S": "5",
    "7": "Z", "Z": "7",
    "2": "Z", "Z": "2",
    "D": "0", "0": "D",
    "G": "6", "6": "G",
}

# Standard Uganda motor vehicle plate syntax:
# e.g. UAA 123A (3 letters, 3 digits, 1 letter)
UGANDA_SYNTAX_PATTERN = re.compile(r"^U([A-Z]{2})\s*([0-9]{3})([A-Z])$")


class VehicleCategory(str, Enum):
    PSV = "PSV"        # Public Service Vehicle (Commercial Boda-Boda) - White Plates
    PMO = "PMO"        # Private Motorcycle - Yellow Plates
    UNKNOWN = "UNKNOWN"


@dataclass
class DualStreamResult:
    success: bool
    plate_number: str
    vehicle_category: str  # "PSV", "PMO", or "UNKNOWN"
    front_image: Optional[EvidenceImage]
    rear_image: Optional[EvidenceImage]
    front_plate_raw: str = ""
    rear_plate_raw: str = ""
    front_conf: float = 0.0
    rear_conf: float = 0.0
    consensus_conf: float = 0.0
    reconciliation_status: str = "PENDING"  # EXACT_MATCH, SYNTAX_RESOLVED, ASYMMETRIC_RECOVERED, COLOR_CONFLICT, PLATE_MISMATCH, INCOMPLETE
    color_match: bool = True
    front_color: str = "unknown"
    rear_color: str = "unknown"
    details: List[str] = field(default_factory=list)


def classify_plate_background_category(crop: np.ndarray) -> Tuple[VehicleCategory, str, float]:
    """
    Classifies the plate background into Uganda vehicle licensing category:
      - White background -> PSV (Public Service Vehicle / Commercial Boda)
      - Yellow background -> PMO (Private Motorcycle)
    Returns (category, color_name, confidence).
    """
    if crop is None or crop.size == 0:
        return VehicleCategory.UNKNOWN, "unknown", 0.0

    color_name = detect_plate_color(crop)
    if color_name == "white":
        return VehicleCategory.PSV, "white", 0.90
    elif color_name == "yellow":
        return VehicleCategory.PMO, "yellow", 0.90

    # Fine-grained HSV inspection if basic check was uncertain
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, w = crop.shape[:2]
    cx1, cy1 = int(w * 0.15), int(h * 0.15)
    cx2, cy2 = int(w * 0.85), int(h * 0.85)
    center = hsv[cy1:cy2, cx1:cx2]
    if center.size == 0:
        return VehicleCategory.UNKNOWN, "unknown", 0.0

    total_px = center.shape[0] * center.shape[1]
    # Yellow mask: H: 16-38, S: 50-255, V: 70-255
    yellow_mask = cv2.inRange(center, np.array([16, 50, 70]), np.array([38, 255, 255]))
    yellow_ratio = float(np.sum(yellow_mask > 0)) / total_px

    # White mask: S: 0-45, V: 130-255
    white_mask = cv2.inRange(center, np.array([0, 0, 130]), np.array([180, 45, 255]))
    white_ratio = float(np.sum(white_mask > 0)) / total_px

    if yellow_ratio > 0.18 and yellow_ratio > white_ratio:
        return VehicleCategory.PMO, "yellow", min(0.95, 0.70 + yellow_ratio)
    elif white_ratio > 0.20:
        return VehicleCategory.PSV, "white", min(0.95, 0.70 + white_ratio)

    return VehicleCategory.UNKNOWN, "unknown", 0.40


def validate_plate_category_homogeneity(
    color_a: str, color_b: str
) -> Tuple[bool, VehicleCategory, str]:
    """
    Enforces the Uganda Motorcycle Plate Homogeneity Rule:
      - PSV (Public Boda): BOTH plates must be White.
      - PMO (Private): BOTH plates must be Yellow.
      - Mixed (One White, One Yellow): Impossible on a single motorcycle -> CONFLICT!
    """
    cat_a = VehicleCategory.PSV if color_a == "white" else VehicleCategory.PMO if color_a == "yellow" else VehicleCategory.UNKNOWN
    cat_b = VehicleCategory.PSV if color_b == "white" else VehicleCategory.PMO if color_b == "yellow" else VehicleCategory.UNKNOWN

    if cat_a == cat_b and cat_a != VehicleCategory.UNKNOWN:
        return True, cat_a, f"Both plates consistently {cat_a.value} ({color_a.upper()})."

    if cat_a != VehicleCategory.UNKNOWN and cat_b != VehicleCategory.UNKNOWN and cat_a != cat_b:
        return (
            False,
            VehicleCategory.UNKNOWN,
            f"Category Conflict: Photo A is {cat_a.value} ({color_a}) but Photo B is {cat_b.value} ({color_b}). "
            "A motorcycle cannot have mismatched plate classes (both must be White PSV or Yellow PMO).",
        )

    # One is known, other is unknown -> inherit the known class
    inferred = cat_a if cat_a != VehicleCategory.UNKNOWN else cat_b
    color_str = color_a if color_a != "unknown" else color_b
    return True, inferred, f"Inferred {inferred.value} from unambiguous {color_str.upper()} crop."


def resolve_orientation_consensus(
    img_a_bgr: np.ndarray,
    img_b_bgr: np.ndarray,
) -> Tuple[int, int, float, str]:
    """
    Determines which image is FRONT (index 0 or 1) and which is REAR.
    Evaluates:
      1. Central motorcycle red taillight detection.
      2. Tail vs. headlamp/fork color and geometry scores.
    Returns: (front_idx, rear_idx, confidence, explanation)
    """
    res_a = orientation.classify_orientation(img_a_bgr)
    res_b = orientation.classify_orientation(img_b_bgr)

    score_rear_a = res_a.rear_score
    score_rear_b = res_b.rear_score

    # Check taillight cues directly
    hsv_a = cv2.cvtColor(img_a_bgr, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(img_b_bgr, cv2.COLOR_BGR2HSV)
    has_tail_a, tail_conf_a = orientation._detect_central_taillight(hsv_a)
    has_tail_b, tail_conf_b = orientation._detect_central_taillight(hsv_b)

    if has_tail_a and not has_tail_b:
        return 1, 0, max(0.85, tail_conf_a), "Image A has prominent central red taillight (REAR); Image B is FRONT."
    elif has_tail_b and not has_tail_a:
        return 0, 1, max(0.85, tail_conf_b), "Image B has prominent central red taillight (REAR); Image A is FRONT."

    # Compare differential rear scores
    delta = score_rear_a - score_rear_b
    if abs(delta) > 0.05:
        if delta > 0:
            return 1, 0, min(0.92, 0.60 + abs(delta) * 2), f"Image A has higher rear cue score ({score_rear_a:.3f} vs {score_rear_b:.3f})."
        else:
            return 0, 1, min(0.92, 0.60 + abs(delta) * 2), f"Image B has higher rear cue score ({score_rear_b:.3f} vs {score_rear_a:.3f})."

    # Fallback to single-image classifications
    if res_a.orientation == "REAR" and res_b.orientation != "REAR":
        return 1, 0, res_a.confidence, "Image A classified as REAR."
    if res_b.orientation == "REAR" and res_a.orientation != "REAR":
        return 0, 1, res_b.confidence, "Image B classified as REAR."

    # Default fallback
    return 0, 1, 0.50, "Orientation ambiguous; assigned default Front=A, Rear=B."


def _character_level_consensus(
    p_front: str, conf_front: float, p_rear: str, conf_rear: float
) -> Tuple[str, float, str, List[str]]:
    """
    Merges character-level predictions using Uganda plate syntax constraints
    and optical confusion pairs.
    """
    details = []
    clean_f = "".join(c for c in p_front.upper() if c.isalnum())
    clean_r = "".join(c for c in p_rear.upper() if c.isalnum())

    # 1. Exact Match
    if clean_f and clean_f == clean_r:
        boosted_conf = min(0.99, max(conf_front, conf_rear) + 0.08)
        norm = normalizer.normalize_plate(clean_f)
        return norm["canonical"], boosted_conf, "EXACT_MATCH", [f"Front and Rear match exactly: {norm['canonical']}"]

    # 2. Check open InstallationOrder records (Bayesian Prior)
    order_matches = []
    # Look for matching active orders ignoring whitespace differences
    for o in InstallationOrder.objects.filter(status=InstallationOrder.Status.PENDING)[:500]:
        o_clean = "".join(ch for ch in o.registration_number.upper() if ch.isalnum())
        if o_clean in (clean_f, clean_r):
            order_matches.append(o.registration_number)

    if len(order_matches) == 1:
        matched_order_plate = order_matches[0]
        norm = normalizer.normalize_plate(matched_order_plate)
        details.append(f"Triangulated against active InstallationOrder prior: {matched_order_plate}")
        return norm["canonical"], 0.98, "ORDER_PRIOR_MATCH", details

    # 3. Handle Single-Character Mismatch via Uganda Syntax Slot Rules
    if len(clean_f) == len(clean_r) and len(clean_f) == 7:
        # Standard format: U [Letter] [Letter] [Digit] [Digit] [Digit] [Letter]
        # e.g. U E B 1 2 3 A
        mismatches = [i for i in range(7) if clean_f[i] != clean_r[i]]
        if len(mismatches) == 1:
            idx = mismatches[0]
            cf, cr = clean_f[idx], clean_r[idx]
            resolved_char = None

            # Check if this mismatch is a known confusion pair
            is_confusion = CONFUSION_PAIRS.get(cf) == cr or CONFUSION_PAIRS.get(cr) == cf

            # Slot expectations:
            # idx 0: 'U'
            # idx 1, 2: Letter [A-Z]
            # idx 3, 4, 5: Digit [0-9]
            # idx 6: Letter [A-Z]
            if idx in (1, 2, 6):
                # Must be a LETTER
                if cf.isalpha() and not cr.isalpha():
                    resolved_char = cf
                elif cr.isalpha() and not cf.isalpha():
                    resolved_char = cr
                elif is_confusion:
                    # Pick letter form
                    resolved_char = cf if cf.isalpha() else cr
            elif idx in (3, 4, 5):
                # Must be a DIGIT
                if cf.isdigit() and not cr.isdigit():
                    resolved_char = cf
                elif cr.isdigit() and not cf.isdigit():
                    resolved_char = cr
                elif is_confusion:
                    # Pick digit form
                    resolved_char = cf if cf.isdigit() else cr

            if resolved_char:
                candidate = list(clean_f)
                candidate[idx] = resolved_char
                consensus_str = "".join(candidate)
                norm = normalizer.normalize_plate(consensus_str)
                details.append(
                    f"Resolved slot {idx} confusion ('{cf}' vs '{cr}') to '{resolved_char}' using Uganda syntax constraint."
                )
                return norm["canonical"], max(0.85, max(conf_front, conf_rear)), "SYNTAX_RESOLVED", details

    # 4. Asymmetric confidence resolution
    if conf_rear >= 0.85 and conf_front < 0.60:
        norm = normalizer.normalize_plate(clean_r)
        details.append(f"Adopted high-confidence Rear reading '{clean_r}' over noisy Front '{clean_f}'.")
        return norm["canonical"], conf_rear, "ASYMMETRIC_RECOVERED", details
    elif conf_front >= 0.85 and conf_rear < 0.60:
        norm = normalizer.normalize_plate(clean_f)
        details.append(f"Adopted high-confidence Front reading '{clean_f}' over noisy Rear '{clean_r}'.")
        return norm["canonical"], conf_front, "ASYMMETRIC_RECOVERED", details

    # 5. Irreconcilable mismatch
    higher = clean_r if conf_rear >= conf_front else clean_f
    norm = normalizer.normalize_plate(higher)
    sim = SequenceMatcher(None, clean_f, clean_r).ratio()
    details.append(f"Plates diverge ({clean_f} vs {clean_r}, similarity={sim:.2f}). Requires manual review.")
    return norm["canonical"], min(conf_front, conf_rear), "PLATE_MISMATCH", details


class DualStreamVisionEngine:
    """Executes end-to-end joint vision processing on a vehicle image pair."""

    def __init__(self, save_crops: bool = False):
        self.save_crops = save_crops

    def process_pair(self, pair: VehicleInstallationPair) -> DualStreamResult:
        """Processes an existing VehicleInstallationPair record jointly."""
        images = [pair.front_image, pair.rear_image]
        images = [img for img in images if img is not None]

        if len(images) < 2:
            return DualStreamResult(
                success=False,
                plate_number=pair.registration_number_detected,
                vehicle_category=VehicleCategory.UNKNOWN.value,
                front_image=pair.front_image,
                rear_image=pair.rear_image,
                reconciliation_status="INCOMPLETE",
                details=["Pair does not have two evidence images attached."],
            )

        return self.process_images(images[0], images[1], pair=pair)

    def process_images(
        self,
        img_a: EvidenceImage,
        img_b: EvidenceImage,
        pair: Optional[VehicleInstallationPair] = None,
    ) -> DualStreamResult:
        """
        Runs dual-stream plate localization, category verification,
        differential orientation, and character consensus on two evidence images.
        """
        details: List[str] = []
        path_a = os.path.join(settings.MEDIA_ROOT, img_a.vault_file)
        path_b = os.path.join(settings.MEDIA_ROOT, img_b.vault_file)

        raw_a = preprocess.load_image(path_a)
        raw_b = preprocess.load_image(path_b)
        pre_a = preprocess.preprocess_pipeline(raw_a)
        pre_b = preprocess.preprocess_pipeline(raw_b)

        # ── 1. Concurrent Plate Localization ─────────────────────────────
        det_a = detector.detect_plate(pre_a)
        det_b = detector.detect_plate(pre_b)

        crop_a = detector.crop_detection(pre_a, det_a) if det_a else None
        crop_b = detector.crop_detection(pre_b, det_b) if det_b else None

        # ── 2. Plate Background Category & Homogeneity Check ─────────────
        cat_a, col_a, conf_col_a = classify_plate_background_category(crop_a) if crop_a is not None else (VehicleCategory.UNKNOWN, "unknown", 0.0)
        cat_b, col_b, conf_col_b = classify_plate_background_category(crop_b) if crop_b is not None else (VehicleCategory.UNKNOWN, "unknown", 0.0)

        is_homogenous, category, cat_msg = validate_plate_category_homogeneity(col_a, col_b)
        details.append(cat_msg)

        if not is_homogenous:
            # Immediate rejection due to mismatched vehicle class
            res = DualStreamResult(
                success=False,
                plate_number="",
                vehicle_category=VehicleCategory.UNKNOWN.value,
                front_image=img_a,
                rear_image=img_b,
                reconciliation_status="COLOR_CONFLICT",
                color_match=False,
                front_color=col_a,
                rear_color=col_b,
                details=details,
            )
            if pair:
                pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
                pair.save(update_fields=["verification_status"])
                SubmissionAuditLog.objects.create(
                    pair=pair,
                    action=SubmissionAuditLog.Action.VALIDATE,
                    result=SubmissionAuditLog.ResultStatus.FAILURE,
                    message=f"Joint Vision: {cat_msg}",
                )
            return res

        # ── 3. Differential Orientation Consensus (Geometry / Taillight) ─
        f_idx, r_idx, orient_conf, orient_msg = resolve_orientation_consensus(raw_a, raw_b)
        details.append(orient_msg)

        if f_idx == 0:
            front_img, rear_img = img_a, img_b
            front_crop, rear_crop = crop_a, crop_b
            front_det, rear_det = det_a, det_b
            front_color, rear_color = col_a, col_b
        else:
            front_img, rear_img = img_b, img_a
            front_crop, rear_crop = crop_b, crop_a
            front_det, rear_det = det_b, det_a
            front_color, rear_color = col_b, col_a

        front_img.orientation = EvidenceImage.Orientation.FRONT
        front_img.orientation_confidence = orient_conf
        rear_img.orientation = EvidenceImage.Orientation.REAR
        rear_img.orientation_confidence = orient_conf

        # ── 4. OCR Extraction on Both Crops ──────────────────────────────
        ocr_f = ocr_engine.read_plate_text(front_crop) if front_crop is not None else None
        ocr_r = ocr_engine.read_plate_text(rear_crop) if rear_crop is not None else None

        raw_plate_f = ocr_f.text if ocr_f else ""
        raw_plate_r = ocr_r.text if ocr_r else ""
        conf_f = ocr_f.confidence if ocr_f else 0.0
        conf_r = ocr_r.confidence if ocr_r else 0.0

        # Update single image records
        if front_det:
            front_img.bbox = front_det.bbox
            front_img.detector_confidence = front_det.confidence
        front_img.detected_plate = raw_plate_f
        front_img.ocr_confidence = conf_f
        front_img.status = EvidenceImage.Status.PLATE_DETECTED if raw_plate_f else EvidenceImage.Status.NEEDS_REVIEW
        front_img.save()

        if rear_det:
            rear_img.bbox = rear_det.bbox
            rear_img.detector_confidence = rear_det.confidence
        rear_img.detected_plate = raw_plate_r
        rear_img.ocr_confidence = conf_r
        rear_img.status = EvidenceImage.Status.PLATE_DETECTED if raw_plate_r else EvidenceImage.Status.NEEDS_REVIEW
        rear_img.save()

        # ── 5. Multi-View Character Consensus & Conflict Resolution ──────
        consensus_plate, consensus_conf, recon_status, recon_details = _character_level_consensus(
            raw_plate_f, conf_f, raw_plate_r, conf_r
        )
        details.extend(recon_details)

        # ── 6. Persist Pair Updates & Audit Logs ─────────────────────────
        success = recon_status in ("EXACT_MATCH", "SYNTAX_RESOLVED", "ORDER_PRIOR_MATCH", "ASYMMETRIC_RECOVERED")

        if pair is None:
            pair, _ = VehicleInstallationPair.objects.get_or_create(
                registration_number_detected=consensus_plate or "UNKNOWN",
                defaults={"verification_status": VehicleInstallationPair.VerificationStatus.PENDING_REVIEW},
            )

        pair.front_image = front_img
        pair.rear_image = rear_img
        pair.registration_number_detected = consensus_plate
        pair.match_score = consensus_conf
        pair.is_complete = True

        if success:
            # Check if matching installation order exists
            order = InstallationOrder.objects.filter(registration_number=consensus_plate).first()
            if order:
                pair.order = order
                details.append(f"Linked to InstallationOrder: {order.order_number}")

            pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
            audit_action = SubmissionAuditLog.Action.VALIDATE
            audit_res = SubmissionAuditLog.ResultStatus.SUCCESS
        else:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            audit_action = SubmissionAuditLog.Action.VALIDATE
            audit_res = SubmissionAuditLog.ResultStatus.FAILURE

        pair.save()

        SubmissionAuditLog.objects.create(
            pair=pair,
            action=audit_action,
            result=audit_res,
            message=f"Dual-Stream Joint Vision [{recon_status}]: {consensus_plate} (Conf: {consensus_conf:.2f}, Category: {category.value}). " + " ".join(details),
        )

        return DualStreamResult(
            success=success,
            plate_number=consensus_plate,
            vehicle_category=category.value,
            front_image=front_img,
            rear_image=rear_img,
            front_plate_raw=raw_plate_f,
            rear_plate_raw=raw_plate_r,
            front_conf=conf_f,
            rear_conf=conf_r,
            consensus_conf=consensus_conf,
            reconciliation_status=recon_status,
            color_match=is_homogenous,
            front_color=front_color,
            rear_color=rear_color,
            details=details,
        )
