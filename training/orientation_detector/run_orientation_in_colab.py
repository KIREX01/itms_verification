#!/usr/bin/env python3
"""
One-Command Stage 3 Runner for Google Colab & Colab AGY Agent:
Front vs. Rear Vehicle Orientation Classifier (YOLOv11n-cls / YOLOv8n-cls).

Executes the entire Front vs. Rear orientation classification training, baseline
OpenCV HSV heuristic comparison, and PyTorch + ONNX production export pipeline
in a single command:

    python run_orientation_in_colab.py --epochs 50 --batch 32 --imgsz 224

Key Generalization Protections:
  - Hue Randomization (hsv_h=0.35): Prevents memorizing Spiro paint colors so the
    classifier focuses on universal vehicle geometry (handlebars, mirrors, front forks,
    and headlamp vs. rear seat, grab-rail, exhaust, rear mudguard, and tail lamp).
  - Random Erasing (erasing=0.25): Forces reliance on holistic structural shape rather
    than any single brand sticker or logo.
  - Horizontal Symmetry (fliplr=0.5, flipud=0.0): A horizontally mirrored front of a
    vehicle is still a front view, doubling geometric training diversity.
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
    required = ["ultralytics", "onnx", "onnxruntime"]
    missing = [pkg for pkg in required if importlib.util.find_spec(pkg) is None]
    if missing:
        print(f"[1/5] Installing missing packages: {missing}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
    else:
        print("[1/5] All required packages (ultralytics, onnx, onnxruntime) are already installed.")


def prepare_dataset_dir(bundle_root: Path) -> Path:
    candidates_dir = [
        bundle_root / "dataset_orientation",
        bundle_root / "datasets" / "dataset_orientation",
    ]
    dataset_dir = next(
        (d for d in candidates_dir if (d / "train").exists() and (d / "val").exists()),
        bundle_root / "dataset_orientation",
    )

    candidates_zip = [
        bundle_root / "dataset_orientation_colab.zip",
        bundle_root / "datasets" / "dataset_orientation_colab.zip",
        bundle_root.parent / "dataset_orientation_colab.zip",
    ]
    zip_path = next(
        (z for z in candidates_zip if z.exists()),
        bundle_root / "dataset_orientation_colab.zip",
    )

    if not ((dataset_dir / "train").exists() and (dataset_dir / "val").exists()):
        if zip_path.exists():
            print(f"[2/5] Extracting {zip_path.name} -> {dataset_dir}...")
            dataset_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dataset_dir)
        else:
            raise FileNotFoundError(
                f"Could not find dataset_orientation/ or dataset_orientation_colab.zip in {bundle_root}"
            )
    else:
        print(f"[2/5] Found existing extracted dataset at {dataset_dir.resolve()}")

    report_path = dataset_dir / "orientation_dataset_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"      Total Exported Frames     : {report.get('total_frames_exported')}")
        print(f"      Baseline HSV Heuristic Acc: {report.get('hsv_heuristic_baseline_accuracy', 0.0) * 100:.2f}%")
        print(f"      Hard Orientation Cases    : {report.get('hard_orientation_cases_found')}")

    return dataset_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 Colab runner for Front vs. Rear Orientation Classifier.")
    parser.add_argument("--epochs", type=int, default=50, help="Training epochs (default: 50).")
    parser.add_argument("--batch", type=int, default=32, help="Batch size (default: 32 on GPU, 16 on CPU).")
    parser.add_argument("--imgsz", type=int, default=224, help="Input image dimension (default: 224).")
    parser.add_argument("--base-model", default="yolo11n-cls.pt", help="Pretrained YOLO classification checkpoint (default: yolo11n-cls.pt).")
    args = parser.parse_args()

    bundle_root = Path(__file__).resolve().parent
    os.chdir(bundle_root)

    print("=" * 74)
    print("ITMS STAGE 3: FRONT VS. REAR VEHICLE ORIENTATION CLASSIFIER — COLAB RUNNER")
    print("=" * 74)

    ensure_dependencies()

    import torch
    from ultralytics import YOLO

    has_gpu = torch.cuda.is_available()
    device = 0 if has_gpu else "cpu"
    batch_size = args.batch if has_gpu else min(args.batch, 16)
    epochs = args.epochs if has_gpu else min(args.epochs, 25)
    print(f"      Device: {torch.cuda.get_device_name(0) if has_gpu else 'CPU'} (batch={batch_size}, epochs={epochs})")

    dataset_dir = prepare_dataset_dir(bundle_root)
    report_path = dataset_dir / "orientation_dataset_report.json"
    hsv_baseline_acc = 0.0
    if report_path.exists():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        hsv_baseline_acc = float(rep.get("hsv_heuristic_baseline_accuracy", 0.0))

    # Step 3: Initialize classification model
    print(f"\n[3/5] Loading Pre-Trained Classification Checkpoint ({args.base_model})...")
    try:
        train_model = YOLO(args.base_model)
    except Exception:
        train_model = YOLO("yolov8n-cls.pt")

    # Step 4: Fine-Tune Front vs. Rear Classifier
    print(f"\n[4/5] Fine-Tuning Front vs. Rear Classifier for {epochs} epochs...")
    train_model.train(
        data=str(dataset_dir.resolve()),
        epochs=epochs,
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        lr0=0.001,
        cos_lr=True,
        patience=15,
        weight_decay=0.0005,
        dropout=0.15,
        hsv_h=0.35,   # Aggressive hue randomization so vehicle paint color is ignored
        hsv_s=0.60,
        hsv_v=0.40,
        degrees=10.0,
        translate=0.10,
        scale=0.25,
        fliplr=0.5,   # Left-right mirror of a Front view is still a Front view
        flipud=0.0,   # Never invert vehicles upside down
        erasing=0.25, # Random cutout to prevent memorizing brand logos
        project=str(bundle_root / "runs" / "orientation_cls"),
        name="uganda_orientation_v1",
        exist_ok=True,
        plots=True,
        save=True,
    )

    # Step 5: Evaluate & Export
    print("\n[5/5] Evaluating Fine-Tuned Classifier & Exporting Production Weights (.pt & .onnx)...")
    best_pt = bundle_root / "runs" / "orientation_cls" / "uganda_orientation_v1" / "weights" / "best.pt"
    if not best_pt.exists():
        raise FileNotFoundError(f"Expected trained checkpoint not found at {best_pt}")

    tuned_model = YOLO(str(best_pt))
    tuned_val = tuned_model.val(
        data=str(dataset_dir.resolve()),
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        verbose=False,
    )
    top1_acc = round(float(tuned_val.top1), 4)

    out_dir = bundle_root / "trained_models"
    out_dir.mkdir(parents=True, exist_ok=True)

    final_pt = out_dir / "orientation_classifier_uganda.pt"
    shutil.copy2(best_pt, final_pt)

    onnx_file = tuned_model.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True)
    final_onnx = out_dir / "orientation_classifier_uganda.onnx"
    if onnx_file and Path(onnx_file).exists():
        shutil.copy2(onnx_file, final_onnx)

    summary = {
        "stage": "Stage 3 - Front vs. Rear Vehicle Orientation Classifier",
        "epochs_configured": epochs,
        "imgsz": args.imgsz,
        "device": str(device),
        "baseline_hsv_heuristic_accuracy": hsv_baseline_acc,
        "finetuned_neural_top1_accuracy": top1_acc,
        "accuracy_improvement_delta": round(top1_acc - hsv_baseline_acc, 4),
        "exported_pytorch_weights": str(final_pt.resolve()),
        "exported_onnx_weights": str(final_onnx.resolve()),
    }
    summary_file = out_dir / "orientation_metrics_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print("STAGE 3 ORIENTATION CLASSIFIER TRAINING & EXPORT COMPLETE!")
    print(f"  Baseline OpenCV HSV Heuristic Accuracy : {hsv_baseline_acc * 100:.2f}%")
    print(f"  Fine-Tuned Neural Top-1 Accuracy       : {top1_acc * 100:.2f}% (Delta: +{(top1_acc - hsv_baseline_acc) * 100:.2f}%)")
    print(f"  Saved PyTorch Weights                  : {final_pt.resolve()}")
    print(f"  Saved ONNX Weights                     : {final_onnx.resolve()}")
    print(f"  Saved Metrics JSON                     : {summary_file.resolve()}")
    print("=" * 74)


if __name__ == "__main__":
    main()
