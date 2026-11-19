"""
Django Management Command: prepare_char_ocr_dataset

Mines hard OCR cases from VehicleInstallationPair / EvidenceImage records,
optionally downloads missing or requested plate photos directly from the
ITMS Archive (stock.itms.ug), crops the license plates, performs forced-alignment
character bounding-box labeling using ground-truth registration numbers, and
packages a ready-to-train 36-class YOLO character OCR dataset for Google Colab.

Usage examples:
  # 1. Mine hard cases & build YOLO character dataset from local vault:
  python manage.py prepare_char_ocr_dataset --output-dir dataset_char_ocr --zip

  # 2. Download specific tricky plate numbers from ITMS archive and include them:
  python manage.py prepare_char_ocr_dataset --plates "UMA 282PG,UMA 835DS" --download-archive

  # 3. Backfill pruned/missing hard-case photos from ITMS archive automatically:
  python manage.py prepare_char_ocr_dataset --download-archive --max-archive-downloads 50 --zip
"""
from __future__ import annotations

import csv
import json
import logging
import os
import random
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import EvidenceImage, InstallationOrder, VehicleInstallationPair
from core.services import vault_service
from core.vision import character_recognizer, detector, normalizer, preprocess

logger = logging.getLogger(__name__)

# Exact 36-class vocabulary matching models/plate_char_reader.pt (0-9, A-Z)
CHARS_VOCAB = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHAR_TO_CLASS_ID: Dict[str, int] = {ch: idx for idx, ch in enumerate(CHARS_VOCAB)}


