# ITMS Verification Copilot — Model Training Hub (`training/`)

Each model in the ITMS vision pipeline has its own **100% self-contained subfolder** inside `training/`. When working in Google Colab (or pointing your Colab AGY agent to Google Drive), you only need to open the specific model folder you are training—eliminating any mix-up between datasets, base weights, or instructions.

---

## 1. Modular Directory Layout

```text
training/
├── README.md                                        <-- Master overview of all 3 model folders
│
├── char_detector/                                   <-- [STAGE 1 - COMPLETED] 36-Class Plate Character OCR
│   ├── INSTRUCTIONS_FOR_COLAB_AGY.md                <-- Dedicated instructions & prompt for char_detector
│   ├── train_plate_character_ocr.ipynb              <-- 18-cell Character OCR training & evaluation notebook
│   ├── run_all_in_colab.py                          <-- One-command automated runner for char_detector
│   ├── plate_char_reader.pt                         <-- Base 36-class YOLO11 character checkpoint
│   ├── dataset_char_ocr_colab.zip                   <-- 24.36 MB Colab dataset zip (1,156 Ugandan plate crops)
│   ├── dataset_char_ocr/                            <-- Pre-extracted YOLO dataset
│   ├── baseline/                                    <-- Backup of original Indonesian checkpoints (.pt & .onnx)
│   └── trained_models/                              <-- Trained Ugandan weights & verified metrics summary
│       ├── plate_char_reader_uganda.pt
│       ├── plate_char_reader_uganda.onnx
│       └── training_metrics_summary.json
│
├── plate_detector/                                  <-- [STAGE 2 - READY FOR COLAB] Full-Image Plate Detector
│   ├── INSTRUCTIONS_FOR_COLAB_AGY.md                <-- Dedicated instructions & prompt for plate_detector
│   ├── train_plate_detector.ipynb                   <-- 14-cell Universal Plate Detector notebook
│   ├── run_plate_detector_in_colab.py               <-- One-command automated runner (with freeze=10)
│   ├── license-plate-finetune-v1n.pt                <-- Base YOLOv11n detector checkpoint
│   ├── dataset_plate_detector_colab.zip             <-- 29.4 MB Colab dataset zip (418 frames: 2-row bike + 1-row car)
│   ├── dataset_plate_detector/                      <-- Pre-extracted YOLO dataset
│   ├── scripts/                                     <-- Standalone CLI auto-annotation & training utilities
│   ├── baseline/                                    <-- Backup of original YOLOv11n detector checkpoint
│   └── trained_models/                              <-- Destination for trained detector weights & metrics
│
└── orientation_detector/                            <-- [STAGE 3 - READY FOR COLAB] Front vs. Rear Classifier
    ├── INSTRUCTIONS_FOR_COLAB_AGY.md                <-- Dedicated instructions & prompt for orientation_detector
    ├── train_orientation_detector.ipynb             <-- 14-cell Front vs. Rear Orientation Classifier notebook
    ├── run_orientation_in_colab.py                  <-- One-command automated runner (YOLOv11n-cls, hsv_h=0.35)
    ├── dataset_orientation_colab.zip                <-- Compact Colab dataset zip (438 frames: FRONT & REAR)
    ├── dataset_orientation/                         <-- Pre-extracted train/ and val/ classification dataset
    └── trained_models/                              <-- Destination for orientation_classifier_uganda.pt & .onnx
```

---

## 2. Dataset Regenerator Commands (Django Management Commands)

* **Stage 1 (`training/char_detector/`)**:
  ```bash
  python manage.py prepare_char_ocr_dataset --output-dir training/char_detector/dataset_char_ocr --zip
  ```
* **Stage 2 (`training/plate_detector/`)**:
  ```bash
  python manage.py prepare_plate_detector_dataset --output-dir training/plate_detector/dataset_plate_detector --zip
  ```
* **Stage 3 (`training/orientation_detector/`)**:
  ```bash
  python manage.py prepare_orientation_dataset --output-dir training/orientation_detector/dataset_orientation --zip
  ```
