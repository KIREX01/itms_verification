"""
Plate Dataset Auto-Annotation & Bootstrapping Tool.

Uses the pipeline's multi-strategy detector to automatically generate
initial YOLO-format annotations (.txt) from raw vehicle/motorcycle photos.
This allows you to bootstrap a 500+ image training dataset in seconds,
which you can then visually verify or fine-tune in CVAT / Roboflow / LabelImg.

Output format (YOLO normalized):
  <class_id> <x_center> <y_center> <width> <height>

Usage:
  python scripts/auto_annotate_plates.py --input-dir media/vault --output-dir dataset --split 0.8
"""
import argparse
import os
import shutil
from pathlib import Path
import random
import cv2

# Ensure Django settings can be imported if needed
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vision import detector, preprocess


def convert_bbox_to_yolo(bbox, img_width, img_height):
    """Convert [x1, y1, x2, y2] to normalized [x_center, y_center, width, height]."""
    x1, y1, x2, y2 = bbox
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    x_center = x1 + (box_w / 2.0)
    y_center = y1 + (box_h / 2.0)

    # Normalize to [0.0, 1.0]
    return (
        round(x_center / float(img_width), 6),
        round(y_center / float(img_height), 6),
        round(box_w / float(img_width), 6),
        round(box_h / float(img_height), 6),
    )


def auto_annotate(input_dir: str, output_dir: str, split_ratio: float = 0.8):
    input_path = Path(input_dir)
    out_path = Path(output_dir)

    # Create YOLO directory layout
    train_img_dir = out_path / "images" / "train"
    val_img_dir = out_path / "images" / "val"
    train_lbl_dir = out_path / "labels" / "train"
    val_lbl_dir = out_path / "labels" / "val"

    for d in (train_img_dir, val_img_dir, train_lbl_dir, val_lbl_dir):
        d.mkdir(parents=True, exist_ok=True)

    extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    image_files = [p for p in input_path.rglob("*") if p.suffix.lower() in extensions]

    print(f"Found {len(image_files)} images in {input_dir}...")
    if not image_files:
        print("No images found to annotate.")
        return

    annotated_count = 0
    skipped_count = 0

    # Shuffle for train/val split
    random.seed(42)
    random.shuffle(image_files)

    for img_path in image_files:
        try:
            raw = preprocess.load_image(str(img_path))
        except Exception:
            skipped_count += 1
            continue

        raw_h, raw_w = raw.shape[:2]
        pre = preprocess.preprocess_pipeline(raw)
        pre_h, pre_w = pre.shape[:2]

        det = detector._detect_with_heuristic(pre)
        if det is None:
            skipped_count += 1
            continue

        # Scale detection coordinates back from 720p preprocessed space to raw space
        scale_x = raw_w / float(pre_w)
        scale_y = raw_h / float(pre_h)
        x1, y1, x2, y2 = det.bbox
        rx1 = max(0, int(x1 * scale_x))
        ry1 = max(0, int(y1 * scale_y))
        rx2 = min(raw_w, int(x2 * scale_x))
        ry2 = min(raw_h, int(y2 * scale_y))

        yolo_box = convert_bbox_to_yolo([rx1, ry1, rx2, ry2], raw_w, raw_h)

        # Decide train vs val split
        is_train = random.random() < split_ratio
        dest_img_dir = train_img_dir if is_train else val_img_dir
        dest_lbl_dir = train_lbl_dir if is_train else val_lbl_dir

        base_name = f"{img_path.stem}_{annotated_count}"
        dest_img_file = dest_img_dir / f"{base_name}{img_path.suffix.lower()}"
        dest_lbl_file = dest_lbl_dir / f"{base_name}.txt"

        # Copy image and write label (class 0: license_plate)
        shutil.copy2(str(img_path), str(dest_img_file))
        with open(dest_lbl_file, "w", encoding="utf-8") as f:
            f.write(f"0 {yolo_box[0]} {yolo_box[1]} {yolo_box[2]} {yolo_box[3]}\n")

        annotated_count += 1

    # Generate data.yaml
    yaml_content = f"""# YOLOv8 Ugandan Plate Dataset Configuration
path: {out_path.resolve().as_posix()}
train: images/train
val: images/val

names:
  0: license_plate
"""
    yaml_file = out_path / "data.yaml"
    with open(yaml_file, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    print("-" * 50)
    print(f"Auto-annotation complete!")
    print(f"  Successfully annotated: {annotated_count} images")
    print(f"  Skipped (no plate box): {skipped_count} images")
    print(f"  Dataset saved to      : {out_path.resolve()}")
    print(f"  Config file           : {yaml_file.resolve()}")
    print(f"\nNext step: Run training:")
    print(f"  python scripts/train_plate_detector.py --data {yaml_file.as_posix()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-generate YOLO annotations using heuristic detector.")
    parser.add_argument("--input-dir", default="media/vault", help="Source folder containing vehicle photos.")
    parser.add_argument("--output-dir", default="dataset", help="Output directory for YOLO dataset.")
    parser.add_argument("--split", type=float, default=0.8, help="Train/val split ratio (default: 0.8).")
    args = parser.parse_args()
    auto_annotate(args.input_dir, args.output_dir, args.split)