def clean_alnum(text: str) -> str:
    """Upper-case alphanumeric characters only."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def classify_hard_case(
    img: EvidenceImage,
    gt_plate: str,
    pair: Optional[VehicleInstallationPair] = None,
) -> Tuple[bool, List[str], List[Tuple[str, str]]]:
    """
    Determines whether an EvidenceImage gave the OCR model a hard time.

    Returns:
        (is_hard_case, reasons_list, char_confusions_list[(gt_char, ocr_char)])
    """
    reasons: List[str] = []
    confusions: List[Tuple[str, str]] = []

    gt_clean = clean_alnum(gt_plate)
    raw_clean = clean_alnum(img.raw_ocr_text)
    det_clean = clean_alnum(img.detected_plate)
    ocr_sample = raw_clean or det_clean

    # 1. Direct OCR mismatch against human-verified ground truth
    if gt_clean:
        if not ocr_sample:
            reasons.append("OCR_EMPTY_READ")
        elif ocr_sample != gt_clean:
            reasons.append(f"OCR_MISMATCH({ocr_sample}!={gt_clean})")
            # Record character-level confusions when lengths are close
            if len(ocr_sample) == len(gt_clean):
                for g_ch, o_ch in zip(gt_clean, ocr_sample):
                    if g_ch != o_ch:
                        confusions.append((g_ch, o_ch))
            else:
                # Align suffix/prefix if possible
                min_len = min(len(gt_clean), len(ocr_sample))
                for i in range(min_len):
                    if gt_clean[i] != ocr_sample[i]:
                        confusions.append((gt_clean[i], ocr_sample[i]))

    # 2. Rescued via Order Prior or Manual Operator Override
    if img.order_guided_plate and clean_alnum(img.order_guided_plate) == gt_clean and raw_clean != gt_clean:
        reasons.append("RESCUED_BY_ORDER_PRIOR")

    if pair is not None:
        if pair.is_manual_override or pair.matched_via in ("MANUAL", "REPAIR"):
            reasons.append(f"MANUAL_OVERRIDE({pair.matched_via})")
        elif pair.matched_via == "ORDER_PRIOR" and raw_clean != gt_clean:
            reasons.append("PAIR_ORDER_PRIOR_GUIDED")

    # 3. Low OCR confidence
    if img.ocr_confidence is not None and img.ocr_confidence < 0.78:
        reasons.append(f"LOW_OCR_CONF({img.ocr_confidence:.2f})")

    # 4. Low Detector confidence
    if img.detector_confidence is not None and img.detector_confidence < 0.50:
        reasons.append(f"LOW_DET_CONF({img.detector_confidence:.2f})")

    return (len(reasons) > 0), reasons, confusions


def extract_plate_crop(full_img: np.ndarray, bbox: Optional[List[int]] = None) -> Tuple[Optional[np.ndarray], List[int]]:
    """
    Extracts the localized plate crop from a full vehicle photo.
    Uses saved bbox if valid, otherwise runs detector.detect_plate().
    """
    h, w = full_img.shape[:2]

    if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            # Stored bbox in EvidenceImage is in 720p normalized height space (ph=720)
            ph = 720
            pw = max(1, int(w * (720.0 / float(max(1, h)))))
            if x2 <= pw and y2 <= ph and (w > pw * 1.05 or h > ph * 1.05):
                # Scale from preprocessed space to raw image resolution
                sx = w / float(pw)
                sy = h / float(ph)
                x1, y1, x2, y2 = int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)

            # Add 5% padding around plate so outer characters aren't clipped
            bw = max(1, x2 - x1)
            bh = max(1, y2 - y1)
            pad_x = int(bw * 0.05)
            pad_y = int(bh * 0.06)
            cx1 = max(0, x1 - pad_x)
            cy1 = max(0, y1 - pad_y)
            cx2 = min(w, x2 + pad_x)
            cy2 = min(h, y2 + pad_y)
            if cx2 - cx1 >= 24 and cy2 - cy1 >= 14:
                return full_img[cy1:cy2, cx1:cx2].copy(), [cx1, cy1, cx2, cy2]
        except Exception:
            pass

    # Fallback: run live detector on preprocessed image and scale back
    pre = preprocess.preprocess_pipeline(full_img)
    ph, pw = pre.shape[:2]
    det = detector.detect_plate(pre)
    if det is None:
        return None, []

    sx = w / float(pw)
    sy = h / float(ph)
    x1, y1, x2, y2 = det.bbox
    rx1 = max(0, int(x1 * sx))
    ry1 = max(0, int(y1 * sy))
    rx2 = min(w, int(x2 * sx))
    ry2 = min(h, int(y2 * sy))

    bw = max(1, rx2 - rx1)
    bh = max(1, ry2 - ry1)
    pad_x = int(bw * 0.05)
    pad_y = int(bh * 0.06)
    cx1 = max(0, rx1 - pad_x)
    cy1 = max(0, ry1 - pad_y)
    cx2 = min(w, rx2 + pad_x)
    cy2 = min(h, ry2 + pad_y)
    if cx2 - cx1 < 24 or cy2 - cy1 < 14:
        return None, []
    return full_img[cy1:cy2, cx1:cx2].copy(), [cx1, cy1, cx2, cy2]


def force_align_character_boxes(
    plate_crop: np.ndarray,
    gt_plate: str,
    rec: character_recognizer.DirectCharacterRecognizer,
) -> Tuple[List[Tuple[int, float, float, float, float]], str]:
    """
    Runs the existing character detector to localize character boxes on `plate_crop`,
    then force-aligns the detected boxes with the verified ground-truth plate string `gt_plate`.

    Why this works so well for Hard Cases:
      The existing model almost always localizes the character rectangles [x1, y1, x2, y2]
      accurately, even when it confuses the character class (e.g. '8' vs 'B', '0' vs 'D', '5' vs 'S').
      By aligning the ordered boxes against `gt_plate`, we assign the 100% TRUE class ID
      to the misread character's bounding box!

    Returns:
      (yolo_labels[(class_id, xc, yc, w, h)], alignment_method)
    """
    gt_clean = clean_alnum(gt_plate)
    if not gt_clean or len(gt_clean) < 5:
        return [], "INVALID_GT"

    ch, cw = plate_crop.shape[:2]
    model = rec.get_model()
    raw_boxes: List[character_recognizer.CharacterBox] = []
    if model is not None:
        try:
            raw_boxes = rec._run_inference(model, plate_crop)
        except Exception:
            raw_boxes = []

    cleaned = character_recognizer.clean_plate_boxes_advanced(raw_boxes, crop_w=float(cw), crop_h=float(ch))
    ordered = character_recognizer.order_character_boxes(cleaned)

    # Strip leading emblem/noise box if count is len(gt_clean) + 1 and box[0] is not 'U'
    if len(ordered) > len(gt_clean):
        pred_str = "".join(b.char.upper() for b in ordered)
        u_idx = pred_str.find("U")
        if u_idx > 0 and len(ordered) - u_idx >= len(gt_clean):
            ordered = ordered[u_idx : u_idx + len(gt_clean)]
        elif len(ordered) > len(gt_clean):
            # Keep top-N by confidence while preserving reading order
            top_set = set(
                id(b)
                for b in sorted(ordered, key=lambda x: x.confidence, reverse=True)[: len(gt_clean)]
            )
            ordered = [b for b in ordered if id(b) in top_set]

    def box_to_yolo(b: character_recognizer.CharacterBox, target_char: str) -> Tuple[int, float, float, float, float]:
        cls_id = CHAR_TO_CLASS_ID[target_char]
        x1 = max(0.0, min(float(cw), b.x1))
        y1 = max(0.0, min(float(ch), b.y1))
        x2 = max(x1 + 1.0, min(float(cw), b.x2))
        y2 = max(y1 + 1.0, min(float(ch), b.y2))
        bw = x2 - x1
        bh = y2 - y1
        xc = (x1 + bw / 2.0) / float(cw)
        yc = (y1 + bh / 2.0) / float(ch)
        nw = bw / float(cw)
        nh = bh / float(ch)
        return (cls_id, round(xc, 6), round(yc, 6), round(nw, 6), round(nh, 6))

    # Case 1: Exact box count match -> 1-to-1 Forced Alignment with Ground Truth!
    if len(ordered) == len(gt_clean):
        labels = [box_to_yolo(b, gt_ch) for b, gt_ch in zip(ordered, gt_clean)]
        return labels, "FORCED_ALIGN_EXACT_COUNT"

    # Case 2: Standard Ugandan 2-line motorcycle plate geometric layout fallback
    # (Top row: 3 letters e.g. UMA on right 72% after flag emblem; Bottom row: 4-5 chars e.g. 835DS)
    aspect = cw / float(max(1, ch))
    if len(gt_clean) in (7, 8) and gt_clean.startswith("U"):
        if aspect <= 2.3:
            # 2-row motorcycle plate layout
            top_chars = gt_clean[:3]
            bot_chars = gt_clean[3:]
            labels = []
            # Top row: y from 0.10 to 0.48, x from 0.28 to 0.92 (flag occupies left ~25%)
            top_x_start, top_x_end = 0.28, 0.90
            top_step = (top_x_end - top_x_start) / float(len(top_chars))
            for i, c in enumerate(top_chars):
                xc = top_x_start + (i + 0.5) * top_step
                yc = 0.29
                nw = top_step * 0.86
                nh = 0.36
                labels.append((CHAR_TO_CLASS_ID[c], round(xc, 6), round(yc, 6), round(nw, 6), round(nh, 6)))

            # Bottom row: y from 0.52 to 0.92, x from 0.08 to 0.94
            bot_x_start, bot_x_end = 0.08, 0.94
            bot_step = (bot_x_end - bot_x_start) / float(len(bot_chars))
            for i, c in enumerate(bot_chars):
                xc = bot_x_start + (i + 0.5) * bot_step
                yc = 0.72
                nw = bot_step * 0.86
                nh = 0.38
                labels.append((CHAR_TO_CLASS_ID[c], round(xc, 6), round(yc, 6), round(nw, 6), round(nh, 6)))

            return labels, "GEOMETRIC_2ROW_TEMPLATE"
        else:
            # 1-row standard vehicle plate layout
            labels = []
            x_start, x_end = 0.14, 0.95
            step = (x_end - x_start) / float(len(gt_clean))
            for i, c in enumerate(gt_clean):
                xc = x_start + (i + 0.5) * step
                yc = 0.52
                nw = step * 0.85
                nh = 0.68
                labels.append((CHAR_TO_CLASS_ID[c], round(xc, 6), round(yc, 6), round(nw, 6), round(nh, 6)))
            return labels, "GEOMETRIC_1ROW_TEMPLATE"

    return [], "UNALIGNED"


class Command(BaseCommand):
    help = (
        "Mines hard OCR cases, optionally downloads plate photos from ITMS Archive, "
        "and builds a 36-class YOLO character recognition dataset for Google Colab."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="training/datasets/dataset_char_ocr",
            help="Destination directory for the YOLO character OCR dataset (default: training/datasets/dataset_char_ocr).",
        )
        parser.add_argument(
            "--split",
            type=float,
            default=0.85,
            help="Train/val split ratio (default: 0.85).",
        )
        parser.add_argument(
            "--max-hard",
            type=int,
            default=600,
            help="Maximum number of hard-case samples to export, prioritized by character rarity & mismatch severity (default: 600).",
        )
        parser.add_argument(
            "--max-easy",
            type=int,
            default=400,
            help="Maximum number of easy/clean verified samples to mix with hard cases (default: 400).",
        )
        parser.add_argument(
            "--plates",
            default="",
            help="Comma-separated list of plate or order numbers to download directly from ITMS archive (e.g. 'UMA 282PG,UMA 835DS').",
        )
        parser.add_argument(
            "--plates-file",
            default="",
            help="Optional path to a text/CSV file with plate numbers (one per line) to download from ITMS archive.",
        )
        parser.add_argument(
            "--download-archive",
            action="store_true",
            help="Download photos from ITMS archive for requested --plates and for hard cases whose local vault file was pruned.",
        )
        parser.add_argument(
            "--auto-balance-archive",
            action="store_true",
            help="Automatically find InstallationOrders containing underrepresented characters (C,D,E,G,K,L,R,S,V,W,X,etc.) and download their photos from ITMS archive.",
        )
        parser.add_argument(
            "--max-archive-downloads",
            type=int,
            default=40,
            help="Max orders to download from ITMS archive in one run (default: 40).",
        )
        parser.add_argument(
            "--zip",
            action="store_true",
            help="Create a dataset_char_ocr.zip bundle ready for Google Colab upload.",
        )

    def handle(self, *args, **options):
        out_dir = Path(options["output_dir"])
        split_ratio = float(options["split"])
        max_easy = int(options["max_easy"])
        do_archive = bool(options["download_archive"])
        auto_balance_archive = bool(options.get("auto_balance_archive", False))
        max_archive_dl = int(options["max_archive_downloads"])
        do_zip = bool(options["zip"])

        train_img_dir = out_dir / "images" / "train"
        val_img_dir = out_dir / "images" / "val"
        train_lbl_dir = out_dir / "labels" / "train"
        val_lbl_dir = out_dir / "labels" / "val"
        archive_dl_dir = out_dir / "archive_downloads"
        review_dir = out_dir / "hard_crops_preview"

        for d in (train_img_dir, val_img_dir, train_lbl_dir, val_lbl_dir, archive_dl_dir, review_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 68))
        self.stdout.write(self.style.MIGRATE_HEADING("ITMS Character OCR Dataset Builder & Hard-Case Miner"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 68))

        # ------------------------------------------------------------------
        # Step 1: Collect Explicit Plate Numbers Requested for Archive Fetch
        # ------------------------------------------------------------------
        requested_plates: List[str] = []
        if options["plates"]:
            for p in str(options["plates"]).split(","):
                if p.strip():
                    requested_plates.append(p.strip())

        if options["plates_file"]:
            pf = Path(options["plates_file"])
            if pf.is_file():
                for line in pf.read_text(encoding="utf-8").splitlines():
                    clean_l = line.strip().split(",")[0].strip()
                    if clean_l and not clean_l.startswith("#"):
                        requested_plates.append(clean_l)

        archive_samples: List[Dict[str, Any]] = []
        client = None
        if do_archive or auto_balance_archive or requested_plates:
            from core.services.itms_web_client import ITMSWebClient
            client = ITMSWebClient()

        if requested_plates and client is not None:
            self.stdout.write(f"Fetching {len(requested_plates)} requested plate(s) from ITMS Archive...")
            for ident in requested_plates[:max_archive_dl]:
                res = client.fetch_order_info(
                    ident,
                    download_photos=True,
                    save_dir=archive_dl_dir / clean_alnum(ident),
                )
                if res.get("success"):
                    gt_p = clean_alnum(res.get("registration_number") or ident)
                    for ph in res.get("photos", []):
                        lpath = ph.get("local_path")
                        if lpath and os.path.isfile(lpath):
                            archive_samples.append({
                                "image_path": lpath,
                                "gt_plate": gt_p,
                                "orientation": ph.get("orientation", "UNKNOWN"),
                                "is_hard": True,
                                "reasons": ["REQUESTED_ARCHIVE_DOWNLOAD"],
                                "bbox": None,
                                "raw_ocr": "",
                                "detected_plate": "",
                                "ocr_conf": None,
                            })
                    self.stdout.write(self.style.SUCCESS(f"  + Downloaded archive photos for {gt_p}"))
                else:
                    self.stdout.write(self.style.WARNING(f"  ! Could not download '{ident}': {res.get('error')}"))

        # ------------------------------------------------------------------
        # Step 2: Scan Verified & Linked Pairs to Mine Hard OCR Examples
        # ------------------------------------------------------------------
        verified_pairs = (
            VehicleInstallationPair.objects.filter(
                is_deleted=False,
            )
            .filter(
                Q(verification_status__in=[
                    VehicleInstallationPair.VerificationStatus.SUBMITTED,
                    VehicleInstallationPair.VerificationStatus.APPROVED,
                ])
                | Q(order__isnull=False)
            )
            .select_related("order", "front_image", "rear_image")
            .order_by("-updated_at")
        )

        hard_candidates: List[Dict[str, Any]] = []
        easy_candidates: List[Dict[str, Any]] = []
        pruned_hard_orders: List[Tuple[str, str]] = []  # (order_ident, gt_plate)
        confusion_counter: Counter = Counter()
        reason_counter: Counter = Counter()

        for pair in verified_pairs:
            gt_plate = ""
            if pair.order and pair.order.registration_number:
                gt_plate = clean_alnum(pair.order.registration_number)
            elif pair.manual_plate_override:
                gt_plate = clean_alnum(pair.manual_plate_override)
            elif pair.registration_number_detected:
                gt_plate = clean_alnum(pair.registration_number_detected)

            if not gt_plate or len(gt_plate) < 5:
                continue

            for side, img in (("FRONT", pair.front_image), ("REAR", pair.rear_image)):
                if img is None:
                    continue

                is_hard, reasons, confusions = classify_hard_case(img, gt_plate, pair=pair)
                for r in reasons:
                    reason_counter[r.split("(")[0]] += 1
                for g_c, o_c in confusions:
                    confusion_counter[f"{g_c}->{o_c}"] += 1

                abs_path = ""
                if img.vault_file and not img.is_file_pruned:
                    resolved = vault_service.resolve_vault_path(img.vault_file)
                    if resolved and os.path.isfile(resolved):
                        abs_path = str(resolved)

                if not abs_path:
                    if is_hard and pair.order:
                        ident = pair.order.itms_order_uuid or pair.order.order_number or gt_plate
                        pruned_hard_orders.append((ident, gt_plate))
                    continue

                entry = {
                    "image_id": str(img.id),
                    "pair_id": pair.id,
                    "image_path": abs_path,
                    "gt_plate": gt_plate,
                    "orientation": side,
                    "is_hard": is_hard,
                    "reasons": reasons if reasons else ["CLEAN_VERIFIED"],
                    "bbox": img.bbox,
                    "raw_ocr": img.raw_ocr_text or "",
                    "detected_plate": img.detected_plate or "",
                    "ocr_conf": img.ocr_confidence,
                }
                if is_hard:
                    hard_candidates.append(entry)
                else:
                    easy_candidates.append(entry)

        # Optionally backfill pruned hard-case images from ITMS Archive
        if do_archive and client is not None and pruned_hard_orders:
            unique_pruned = list(dict.fromkeys(pruned_hard_orders))[:max_archive_dl]
            self.stdout.write(f"Backfilling {len(unique_pruned)} pruned hard-case order(s) from ITMS Archive...")
            for ident, gt_p in unique_pruned:
                res = client.fetch_order_info(
                    ident,
                    download_photos=True,
                    save_dir=archive_dl_dir / gt_p,
                )
                if res.get("success"):
                    for ph in res.get("photos", []):
                        lpath = ph.get("local_path")
                        if lpath and os.path.isfile(lpath):
                            archive_samples.append({
                                "image_path": lpath,
                                "gt_plate": gt_p,
                                "orientation": ph.get("orientation", "UNKNOWN"),
                                "is_hard": True,
                                "reasons": ["PRUNED_HARD_CASE_ARCHIVE_BACKFILL"],
                                "bbox": None,
                                "raw_ocr": "",
                                "detected_plate": "",
                                "ocr_conf": None,
                            })

        # Automatically query InstallationOrder for underrepresented characters and download from ITMS Archive
        if auto_balance_archive and client is not None:
            local_char_counts: Counter = Counter()
            existing_plates = set()
            for item in hard_candidates + easy_candidates + archive_samples:
                existing_plates.add(item["gt_plate"])
                for ch in item["gt_plate"]:
                    local_char_counts[ch] += 1

            missing_chars = [ch for ch in CHARS_VOCAB if local_char_counts.get(ch, 0) < 15]
            if missing_chars:
                self.stdout.write(f"Auto-balancing underrepresented characters from ITMS Archive: {missing_chars}")
                candidate_orders = (
                    InstallationOrder.objects.exclude(registration_number="")
                    .filter(Q(is_archived=True) | Q(status__in=["SUBMITTED", "INSTALLED"]) | ~Q(itms_order_uuid=""))
                    .order_by("-updated_at")
                )
                to_fetch: List[Tuple[str, str]] = []
                for ord_obj in candidate_orders:
                    reg = clean_alnum(ord_obj.registration_number)
                    if not reg or reg in existing_plates:
                        continue
                    if any(ch in reg for ch in missing_chars):
                        ident = ord_obj.itms_order_uuid or ord_obj.order_number or reg
                        to_fetch.append((ident, reg))
                        existing_plates.add(reg)
                        for ch in reg:
                            local_char_counts[ch] += 2
                        missing_chars = [ch for ch in CHARS_VOCAB if local_char_counts.get(ch, 0) < 15]
                        if len(to_fetch) >= max_archive_dl or not missing_chars:
                            break

                for ident, gt_p in to_fetch:
                    res = client.fetch_order_info(
                        ident,
                        download_photos=True,
                        save_dir=archive_dl_dir / gt_p,
                    )
                    if res.get("success"):
                        for ph in res.get("photos", []):
                            lpath = ph.get("local_path")
                            if lpath and os.path.isfile(lpath):
                                archive_samples.append({
                                    "image_path": lpath,
                                    "gt_plate": gt_p,
                                    "orientation": ph.get("orientation", "UNKNOWN"),
                                    "is_hard": True,
                                    "reasons": ["AUTO_BALANCE_RARE_CHAR_ARCHIVE"],
                                    "bbox": None,
                                    "raw_ocr": "",
                                    "detected_plate": "",
                                    "ocr_conf": None,
                                })
                        self.stdout.write(self.style.SUCCESS(f"  + Auto-balanced archive photos for {gt_p}"))

        self.stdout.write(f"Scanned {verified_pairs.count()} verified pairs:")
        self.stdout.write(f"  - Hard OCR images on disk : {len(hard_candidates)}")
        self.stdout.write(f"  - Archive-downloaded imgs : {len(archive_samples)}")
        self.stdout.write(f"  - Clean verified images   : {len(easy_candidates)}")
        self.stdout.write(f"  - Pruned hard-case orders : {len(set(pruned_hard_orders))}")

        if confusion_counter:
            top_conf = ", ".join(f"{k} ({v}x)" for k, v in confusion_counter.most_common(12))
            self.stdout.write(self.style.WARNING(f"  - Top Character Confusions: {top_conf}"))

        # ------------------------------------------------------------------
        # Step 3: Rare-Character Balancing + Sample Selection
        # ------------------------------------------------------------------
        random.seed(42)
        max_hard = int(options.get("max_hard", 600))
        # Prioritize samples that contain rare characters (C, D, E, F, G, H, J, K, L, R, S, T, V, W, X, Z)
        char_freq: Counter = Counter()
        for item in hard_candidates + easy_candidates + archive_samples:
            for ch in item["gt_plate"]:
                char_freq[ch] += 1

        def rarity_score(item: Dict[str, Any]) -> float:
            has_mismatch = any("OCR_MISMATCH" in r or "OCR_EMPTY" in r for r in item.get("reasons", []))
            bonus = 2.0 if has_mismatch else 0.0
            return bonus + sum(100.0 / (1.0 + char_freq[ch]) for ch in set(item["gt_plate"]))

        hard_sorted = sorted(hard_candidates, key=rarity_score, reverse=True)
        selected_hard = hard_sorted[:max_hard]

        selected_char_freq: Counter = Counter()
        for item in selected_hard + archive_samples:
            for ch in item["gt_plate"]:
                selected_char_freq[ch] += 1

        def easy_rarity_score(item: Dict[str, Any]) -> float:
            return sum(100.0 / (1.0 + selected_char_freq[ch]) for ch in set(item["gt_plate"]))

        easy_sorted = sorted(easy_candidates, key=easy_rarity_score, reverse=True)
        selected_easy = easy_sorted[:max_easy]

        all_samples = selected_hard + archive_samples + selected_easy
        random.shuffle(all_samples)

        # ------------------------------------------------------------------
        # Step 4: Crop Plates & Force-Align Character Bounding Boxes
        # ------------------------------------------------------------------
        rec = character_recognizer.get_character_recognizer()
        manifest_rows: List[Dict[str, Any]] = []
        exported_count = 0
        exact_align_count = 0
        template_align_count = 0
        skipped_count = 0
        dataset_char_hist: Counter = Counter()

        for idx, sample in enumerate(all_samples):
            try:
                raw_img = preprocess.load_image(sample["image_path"])
            except Exception:
                skipped_count += 1
                continue

            crop, crop_bbox = extract_plate_crop(raw_img, sample.get("bbox"))
            if crop is None or crop.size == 0:
                skipped_count += 1
                continue

            # Standardize height to ~200px (160-240px range) for crisp character resolution & compact Colab bundle size
            ch, cw = crop.shape[:2]
            if ch < 160 or ch > 240:
                target_h = 160.0 if ch < 160 else 200.0
                scale = target_h / float(max(1, ch))
                interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
                crop = cv2.resize(crop, (max(1, int(cw * scale)), int(target_h)), interpolation=interp)

            labels, align_method = force_align_character_boxes(crop, sample["gt_plate"], rec)
            if not labels:
                skipped_count += 1
                continue

            if align_method == "FORCED_ALIGN_EXACT_COUNT":
                exact_align_count += 1
            else:
                template_align_count += 1

            is_train = random.random() < split_ratio
            dest_img_dir = train_img_dir if is_train else val_img_dir
            dest_lbl_dir = train_lbl_dir if is_train else val_lbl_dir

            tag = "hard" if sample["is_hard"] else "clean"
            base_name = f"{sample['gt_plate']}_{sample['orientation'].lower()}_{tag}_{idx:04d}"
            img_file = dest_img_dir / f"{base_name}.jpg"
            lbl_file = dest_lbl_dir / f"{base_name}.txt"

            cv2.imwrite(str(img_file), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            with open(lbl_file, "w", encoding="utf-8") as f:
                for cls_id, xc, yc, nw, nh in labels:
                    f.write(f"{cls_id} {xc} {yc} {nw} {nh}\n")
                    dataset_char_hist[CHARS_VOCAB[cls_id]] += 1

            # Save annotated visual preview for the first 60 hard cases
            if sample["is_hard"] and exported_count < 60:
                vis = crop.copy()
                vh, vw = vis.shape[:2]
                for cls_id, xc, yc, nw, nh in labels:
                    bx1 = int((xc - nw / 2.0) * vw)
                    by1 = int((yc - nh / 2.0) * vh)
                    bx2 = int((xc + nw / 2.0) * vw)
                    by2 = int((yc + nh / 2.0) * vh)
                    cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 0), 1)
                    cv2.putText(
                        vis,
                        CHARS_VOCAB[cls_id],
                        (bx1, max(12, by1 - 2)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (0, 255, 255),
                        1,
                    )
                cv2.imwrite(str(review_dir / f"{base_name}_preview.jpg"), vis)

            manifest_rows.append({
                "filename": img_file.name,
                "split": "train" if is_train else "val",
                "gt_plate": sample["gt_plate"],
                "raw_ocr": sample.get("raw_ocr", ""),
                "detected_plate": sample.get("detected_plate", ""),
                "ocr_confidence": sample.get("ocr_conf", ""),
                "orientation": sample["orientation"],
                "is_hard_case": sample["is_hard"],
                "hard_reasons": "|".join(sample["reasons"]),
                "alignment_method": align_method,
            })
            exported_count += 1

        # ------------------------------------------------------------------
        # Step 5: Write data.yaml, manifest CSV, and Hard-Case Summary JSON
        # ------------------------------------------------------------------
        names_yaml_lines = "\n".join(f"  {idx}: '{ch}'" for idx, ch in enumerate(CHARS_VOCAB))
        yaml_content = f"""# YOLOv8 / YOLOv11 36-Class Ugandan Plate Character OCR Dataset
