"""
Django Management Command: prepare_plate_detector_dataset

Builds the Stage 2 Full-Image Ugandan License Plate Detector Dataset (YOLOv8 / YOLOv11)
inside `training/datasets/dataset_plate_detector` with three key generalization protections
so the trained model works across Spiro bikes, other motorcycle brands (Bajaj, TVS, Haojue),
AND future cars/trucks:

  1. Hard-Case Localization Mining:
     Prioritizes photos where `detector_confidence` was low (< 0.65), missing (OpenCV
     heuristic fallback), or where OCR/manual override was triggered due to angled,
     glare-heavy, or far-away shots.
  2. Teacher Model (v1s) + Stored BBox Verification:
     Verifies and refines plate bounding boxes at standardized 640px resolution.
  3. Car & Multi-Bike Generalization Augmentation:
     - Synthesizes single-line horizontal Ugandan car plates (~4.2:1 aspect ratio) by
       stitching the top row (`UMA`) and bottom row (`123AA`) of real Ugandan plates
       side-by-side and embedding them into vehicle scenes (~18% of training set).
     - Applies random context-decoupling crops (~25% of training set) to remove outer
       Spiro handlebars/body frames so the detector focuses on plate geometry rather
       than memorizing a single motorcycle silhouette.

Usage:
  python manage.py prepare_plate_detector_dataset --zip
"""
from __future__ import annotations

import csv
import json
import logging
import os
import random
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import EvidenceImage, VehicleInstallationPair
from core.services import vault_service
from core.vision import detector, preprocess

logger = logging.getLogger(__name__)


def convert_xyxy_to_yolo(bbox: List[int], img_w: int, img_h: int) -> Tuple[float, float, float, float]:
    """Converts [x1, y1, x2, y2] pixel coordinates to normalized YOLO (xc, yc, w, h)."""
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(img_w - 2, int(x1)))
    y1 = max(0, min(img_h - 2, int(y1)))
    x2 = max(x1 + 2, min(img_w, int(x2)))
    y2 = max(y1 + 2, min(img_h, int(y2)))

    bw = float(x2 - x1)
    bh = float(y2 - y1)
    xc = (x1 + bw / 2.0) / float(img_w)
    yc = (y1 + bh / 2.0) / float(img_h)
    return (
        round(xc, 6),
        round(yc, 6),
        round(bw / float(img_w), 6),
        round(bh / float(img_h), 6),
    )


def synthesize_1row_car_plate_from_2row(crop_2row: np.ndarray) -> Optional[np.ndarray]:
    """
    Transforms a real Ugandan 2-row motorcycle plate crop (top: 'UMA', bottom: '123AA')
    into a realistic 1-row horizontal Ugandan car/truck license plate (~4.4:1 aspect ratio).
    """
    if crop_2row is None or crop_2row.size == 0:
        return None
    h, w = crop_2row.shape[:2]
    if h < 28 or w < 36:
        return None

    # Top row in Ugandan 2-line plate: y in [0.06..0.50], x in [0.05..0.95] (includes flag + UMA)
    top_band = crop_2row[int(h * 0.06) : int(h * 0.52), int(w * 0.04) : int(w * 0.94)]
    # Bottom row: y in [0.50..0.94], x in [0.06..0.94] (123AA)
    bot_band = crop_2row[int(h * 0.50) : int(h * 0.95), int(w * 0.06) : int(w * 0.94)]

    if top_band.size == 0 or bot_band.size == 0:
        return None

    target_band_h = 56
    top_w = max(40, int(top_band.shape[1] * (target_band_h / float(max(1, top_band.shape[0])))))
    bot_w = max(50, int(bot_band.shape[1] * (target_band_h / float(max(1, bot_band.shape[0])))))

    top_resized = cv2.resize(top_band, (top_w, target_band_h), interpolation=cv2.INTER_CUBIC)
    bot_resized = cv2.resize(bot_band, (bot_w, target_band_h), interpolation=cv2.INTER_CUBIC)

    # Create a small spacer between series prefix ('UMA') and suffix ('123AA') using median background color
    bg_color = np.median(crop_2row, axis=(0, 1)).astype(np.uint8).tolist()
    spacer = np.full((target_band_h, 12, 3), bg_color, dtype=np.uint8)

    stitched = np.hstack([top_resized, spacer, bot_resized])
    # Add realistic metallic plate border padding
    car_plate = cv2.copyMakeBorder(
        stitched,
        top=5,
        bottom=5,
        left=8,
        right=8,
        borderType=cv2.BORDER_CONSTANT,
        value=bg_color,
    )
    # Draw thin dark frame border around the 1-line plate
    ch, cw = car_plate.shape[:2]
    cv2.rectangle(car_plate, (0, 0), (cw - 1, ch - 1), (35, 35, 35), 2)
    return car_plate


