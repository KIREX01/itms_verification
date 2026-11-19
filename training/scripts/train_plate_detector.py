"""
YOLOv8 Plate Detector Training Script.

Fine-tunes a YOLOv8 nano model specifically for Ugandan vehicle and motorcycle
license plates, applying augmentations for varied lighting, angles, and distances.

Usage:
  python scripts/train_plate_detector.py --data dataset/data.yaml --epochs 100 --imgsz 640
"""
import argparse
import os
import shutil
from pathlib import Path

# Lazy import inside train_plate_detector so --help works even if ultralytics is not installed yet


def train_plate_detector(
    data_yaml: str,
    epochs: int = 100,
    imgsz: int = 640,
    batch: int = 16,
    base_model: str = "yolov8n.pt",
    device: str = "",
    output_weights: str = "models/plate_yolov8n.pt",
):
    yaml_path = Path(data_yaml)
    if not yaml_path.is_file():
        raise FileNotFoundError(
            f"Dataset configuration file not found at: {yaml_path.resolve()}\n"
            f"Run scripts/auto_annotate_plates.py first to generate the dataset."
        )

    print("=" * 60)
    print("Starting YOLOv8 Plate Detector Training")
    print(f"  Base checkpoint : {base_model}")
    print(f"  Dataset config  : {yaml_path.resolve()}")
    print(f"  Epochs          : {epochs}")
    print(f"  Image size      : {imgsz}")
    print(f"  Batch size      : {batch}")
    print(f"  Device          : {device or 'auto (GPU if available, else CPU)'}")
    print("=" * 60)

    try:
        from ultralytics import YOLO
    except ImportError:
        raise ImportError(
            "ultralytics is not installed. Please install it with: pip install ultralytics"
        )

    # Initialize model from base weights
    model = YOLO(base_model)

    # Train with plate-specific hyperparameters and augmentations
    results = model.train(
        data=str(yaml_path.resolve()),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device if device else None,
        # Augmentations suitable for natural vehicle & motorcycle photos:
        hsv_h=0.015,     # subtle color hue variation
        hsv_s=0.7,       # saturation changes (sunny vs overcast)
        hsv_v=0.4,       # brightness changes (direct sunlight vs shadow)
        degrees=10.0,    # tilt / roll angle variations
        translate=0.1,   # translation jitter
        scale=0.5,       # distance variations (near vs far shots)
        fliplr=0.0,      # DO NOT flip horizontally (text would be mirrored!)
        flipud=0.0,      # DO NOT flip vertically
        mosaic=1.0,      # mosaic augmentation for context learning
        plots=True,      # save training curves and validation batch images
        save=True,
    )

    # Locate the best trained weights
    runs_dir = Path("runs/detect")
    latest_train_dir = max(runs_dir.glob("train*"), key=os.path.getmtime, default=None)

    if latest_train_dir:
        best_pt = latest_train_dir / "weights" / "best.pt"
        if best_pt.is_file():
            out_path = Path(output_weights)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(best_pt), str(out_path))

            print("\n" + "=" * 60)
            print("TRAINING SUCCESSFUL!")
            print(f"  Best weights saved to: {out_path.resolve()}")
            print("\nTo activate your new model in the pipeline, update your .env file:")
            print(f"  PLATE_YOLO_WEIGHTS={out_path.as_posix()}")
            print(f"  PLATE_DETECTOR_CONF_THRESHOLD=0.45")
            print("=" * 60)
            return

    print("Training finished. Check runs/detect/ for output artifacts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train custom YOLOv8 plate detector.")
    parser.add_argument("--data", default="dataset/data.yaml", help="Path to data.yaml dataset config.")
    parser.add_argument("--epochs", type=int, default=100, help="Number of training epochs (default: 100).")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image dimension (default: 640).")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (default: 16, use 8 on CPU).")
    parser.add_argument("--base-model", default="yolov8n.pt", help="Pretrained base model (default: yolov8n.pt).")
    parser.add_argument("--device", default="", help="Device: '0', 'cpu', etc. Leave blank for auto.")
    parser.add_argument("--output", default="models/plate_yolov8n.pt", help="Where to copy the best weights.")
    args = parser.parse_args()

    train_plate_detector(
        data_yaml=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        base_model=args.base_model,
        device=args.device,
        output_weights=args.output,
    )