path: {out_dir.resolve().as_posix()}
train: images/train
val: images/val
nc: 36
names:
{names_yaml_lines}
"""
        yaml_path = out_dir / "data.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")

        manifest_path = out_dir / "dataset_manifest.csv"
        with open(manifest_path, "w", newline="", encoding="utf-8") as mf:
            writer = csv.DictWriter(
                mf,
                fieldnames=[
                    "filename",
                    "split",
                    "gt_plate",
                    "raw_ocr",
                    "detected_plate",
                    "ocr_confidence",
                    "orientation",
                    "is_hard_case",
                    "hard_reasons",
                    "alignment_method",
                ],
            )
            writer.writeheader()
            writer.writerows(manifest_rows)

        summary_report = {
            "total_verified_pairs_scanned": verified_pairs.count(),
            "hard_cases_found": len(hard_candidates),
            "archive_downloaded_images": len(archive_samples),
            "clean_verified_images_available": len(easy_candidates),
            "pruned_hard_case_orders_available_on_archive": len(set(pruned_hard_orders)),
            "pruned_hard_case_plates_sample": [p[1] for p in list(dict.fromkeys(pruned_hard_orders))[:50]],
            "total_plate_crops_exported": exported_count,
            "exact_neural_forced_alignments": exact_align_count,
            "geometric_template_alignments": template_align_count,
            "skipped_no_crop": skipped_count,
            "hard_case_reasons_breakdown": dict(reason_counter.most_common()),
            "top_character_confusions": dict(confusion_counter.most_common(30)),
            "character_class_distribution": {ch: dataset_char_hist.get(ch, 0) for ch in CHARS_VOCAB},
        }
        report_path = out_dir / "hard_cases_report.json"
        report_path.write_text(json.dumps(summary_report, indent=2), encoding="utf-8")

        zip_file_path = None
        if do_zip:
            zip_base = Path(f"{out_dir}_colab")
            zip_file_path = shutil.make_archive(str(zip_base), "zip", root_dir=str(out_dir))

        self.stdout.write("-" * 68)
        self.stdout.write(self.style.SUCCESS(f"Dataset Build Complete! Exported {exported_count} labeled plate crops."))
        self.stdout.write(f"  - Exact Forced-Aligned Crops : {exact_align_count}")
        self.stdout.write(f"  - 2-Row/1-Row Template Crops : {template_align_count}")
        self.stdout.write(f"  - Dataset Config (data.yaml) : {yaml_path.resolve()}")
        self.stdout.write(f"  - Manifest CSV               : {manifest_path.resolve()}")
        self.stdout.write(f"  - Hard-Case Diagnostics JSON : {report_path.resolve()}")
        if zip_file_path:
            self.stdout.write(self.style.SUCCESS(f"  - Colab ZIP Bundle           : {zip_file_path}"))
        self.stdout.write("=" * 68)
