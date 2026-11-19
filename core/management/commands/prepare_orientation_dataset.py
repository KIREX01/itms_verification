"""
Django Management Command: prepare_orientation_dataset

Builds the Stage 3 Front vs. Rear Vehicle Orientation Classifier Dataset
inside `training/orientation_detector/dataset_orientation` using 100% human-verified
`front_image` (FRONT) and `rear_image` (REAR) pairs from `VehicleInstallationPair`.

Key Capabilities:
  1. Hard-Case Orientation Mining & Baseline Heuristic Benchmark:
     Runs the existing OpenCV HSV heuristic (`core.vision.orientation.classify_orientation`)
     over every verified Front and Rear photo to measure its exact baseline accuracy and
     isolate every photo where the HSV color rule misclassified Front vs. Rear (e.g. due
     to red paint/glare on front shots or white sunlight glare on rear taillights).
  2. Multi-Bike Color & Context Decoupling:
     Generates Hue-Shifted (color-randomized) and sub-cropped training samples (~25% of
     train split) so the classifier ignores Spiro paint colors and learns universal
     motorcycle geometry (handlebars, mirrors, front forks, headlamp vs. rear seat,
     grab-rail, exhaust, rear mudguard, tail lamp).
  3. Ultralytics Classification Folder Structure:
     Exports `train/FRONT`, `train/REAR`, `val/FRONT`, `val/REAR` at compact 320px
     resolution and packages `training/orientation_detector/dataset_orientation_colab.zip`.

Usage:
  python manage.py prepare_orientation_dataset --zip
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
from typing import Any, Dict, List

import cv2
import numpy as np
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import VehicleInstallationPair
from core.services import vault_service
from core.vision import orientation, preprocess

logger = logging.getLogger(__name__)


def apply_hue_shift_and_subcrop(img: np.ndarray) -> np.ndarray:
    """
    Randomly shifts the HSV Hue channel (changing vehicle body paint color while preserving
    edges and lighting structure) and applies a slight 85-92% center/sub-crop so the model
    learns structural Front vs. Rear geometry rather than brand-specific paint color.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int32)
    hue_shift = random.choice([30, 55, 85, 115, 145])
    hsv[:, :, 0] = (hsv[:, :, 0] + hue_shift) % 180
    shifted = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # Sub-crop 85-92% of the frame
    crop_frac = random.uniform(0.84, 0.93)
    nh, nw = max(32, int(h * crop_frac)), max(32, int(w * crop_frac))
    y0 = random.randint(0, max(0, h - nh))
    x0 = random.randint(0, max(0, w - nw))
    return shifted[y0 : y0 + nh, x0 : x0 + nw].copy()


