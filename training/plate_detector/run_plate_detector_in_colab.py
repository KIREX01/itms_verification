#!/usr/bin/env python3
"""
One-Command Stage 2 Runner for Google Colab & Colab AGY Agent:
Universal Ugandan License Plate Detector (YOLOv11n / YOLOv8n) with Car & Multi-Bike Protection.

Executes the entire full-image license plate detector training, baseline comparison,
and PyTorch + ONNX production export pipeline in a single command:

    python run_plate_detector_in_colab.py --epochs 80 --batch 16 --freeze 10

Why `--freeze 10` is critical:
  Freezing the first 10 backbone layers of `license-plate-finetune-v1n.pt` preserves
  the base model's pre-trained visual features for cars, trucks, vans, and other
  motorcycle brands (Bajaj, TVS, Haojue) while adapting the detection head to
  Ugandan 2-row motorcycle plates, 1-row car plates, and equatorial glare.
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
    required = ["ultralytics", "onnx", "onnxruntime", "huggingface_hub"]
    missing = [pkg for pkg in required if importlib.util.find_spec(pkg) is None]
    if missing:
        print(f"[1/6] Installing missing packages: {missing}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
    else:
        print("[1/6] All required packages (ultralytics, onnx, onnxruntime) are already installed.")


def prepare_dataset_dir(bundle_root: Path) -> Path:
    candidates_dir = [
        bundle_root / "dataset_plate_detector",
        bundle_root / "datasets" / "dataset_plate_detector",
    ]
    dataset_dir = next(
        (d for d in candidates_dir if (d / "data.yaml").exists()),
        bundle_root / "dataset_plate_detector",
    )

    candidates_zip = [
        bundle_root / "dataset_plate_detector_colab.zip",
        bundle_root / "datasets" / "dataset_plate_detector_colab.zip",
        bundle_root.parent / "dataset_plate_detector_colab.zip",
    ]
    zip_path = next(
        (z for z in candidates_zip if z.exists()),
        bundle_root / "dataset_plate_detector_colab.zip",
    )

    if not (dataset_dir / "data.yaml").exists():
        if zip_path.exists():
            print(f"[2/6] Extracting {zip_path.name} -> {dataset_dir}...")
            dataset_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dataset_dir)
        else:
            raise FileNotFoundError(
                f"Could not find dataset_plate_detector/ or dataset_plate_detector_colab.zip in {bundle_root}"
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

    report_path = dataset_dir / "detector_dataset_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"      Total Training/Val Frames : {report.get('total_frames_exported')}")
        print(f"      2-Row Motorcycle Frames   : {report.get('real_2row_frames_exported')} ({report.get('context_decoupled_frames')} context-decoupled)")
        print(f"      1-Row Synthetic Car Frames: {report.get('synthetic_1row_car_frames')}")

    return yaml_path


def resolve_base_weights(bundle_root: Path) -> Path:
    candidates = [
        bundle_root / "checkpoints" / "baseline" / "license-plate-finetune-v1n.pt",
        bundle_root / "license-plate-finetune-v1n.pt",
        bundle_root.parent / "models" / "license-plate-finetune-v1n.pt",
    ]
    for c in candidates:
        if c.is_file():
            print(f"[3/6] Using local base plate detector checkpoint: {c.resolve()}")
            return c

    print("[3/6] Downloading base YOLOv11 license plate detector from Hugging Face...")
    from huggingface_hub import hf_hub_download

    dest = bundle_root / "license-plate-finetune-v1n.pt"
    dl = hf_hub_download(
        repo_id="morsetechlab/yolov11-license-plate-detection",
        filename="license-plate-finetune-v1n.pt",
    )
    shutil.copy2(dl, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 Colab runner for Universal Ugandan Plate Detector.")
    parser.add_argument("--epochs", type=int, default=80, help="Training epochs (default: 80).")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (default: 16 on GPU, 8 on CPU).")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image dimension (default: 640).")
    parser.add_argument("--freeze", type=int, default=10, help="Backbone layers to freeze to preserve Car/Multi-Bike features (default: 10).")
    args = parser.parse_args()

    bundle_root = Path(__file__).resolve().parent
    os.chdir(bundle_root)

    print("=" * 74)
    print("ITMS STAGE 2: UNIVERSAL UGANDAN PLATE DETECTOR (CAR + MULTI-BIKE READY)")
    print("=" * 74)

    ensure_dependencies()

    import torch
    from ultralytics import YOLO

    has_gpu = torch.cuda.is_available()
    device = 0 if has_gpu else "cpu"
    batch_size = args.batch if has_gpu else min(args.batch, 8)
    epochs = args.epochs if has_gpu else min(args.epochs, 30)
    print(f"      Device: {torch.cuda.get_device_name(0) if has_gpu else 'CPU'} (batch={batch_size}, epochs={epochs}, freeze={args.freeze})")

    yaml_path = prepare_dataset_dir(bundle_root)
    base_weights = resolve_base_weights(bundle_root)

    # Step 4: Baseline evaluation
    print("\n[4/6] Evaluating Baseline Detector on Ugandan Validation Split...")
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

    # Step 5: Fine-Tuning with Backbone Freeze (freeze=10)
    print(f"\n[5/6] Fine-Tuning Universal Plate Detector (freeze={args.freeze} backbone layers)...")
    train_model = YOLO(str(base_weights))
    train_model.train(
        data=str(yaml_path.resolve()),
        epochs=epochs,
        imgsz=args.imgsz,
        batch=batch_size,
        device=device,
        freeze=args.freeze,
        lr0=0.001,
        cos_lr=True,
        patience=20,
        weight_decay=0.0005,
        hsv_h=0.015,
        hsv_s=0.65,
        hsv_v=0.40,
        degrees=12.0,
        translate=0.10,
        scale=0.60,
        fliplr=0.0,   # Never mirror license plates
        flipud=0.0,
        mosaic=1.0,
        project=str(bundle_root / "runs" / "plate_detector"),
        name="uganda_plate_detector_v1",
        exist_ok=True,
        plots=True,
        save=True,
    )

    # Step 6: Evaluate & Export
    print("\n[6/6] Evaluating Fine-Tuned Detector & Exporting Production Weights (.pt & .onnx)...")
    best_pt = bundle_root / "runs" / "plate_detector" / "uganda_plate_detector_v1" / "weights" / "best.pt"
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

    out_dir = bundle_root / "checkpoints" / "trained_models"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Also write to trained_models/ if Colab mounts the folder directly
    alt_out_dir = bundle_root / "trained_models"
    alt_out_dir.mkdir(parents=True, exist_ok=True)

    final_pt = out_dir / "license_plate_uganda_v1n.pt"
    shutil.copy2(best_pt, final_pt)
    shutil.copy2(best_pt, alt_out_dir / "license_plate_uganda_v1n.pt")

    onnx_file = tuned_model.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True)
    final_onnx = out_dir / "license_plate_uganda_v1n.onnx"
    if onnx_file and Path(onnx_file).exists():
        shutil.copy2(onnx_file, final_onnx)
        shutil.copy2(onnx_file, alt_out_dir / "license_plate_uganda_v1n.onnx")

    summary = {
        "stage": "Stage 2 - Universal Ugandan Plate Detector",
        "epochs_configured": epochs,
        "imgsz": args.imgsz,
        "frozen_backbone_layers": args.freeze,
        "device": str(device),
        "baseline_metrics": base_metrics,
        "finetuned_metrics": tuned_metrics,
        "improvement_delta": {
            k: round(tuned_metrics[k] - base_metrics[k], 4) for k in base_metrics
        },
        "exported_pytorch_weights": str(final_pt.resolve()),
        "exported_onnx_weights": str(final_onnx.resolve()),
    }
    summary_file = out_dir / "detector_metrics_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (alt_out_dir / "detector_metrics_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print("STAGE 2 DETECTOR TRAINING & EXPORT COMPLETE!")
    print(f"  Baseline   -> mAP50: {base_metrics['mAP50']:.4f} | mAP50-95: {base_metrics['mAP50_95']:.4f} | P: {base_metrics['precision']:.4f} | R: {base_metrics['recall']:.4f}")
    print(f"  Fine-Tuned -> mAP50: {tuned_metrics['mAP50']:.4f} | mAP50-95: {tuned_metrics['mAP50_95']:.4f} | P: {tuned_metrics['precision']:.4f} | R: {tuned_metrics['recall']:.4f}")
    print(f"  Saved PyTorch Weights : {final_pt.resolve()}")
    print(f"  Saved ONNX Weights    : {final_onnx.resolve()}")
    print(f"  Saved Metrics JSON    : {summary_file.resolve()}")
    print("=" * 74)


if __name__ == "__main__":
    main()