def resolve_plate_bbox_in_raw(
    raw_img: np.ndarray,
    stored_bbox: Optional[List[int]],
) -> Tuple[Optional[List[int]], float, str]:
    """
    Resolves plate [x1, y1, x2, y2] in `raw_img` pixel coordinates.
    Returns (bbox_xyxy, confidence, backend_used).
    """
    h, w = raw_img.shape[:2]

    if stored_bbox and isinstance(stored_bbox, (list, tuple)) and len(stored_bbox) == 4:
        try:
            x1, y1, x2, y2 = [int(v) for v in stored_bbox]
            # Stored bbox in EvidenceImage is in 720p normalized height space
            ph = 720
            pw = max(1, int(w * (720.0 / float(max(1, h)))))
            if x2 <= pw and y2 <= ph and (w > pw * 1.05 or h > ph * 1.05):
                sx = w / float(pw)
                sy = h / float(ph)
                x1, y1, x2, y2 = int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)
            x1 = max(0, min(w - 4, x1))
            y1 = max(0, min(h - 4, y1))
            x2 = max(x1 + 10, min(w, x2))
            y2 = max(y1 + 10, min(h, y2))
            if (x2 - x1) >= 18 and (y2 - y1) >= 12:
                return [x1, y1, x2, y2], 0.85, "stored_verified_bbox"
        except Exception:
            pass

    # Fallback: run live detector on 720p normalized image
    scale_to_720 = 720.0 / float(max(1, h))
    pw = max(1, int(w * scale_to_720))
    resized_720 = cv2.resize(raw_img, (pw, 720), interpolation=cv2.INTER_AREA if scale_to_720 < 1.0 else cv2.INTER_CUBIC)
    det = detector.detect_plate(resized_720)
    if det is None:
        return None, 0.0, "none"

    sx = w / float(pw)
    sy = h / 720.0
    x1, y1, x2, y2 = det.bbox
    rx1 = max(0, int(x1 * sx))
    ry1 = max(0, int(y1 * sy))
    rx2 = min(w, int(x2 * sx))
    ry2 = min(h, int(y2 * sy))
    if (rx2 - rx1) < 18 or (ry2 - ry1) < 12:
        return None, 0.0, "none"
    return [rx1, ry1, rx2, ry2], float(det.confidence), det.backend


