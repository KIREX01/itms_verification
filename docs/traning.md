# Custom YOLOv8 License Plate Detector Training Guide

See the full, detailed guide at [docs/training.md](file:///F:/website%20backup/projects/itms_verification/itms_verification/docs/training.md).

---

## Quick Reference

### 1. Auto-Annotate Photos (Bootstrapping)
```bash
python scripts/auto_annotate_plates.py --input-dir "media/vault" --output-dir "dataset" --split 0.8
```

### 2. Fine-tune YOLOv8
```bash
# GPU:
python scripts/train_plate_detector.py --data dataset/data.yaml --epochs 100 --imgsz 640 --batch 16 --device 0

# CPU:
python scripts/train_plate_detector.py --data dataset/data.yaml --epochs 100 --imgsz 640 --batch 8 --device cpu
```

### 3. Deploy in .env
```env
PLATE_YOLO_WEIGHTS=models/plate_yolov8n.pt
PLATE_DETECTOR_CONF_THRESHOLD=0.45
```

### 4. Verify Pipeline
```bash
python manage.py process_vision --reprocess-all
```

For complete instructions on dataset collection, Ugandan plate geometry, hyperparameters, evaluation metrics, and troubleshooting, refer to [docs/training.md](file:///F:/website%20backup/projects/itms_verification/itms_verification/docs/training.md).