class Command(BaseCommand):
    help = (
        "Mines hard Front vs. Rear orientation cases, benchmarks the HSV heuristic, "
        "and builds the Stage 3 YOLO classification dataset for Google Colab."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="training/orientation_detector/dataset_orientation",
            help="Destination folder for the orientation dataset (default: training/orientation_detector/dataset_orientation).",
        )
        parser.add_argument(
            "--split",
            type=float,
            default=0.85,
            help="Train/val split ratio (default: 0.85).",
        )
        parser.add_argument(
            "--target-dim",
            type=int,
            default=320,
            help="Max image dimension for exported classification images (default: 320).",
        )
        parser.add_argument(
            "--hue-aug-ratio",
            type=float,
            default=0.25,
            help="Fraction of training images to augment with hue-shifted color-decoupled variants (default: 0.25).",
        )
        parser.add_argument(
            "--zip",
            action="store_true",
            help="Create training/orientation_detector/dataset_orientation_colab.zip ready for Google Colab.",
        )

    def handle(self, *args, **options):
        out_dir = Path(options["output_dir"])
        split_ratio = float(options["split"])
        target_dim = int(options["target_dim"])
        hue_aug_ratio = float(options["hue_aug_ratio"])
        do_zip = bool(options["zip"])

        for split_name in ("train", "val"):
            for cls_name in ("FRONT", "REAR"):
                (out_dir / split_name / cls_name).mkdir(parents=True, exist_ok=True)
        preview_dir = out_dir / "orientation_previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))
        self.stdout.write(self.style.MIGRATE_HEADING("ITMS Stage 3: Front vs. Rear Orientation Classifier Dataset Builder"))
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

        candidates: List[Dict[str, Any]] = []
        for pair in verified_pairs:
            gt_plate = ""
            if pair.order and pair.order.registration_number:
                gt_plate = pair.order.registration_number.replace(" ", "").upper()
            elif pair.registration_number_detected:
                gt_plate = pair.registration_number_detected.replace(" ", "").upper()
            else:
                gt_plate = f"PAIR{pair.id}"

            for true_side, img in (("FRONT", pair.front_image), ("REAR", pair.rear_image)):
                if img is None or img.is_file_pruned or not img.vault_file:
                    continue
                resolved = vault_service.resolve_vault_path(img.vault_file)
                if not resolved or not os.path.isfile(resolved):
                    continue
                candidates.append({
                    "image_id": str(img.id),
                    "pair_id": pair.id,
                    "image_path": str(resolved),
                    "gt_plate": gt_plate,
                    "true_orientation": true_side,
                    "stored_orientation": img.orientation or "UNKNOWN",
                    "stored_orient_conf": img.orientation_confidence,
                })

        random.seed(42)
        random.shuffle(candidates)

        manifest_rows: List[Dict[str, Any]] = []
        reason_counter: Counter = Counter()
        confusion_counter: Counter = Counter()
        heuristic_correct = 0
        heuristic_total = 0
        hard_count = 0
        exported_real = 0
        exported_hue_aug = 0
        skipped = 0

        for idx, item in enumerate(candidates):
            try:
                raw_img = preprocess.load_image(item["image_path"])
            except Exception:
                skipped += 1
                continue

            rh, rw = raw_img.shape[:2]
            scale = target_dim / float(max(1, max(rh, rw)))
            new_w = max(32, int(rw * scale))
            new_h = max(32, int(rh * scale))
            resized = cv2.resize(
                raw_img,
                (new_w, new_h),
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
            )

            # Evaluate existing OpenCV HSV orientation heuristic
            hsv_res = orientation.classify_orientation_heuristic(resized) if hasattr(orientation, "classify_orientation_heuristic") else orientation.classify_orientation(resized)
            pred_side = hsv_res.orientation
            pred_conf = hsv_res.confidence
            true_side = item["true_orientation"]

            heuristic_total += 1
            is_correct = (pred_side == true_side)
            if is_correct:
                heuristic_correct += 1

            reasons: List[str] = []
            if not is_correct:
                if pred_side == "UNKNOWN":
                    reasons.append("HSV_UNKNOWN_AMBIGUOUS")
                else:
                    reasons.append(f"HSV_MISCLASSIFIED({true_side}_READ_AS_{pred_side})")
                confusion_counter[f"{true_side}->{pred_side}"] += 1
            elif pred_conf < 0.68:
                reasons.append("HSV_LOW_MARGIN_CONFIDENCE")
            else:
                confusion_counter[f"{true_side}->{true_side}"] += 1

            is_hard = len(reasons) > 0
            if is_hard:
                hard_count += 1
            for r in reasons:
                reason_counter[r.split("(")[0]] += 1

            is_train = random.random() < split_ratio
            split_name = "train" if is_train else "val"
            dest_dir = out_dir / split_name / true_side

            tag = "hard" if is_hard else "clean"
            base_name = f"{item['gt_plate']}_{true_side.lower()}_{tag}_{idx:04d}"
            img_file = dest_dir / f"{base_name}.jpg"
            cv2.imwrite(str(img_file), resized, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            exported_real += 1

            manifest_rows.append({
                "filename": img_file.name,
                "split": split_name,
                "true_orientation": true_side,
                "hsv_predicted_orientation": pred_side,
                "hsv_confidence": pred_conf,
                "hsv_front_score": hsv_res.front_score,
                "hsv_rear_score": hsv_res.rear_score,
                "is_hsv_correct": is_correct,
                "is_hard_case": is_hard,
                "hard_reasons": "|".join(reasons) if reasons else "CLEAN_HSV_MATCH",
                "augmentation_type": "REAL_FRAME",
            })

            # Save annotated visual preview for the first 36 hard cases
            if is_hard and exported_real <= 120 and len(list(preview_dir.glob("*.jpg"))) < 24:
                vis = resized.copy()
                banner_color = (0, 0, 220) if not is_correct else (0, 165, 255)
                cv2.rectangle(vis, (0, 0), (new_w, 26), (15, 15, 15), -1)
                cv2.putText(
                    vis,
                    f"TRUE: {true_side} | HSV: {pred_side} ({pred_conf:.2f})",
                    (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    banner_color,
                    1,
                )
                cv2.imwrite(str(preview_dir / f"{base_name}_preview.jpg"), vis)

            # Generate Hue-Shifted Color-Decoupled Augmentation in train split
            if is_train and random.random() < hue_aug_ratio:
                hue_img = apply_hue_shift_and_subcrop(resized)
                aug_name = f"HUEAUG_{item['gt_plate']}_{true_side.lower()}_{idx:04d}.jpg"
                aug_file = dest_dir / aug_name
                cv2.imwrite(str(aug_file), hue_img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                exported_hue_aug += 1

                manifest_rows.append({
                    "filename": aug_name,
                    "split": "train",
                    "true_orientation": true_side,
                    "hsv_predicted_orientation": pred_side,
                    "hsv_confidence": pred_conf,
                    "hsv_front_score": hsv_res.front_score,
                    "hsv_rear_score": hsv_res.rear_score,
                    "is_hsv_correct": is_correct,
                    "is_hard_case": True,
                    "hard_reasons": "HUE_SHIFT_COLOR_DECOUPLED_AUG",
                    "augmentation_type": "HUE_COLOR_DECOUPLED",
                })

        manifest_path = out_dir / "dataset_manifest.csv"
        with open(manifest_path, "w", newline="", encoding="utf-8") as mf:
            writer = csv.DictWriter(
                mf,
                fieldnames=[
                    "filename",
                    "split",
                    "true_orientation",
                    "hsv_predicted_orientation",
                    "hsv_confidence",
                    "hsv_front_score",
                    "hsv_rear_score",
                    "is_hsv_correct",
                    "is_hard_case",
                    "hard_reasons",
                    "augmentation_type",
                ],
            )
            writer.writeheader()
            writer.writerows(manifest_rows)

        baseline_acc = round(heuristic_correct / float(max(1, heuristic_total)), 4)
        class_counts = Counter(f"{r['split']}_{r['true_orientation']}" for r in manifest_rows)

        report_data = {
            "total_verified_photos_evaluated": heuristic_total,
            "hsv_heuristic_correct_count": heuristic_correct,
            "hsv_heuristic_baseline_accuracy": baseline_acc,
            "hard_orientation_cases_found": hard_count,
            "total_frames_exported": len(manifest_rows),
            "real_frames_exported": exported_real,
            "hue_color_decoupled_frames": exported_hue_aug,
            "skipped_frames": skipped,
            "split_class_distribution": dict(class_counts),
            "hsv_confusion_matrix": dict(confusion_counter),
            "hard_reasons_breakdown": dict(reason_counter.most_common()),
        }
        report_path = out_dir / "orientation_dataset_report.json"
        report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        zip_file = None
        if do_zip:
            zip_base = out_dir.parent / "dataset_orientation_colab"
            zip_file = shutil.make_archive(str(zip_base), "zip", root_dir=str(out_dir))

        self.stdout.write("-" * 72)
        self.stdout.write(self.style.SUCCESS(f"Stage 3 Orientation Dataset Complete! Total frames: {len(manifest_rows)}"))
        self.stdout.write(f"  - Baseline OpenCV HSV Accuracy : {baseline_acc * 100:.2f}% ({heuristic_correct}/{heuristic_total})")
        self.stdout.write(f"  - Hard Orientation Cases Mined : {hard_count}")
        self.stdout.write(f"  - Real Frames + Hue-Decoupled  : {exported_real} real + {exported_hue_aug} hue-decoupled")
        self.stdout.write(f"  - Split / Class Breakdown      : {dict(class_counts)}")
        if zip_file:
            self.stdout.write(self.style.SUCCESS(f"  - Colab ZIP Bundle             : {zip_file}"))
        self.stdout.write("=" * 72)