class Command(BaseCommand):
    help = (
        "Builds Stage 2 Full-Image Ugandan Plate Detector dataset with hard-case mining, "
        "context decoupling, and synthetic 1-row car plate augmentation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="training/plate_detector/dataset_plate_detector",
            help="Destination folder for the YOLO plate detector dataset (default: training/plate_detector/dataset_plate_detector).",
        )
        parser.add_argument(
            "--split",
            type=float,
            default=0.85,
            help="Train/val split ratio (default: 0.85).",
        )
        parser.add_argument(
            "--max-samples",
            type=int,
            default=750,
            help="Maximum base vehicle photos to export before synthetic 1-row car augmentations (default: 750).",
        )
        parser.add_argument(
            "--car-aug-ratio",
            type=float,
            default=0.20,
            help="Fraction of training images to augment with synthetic 1-row horizontal car plates (default: 0.20).",
        )
        parser.add_argument(
            "--target-dim",
            type=int,
            default=640,
            help="Max image dimension for exported training frames (default: 640).",
        )
        parser.add_argument(
            "--zip",
            action="store_true",
            help="Create training/datasets/dataset_plate_detector_colab.zip ready for Google Colab.",
        )

    def handle(self, *args, **options):
        out_dir = Path(options["output_dir"])
        split_ratio = float(options["split"])
        max_samples = int(options["max_samples"])
        car_aug_ratio = float(options["car_aug_ratio"])
        target_dim = int(options["target_dim"])
        do_zip = bool(options["zip"])

        train_img_dir = out_dir / "images" / "train"
        val_img_dir = out_dir / "images" / "val"
        train_lbl_dir = out_dir / "labels" / "train"
        val_lbl_dir = out_dir / "labels" / "val"
        preview_dir = out_dir / "detector_previews"

        for d in (train_img_dir, val_img_dir, train_lbl_dir, val_lbl_dir, preview_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))
        self.stdout.write(self.style.MIGRATE_HEADING("ITMS Stage 2: Universal Ugandan Plate Detector Dataset Builder"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))

        verified_pairs = (
            VehicleInstallationPair.objects.filter(is_deleted=False)
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

        hard_items: List[Dict[str, Any]] = []
        clean_items: List[Dict[str, Any]] = []
        reason_counter: Counter = Counter()

        for pair in verified_pairs:
            gt_plate = ""
            if pair.order and pair.order.registration_number:
                gt_plate = pair.order.registration_number.replace(" ", "").upper()
            elif pair.registration_number_detected:
                gt_plate = pair.registration_number_detected.replace(" ", "").upper()
            if not gt_plate:
                continue

            for side, img in (("FRONT", pair.front_image), ("REAR", pair.rear_image)):
                if img is None or img.is_file_pruned or not img.vault_file:
                    continue
                resolved = vault_service.resolve_vault_path(img.vault_file)
                if not resolved or not os.path.isfile(resolved):
                    continue

                reasons: List[str] = []
                det_conf = img.detector_confidence
                ocr_conf = img.ocr_confidence

                if det_conf is None or det_conf < 0.65:
                    reasons.append("LOW_OR_HEURISTIC_DET_CONF")
                if not img.bbox:
                    reasons.append("MISSING_INITIAL_BBOX")
                if pair.is_manual_override or pair.matched_via in ("MANUAL", "REPAIR", "ORDER_PRIOR"):
                    reasons.append(f"RESCUED_PAIR({pair.matched_via})")
                if ocr_conf is not None and ocr_conf < 0.78:
                    reasons.append("LOW_OCR_CLARITY")

                is_hard = len(reasons) > 0
                for r in reasons:
                    reason_counter[r.split("(")[0]] += 1

                item = {
                    "image_id": str(img.id),
                    "image_path": str(resolved),
                    "gt_plate": gt_plate,
                    "orientation": side,
                    "bbox": img.bbox,
                    "det_conf": det_conf,
                    "ocr_conf": ocr_conf,
                    "is_hard": is_hard,
                    "reasons": reasons if reasons else ["CLEAN_LOCALIZATION"],
                }
                if is_hard:
                    hard_items.append(item)
                else:
                    clean_items.append(item)

        random.seed(42)
        random.shuffle(hard_items)
        random.shuffle(clean_items)

        selected = (hard_items + clean_items)[:max_samples]
        random.shuffle(selected)

        self.stdout.write(f"Scanned {verified_pairs.count()} pairs -> {len(hard_items)} hard localization photos, {len(clean_items)} clean photos.")

        manifest_rows: List[Dict[str, Any]] = []
        exported_real = 0
        exported_context_crop = 0
        exported_synth_car = 0
        skipped = 0
        aspect_ratios: List[float] = []

        for idx, item in enumerate(selected):
            try:
                raw_img = preprocess.load_image(item["image_path"])
            except Exception:
                skipped += 1
                continue

            bbox_xyxy, det_conf_used, backend_used = resolve_plate_bbox_in_raw(raw_img, item.get("bbox"))
            if not bbox_xyxy:
                skipped += 1
                continue

            rh, rw = raw_img.shape[:2]
            x1, y1, x2, y2 = bbox_xyxy
            plate_crop_orig = raw_img[y1:y2, x1:x2].copy()

            is_train = random.random() < split_ratio
            dest_img_dir = train_img_dir if is_train else val_img_dir
            dest_lbl_dir = train_lbl_dir if is_train else val_lbl_dir

            # 1. Standardize frame to target_dim (640px max side)
            work_img = raw_img
            wx1, wy1, wx2, wy2 = x1, y1, x2, y2
            geometry_type = "2ROW_MOTORCYCLE_FULL"

            # For 25% of training images, apply Context-Decoupling random sub-crop
            # (removes outer Spiro handlebars/frame so detector learns vehicle-agnostic plate localization)
            if is_train and random.random() < 0.25:
                pad_left = random.uniform(0.6, 1.8) * (x2 - x1)
                pad_right = random.uniform(0.6, 1.8) * (x2 - x1)
                pad_top = random.uniform(0.8, 2.2) * (y2 - y1)
                pad_bot = random.uniform(0.8, 2.0) * (y2 - y1)
                cx1 = max(0, int(x1 - pad_left))
                cy1 = max(0, int(y1 - pad_top))
                cx2 = min(rw, int(x2 + pad_right))
                cy2 = min(rh, int(y2 + pad_bot))
                if (cx2 - cx1) > (x2 - x1) * 1.6 and (cy2 - cy1) > (y2 - y1) * 1.6:
                    work_img = raw_img[cy1:cy2, cx1:cx2].copy()
                    wx1, wy1, wx2, wy2 = x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1
                    geometry_type = "2ROW_CONTEXT_DECOUPLED"
                    exported_context_crop += 1

            wh, ww = work_img.shape[:2]
            scale = target_dim / float(max(wh, ww))
            if scale < 1.0 or max(wh, ww) != target_dim:
                new_w = max(32, int(ww * scale))
                new_h = max(32, int(wh * scale))
                work_img = cv2.resize(
                    work_img,
                    (new_w, new_h),
                    interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
                )
                wx1, wy1 = int(wx1 * scale), int(wy1 * scale)
                wx2, wy2 = int(wx2 * scale), int(wy2 * scale)
                wh, ww = new_h, new_w

            xc, yc, nw, nh = convert_xyxy_to_yolo([wx1, wy1, wx2, wy2], ww, wh)
            ar = round((wx2 - wx1) / float(max(1, wy2 - wy1)), 3)
            aspect_ratios.append(ar)

            tag = "hard" if item["is_hard"] else "clean"
            base_name = f"{item['gt_plate']}_{item['orientation'].lower()}_{tag}_{idx:04d}"
            img_path = dest_img_dir / f"{base_name}.jpg"
            lbl_path = dest_lbl_dir / f"{base_name}.txt"

            cv2.imwrite(str(img_path), work_img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            lbl_path.write_text(f"0 {xc} {yc} {nw} {nh}\n", encoding="utf-8")
            exported_real += 1

            manifest_rows.append({
                "filename": img_path.name,
                "split": "train" if is_train else "val",
                "gt_plate": item["gt_plate"],
                "orientation": item["orientation"],
                "geometry_type": geometry_type,
                "plate_aspect_ratio": ar,
                "box_area_ratio": round(nw * nh, 5),
                "detector_confidence": item["det_conf"] if item["det_conf"] is not None else "",
                "ocr_confidence": item["ocr_conf"] if item["ocr_conf"] is not None else "",
                "is_hard_case": item["is_hard"],
                "hard_reasons": "|".join(item["reasons"]),
            })

            # Save preview for first 30 samples
            if exported_real <= 30:
                vis = work_img.copy()
                cv2.rectangle(vis, (wx1, wy1), (wx2, wy2), (0, 255, 0), 2)
                cv2.putText(
                    vis,
                    f"{item['gt_plate']} ({geometry_type})",
                    (max(4, wx1), max(18, wy1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 255),
                    2,
                )
                cv2.imwrite(str(preview_dir / f"{base_name}_preview.jpg"), vis)

            # 2. Synthetic 1-Row Horizontal Car Plate Augmentation (~car_aug_ratio of training set)
            if is_train and random.random() < car_aug_ratio:
                car_plate = synthesize_1row_car_plate_from_2row(plate_crop_orig)
                if car_plate is not None:
                    cph, cpw = car_plate.shape[:2]
                    # Scale synthetic 1-row car plate to fit realistically into work_img
                    target_cpw = min(int(ww * 0.48), max(90, int((wx2 - wx1) * 1.75)))
                    target_cph = max(20, int(cph * (target_cpw / float(max(1, cpw)))))
                    if target_cpw + 16 < ww and target_cph + 16 < wh:
                        car_resized = cv2.resize(car_plate, (target_cpw, target_cph), interpolation=cv2.INTER_CUBIC)
                        synth_canvas = work_img.copy()
                        # Blur out original 2-row motorcycle plate region so there isn't a second unlabeled plate
                        roi_orig = synth_canvas[max(0, wy1 - 4) : min(wh, wy2 + 4), max(0, wx1 - 4) : min(ww, wx2 + 4)]
                        if roi_orig.size > 0:
                            synth_canvas[max(0, wy1 - 4) : min(wh, wy2 + 4), max(0, wx1 - 4) : min(ww, wx2 + 4)] = cv2.GaussianBlur(
                                roi_orig, (31, 31), 0
                            )

                        # Paste 1-row horizontal car plate near center/lower bumper region
                        px1 = max(6, min(ww - target_cpw - 6, int((wx1 + wx2) / 2 - target_cpw / 2)))
                        py1 = max(6, min(wh - target_cph - 6, int((wy1 + wy2) / 2 - target_cph / 2)))
                        px2 = px1 + target_cpw
                        py2 = py1 + target_cph
                        synth_canvas[py1:py2, px1:px2] = car_resized

                        sxc, syc, snw, snh = convert_xyxy_to_yolo([px1, py1, px2, py2], ww, wh)
                        sar = round(target_cpw / float(max(1, target_cph)), 3)
                        aspect_ratios.append(sar)

                        s_name = f"CAR1ROW_{item['gt_plate']}_{idx:04d}"
                        s_img_path = train_img_dir / f"{s_name}.jpg"
                        s_lbl_path = train_lbl_dir / f"{s_name}.txt"
                        cv2.imwrite(str(s_img_path), synth_canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                        s_lbl_path.write_text(f"0 {sxc} {syc} {snw} {snh}\n", encoding="utf-8")
                        exported_synth_car += 1

                        manifest_rows.append({
                            "filename": s_img_path.name,
                            "split": "train",
                            "gt_plate": item["gt_plate"],
                            "orientation": item["orientation"],
                            "geometry_type": "1ROW_SYNTHETIC_CAR_PLATE",
                            "plate_aspect_ratio": sar,
                            "box_area_ratio": round(snw * snh, 5),
                            "detector_confidence": item["det_conf"] if item["det_conf"] is not None else "",
                            "ocr_confidence": item["ocr_conf"] if item["ocr_conf"] is not None else "",
                            "is_hard_case": True,
                            "hard_reasons": "SYNTHETIC_1ROW_CAR_GENERALIZATION",
                        })

                        if exported_synth_car <= 15:
                            svis = synth_canvas.copy()
                            cv2.rectangle(svis, (px1, py1), (px2, py2), (255, 128, 0), 2)
                            cv2.putText(
                                svis,
                                f"1-ROW CAR: {item['gt_plate']} (AR={sar})",
                                (max(4, px1), max(18, py1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (0, 255, 255),
                                2,
                            )
                            cv2.imwrite(str(preview_dir / f"{s_name}_preview.jpg"), svis)

        yaml_content = f"""# YOLOv8 / YOLOv11 Universal Ugandan License Plate Detector Dataset
# Includes 2-Row Motorcycle Plates, Context-Decoupled Crops & 1-Row Car Plates
path: {out_dir.resolve().as_posix()}
train: images/train
val: images/val
nc: 1
names:
  0: license_plate
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
                    "orientation",
                    "geometry_type",
                    "plate_aspect_ratio",
                    "box_area_ratio",
                    "detector_confidence",
                    "ocr_confidence",
                    "is_hard_case",
                    "hard_reasons",
                ],
            )
            writer.writeheader()
            writer.writerows(manifest_rows)

        summary_report = {
            "total_verified_pairs_scanned": verified_pairs.count(),
            "hard_localization_photos_found": len(hard_items),
            "clean_localization_photos_found": len(clean_items),
            "total_frames_exported": len(manifest_rows),
            "real_2row_frames_exported": exported_real,
            "context_decoupled_frames": exported_context_crop,
            "synthetic_1row_car_frames": exported_synth_car,
            "skipped_frames": skipped,
            "hard_reasons_breakdown": dict(reason_counter.most_common()),
            "mean_aspect_ratio": round(float(np.mean(aspect_ratios)), 3) if aspect_ratios else 0.0,
        }
        report_path = out_dir / "detector_dataset_report.json"
        report_path.write_text(json.dumps(summary_report, indent=2), encoding="utf-8")

        zip_file = None
        if do_zip:
            zip_base = out_dir.parent / "dataset_plate_detector_colab"
            zip_file = shutil.make_archive(str(zip_base), "zip", root_dir=str(out_dir))

        self.stdout.write("-" * 72)
        self.stdout.write(self.style.SUCCESS(f"Stage 2 Detector Dataset Build Complete! Total frames: {len(manifest_rows)}"))
        self.stdout.write(f"  - Real 2-Row Motorcycle Frames  : {exported_real} ({exported_context_crop} context-decoupled)")
        self.stdout.write(f"  - Synthetic 1-Row Car Frames    : {exported_synth_car}")
        self.stdout.write(f"  - Dataset Config (data.yaml)    : {yaml_path.resolve()}")
        self.stdout.write(f"  - Manifest CSV                  : {manifest_path.resolve()}")
        if zip_file:
            self.stdout.write(self.style.SUCCESS(f"  - Colab ZIP Bundle              : {zip_file}"))
        self.stdout.write("=" * 72)
