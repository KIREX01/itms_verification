#!/usr/bin/env python3
"""
One-Command End-to-End Runner for Google Colab & Colab AGY Agent.

Executes the entire Ugandan License Plate Character OCR (36-Class YOLO11)
training, baseline comparison, and PyTorch + ONNX production export pipeline
from start to finish in a single command:

    python run_all_in_colab.py --epochs 80 --batch 32

What this script does automatically:
  1. Verifies/installs required packages (ultralytics, onnx, onnxruntime, huggingface_hub).
  2. Extracts dataset_char_ocr_colab.zip if dataset_char_ocr/ is not yet extracted.
  3. Rewrites dataset_char_ocr/data.yaml with the exact absolute path of the current runtime.
  4. Displays the Hard-Case Mining & Character Confusion summary from hard_cases_report.json.
  5. Evaluates the baseline pre-trained model (plate_char_reader.pt) on the validation split.
  6. Fine-tunes the 36-class YOLO11 character detector with Ugandan plate augmentations
     (strictly fliplr=0.0, flipud=0.0 so characters are never mirrored).
  7. Evaluates the fine-tuned model and prints a Baseline vs. Fine-Tuned comparison table.
  8. Exports production weights to:
       - trained_models/plate_char_reader_uganda.pt
       - trained_models/plate_char_reader_uganda.onnx
       - trained_models/training_metrics_summary.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def ensure_dependencies() -> None:
    """Install ultralytics, onnx, onnxruntime if missing in the Colab runtime."""
    required = ["ultralytics", "onnx", "onnxruntime", "huggingface_hub"]
    missing = [pkg for pkg in required if importlib.util.find_spec(pkg) is None]
    if missing:
        print(f"[1/6] Installing missing packages: {missing}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
    else:
        print("[1/6] All required packages (ultralytics, onnx, onnxruntime) are already installed.")


def prepare_dataset_dir(bundle_root: Path) -> Path:
    """Ensure dataset_char_ocr/ is extracted and data.yaml has the current absolute path."""
    candidates_dir = [
        bundle_root / "datasets" / "dataset_char_ocr",
        bundle_root / "dataset_char_ocr",
    ]
    dataset_dir = next((d for d in candidates_dir if (d / "data.yaml").exists()), bundle_root / "datasets" / "dataset_char_ocr")

    candidates_zip = [
        bundle_root / "datasets" / "dataset_char_ocr_colab.zip",
        bundle_root / "dataset_char_ocr_colab.zip",
        bundle_root.parent / "dataset_char_ocr_colab.zip",
    ]
    zip_path = next((z for z in candidates_zip if z.exists()), bundle_root / "datasets" / "dataset_char_ocr_colab.zip")

    if not (dataset_dir / "data.yaml").exists():
        if zip_path.exists():
            print(f"[2/6] Extracting {zip_path.name} -> {dataset_dir}...")
            dataset_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dataset_dir)
        else:
            raise FileNotFoundError(
                f"Could not find dataset_char_ocr/ or dataset_char_ocr_colab.zip in {bundle_root}"
            )
    else:
        print(f"[2/6] Found existing extracted dataset at {dataset_dir.resolve()}")

    yaml_path = dataset_dir / "data.yaml"
    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    new_lines = [
        f"path: {dataset_dir.resolve().as_posix()}" if line.startswith("path:") else line
        for line in lines
    ]
    yaml_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"      Updated data.yaml path -> {dataset_dir.resolve().as_posix()}")

    report_path = dataset_dir / "hard_cases_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"      Total Plate Crops      : {report.get('total_plate_crops_exported')}")
        print(f"      Hard OCR Cases Mined   : {report.get('hard_cases_found')}")
        print(f"      Top Char Confusions    : {list(report.get('top_character_confusions', {}).items())[:8]}")

    return yaml_path


def resolve_base_weights(bundle_root: Path) -> Path:
    """Locate plate_char_reader.pt locally or download from Hugging Face."""
    candidates = [
        bundle_root / "plate_char_reader.pt",
        bundle_root / "models" / "plate_char_reader.pt",
        bundle_root.parent / "models" / "plate_char_reader.pt",
    ]
    for c in candidates:
        if c.is_file():
            print(f"[3/6] Using local base character checkpoint: {c.resolve()}")
            return c

    print("[3/6] Downloading base 36-class YOLO11 character checkpoint from Hugging Face...")
    from huggingface_hub import hf_hub_download

    dest = bundle_root / "plate_char_reader.pt"
    dl = hf_hub_download(
        repo_id="carlravel/indonesian-plate-recognizer-yolo11",
        filename="plate_recognizer_yolo11n.pt",
    )
    shutil.copy2(dl, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="One-command Colab runner for Ugandan Plate Character OCR.")
    parser.add_argument("--epochs", type=int, default=80, help="Training epochs (default: 80).")
    parser.add_argument("--batch", type=int, default=32, help="Batch size (default: 32 on GPU, 8 on CPU).")
    parser.add_argument("--imgsz", type=int, default=320, help="Crop image size (default: 320).")
    args = parser.parse_args()

    bundle_root = Path(__file__).resolve().parent
    os.chdir(bundle_root)

    print("=" * 72)
    print("ITMS UGANDA LICENSE PLATE CHARACTER OCR (36-CLASS) — COLAB RUNNER")
    print("=" * 72)

    ensure_dependencies()

    import torch
    from ultralytics import YOLO

    has_gpu = torch.cuda.is_available()
    device = 0 if has_gpu else "cpu"
    batch_size = args.batch if has_gpu else min(args.batch, 8)
    print(f"      Device: {torch.cuda.get_device_name(0) if has_gpu else 'CPU'} (batch={batch_size})")

    yaml_path = prepare_dataset_dir(bundle_root)
    base_weights = resolve_base_weights(bundle_root)

    # Step 4: Baseline validation
    print("\n[4/6] Evaluating Baseline Model on Ugandan Validation Split...")
    base_model = YOLO(str(base_weights))
    base_val = base_model.val(
        data=str(yaml_path.resolve()),
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        verbose=False,
    )
    base_metrics = {
        "mAP50": round(float(base_val.box.map50), 4),
        "mAP50_95": round(float(base_val.box.map), 4),
        "precision": round(float(base_val.box.mp), 4),
        "recall": round(float(base_val.box.mr), 4),
    }
    print(f"      Baseline Metrics: {base_metrics}")

    # Step 5: Fine-Tuning
    print(f"\n[5/6] Fine-Tuning 36-Class Character Detector for {args.epochs} epochs...")
    train_model = YOLO(str(base_weights))
    train_model.train(
        data=str(yaml_path.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        patience=20,
        cos_lr=True,
        weight_decay=0.0005,
        dropout=0.05,
        hsv_h=0.015,
        hsv_s=0.6,
        hsv_v=0.35,
        degrees=8.0,
        translate=0.08,
        scale=0.25,
        fliplr=0.0,   # Never flip characters horizontally
        flipud=0.0,   # Never flip characters vertically
        mosaic=0.4,
        project=str(bundle_root / "runs" / "char_ocr"),
        name="uganda_plate_char_v1",
        exist_ok=True,
        plots=True,
        save=True,
    )

    # Step 6: Evaluate Best Checkpoint & Export .pt + .onnx
    print("\n[6/6] Evaluating Fine-Tuned Model & Exporting Production Weights (.pt & .onnx)...")
    best_pt = bundle_root / "runs" / "char_ocr" / "uganda_plate_char_v1" / "weights" / "best.pt"
    if not best_pt.exists():
        raise FileNotFoundError(f"Expected trained checkpoint not found at {best_pt}")

    tuned_model = YOLO(str(best_pt))
    tuned_val = tuned_model.val(
        data=str(yaml_path.resolve()),
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        verbose=False,
    )
    tuned_metrics = {
        "mAP50": round(float(tuned_val.box.map50), 4),
        "mAP50_95": round(float(tuned_val.box.map), 4),
        "precision": round(float(tuned_val.box.mp), 4),
        "recall": round(float(tuned_val.box.mr), 4),
    }

    out_dir = bundle_root / "trained_models"
    out_dir.mkdir(parents=True, exist_ok=True)

    final_pt = out_dir / "plate_char_reader_uganda.pt"
    shutil.copy2(best_pt, final_pt)

    onnx_file = tuned_model.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True)
    final_onnx = out_dir / "plate_char_reader_uganda.onnx"
    if onnx_file and Path(onnx_file).exists():
        shutil.copy2(onnx_file, final_onnx)

    summary = {
        "epochs_configured": args.epochs,
        "imgsz": args.imgsz,
        "device": str(device),
        "baseline_metrics": base_metrics,
        "finetuned_metrics": tuned_metrics,
        "improvement_delta": {
            k: round(tuned_metrics[k] - base_metrics[k], 4) for k in base_metrics
        },
        "exported_pytorch_weights": str(final_pt.resolve()),
        "exported_onnx_weights": str(final_onnx.resolve()),
    }
    summary_file = out_dir / "training_metrics_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print("TRAINING & EXPORT COMPLETE!")
    print(f"  Baseline   -> mAP50: {base_metrics['mAP50']:.4f} | mAP50-95: {base_metrics['mAP50_95']:.4f} | P: {base_metrics['precision']:.4f} | R: {base_metrics['recall']:.4f}")
    print(f"  Fine-Tuned -> mAP50: {tuned_metrics['mAP50']:.4f} | mAP50-95: {tuned_metrics['mAP50_95']:.4f} | P: {tuned_metrics['precision']:.4f} | R: {tuned_metrics['recall']:.4f}")
    print(f"  Saved PyTorch Weights : {final_pt.resolve()}")
    print(f"  Saved ONNX Weights    : {final_onnx.resolve()}")
    print(f"  Saved Metrics JSON    : {summary_file.resolve()}")
    print("=" * 72)


if __name__ == "__main__":
    main()
