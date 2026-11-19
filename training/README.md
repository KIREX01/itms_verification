# ITMS Verification Copilot — Dedicated Model Training Hub (`training/`)

All dataset generation outputs, Google Colab notebooks, training scripts, baseline backups, and fine-tuned model metrics are consolidated inside this single `training/` folder so the root application workspace remains clean.

---

## 1. Directory Structure

```text
training/
├── README.md                                     <-- This guide & training roadmap
├── INSTRUCTIONS_FOR_COLAB_AGY.md                 <-- Runbook & prompt for Google Colab AGY sessions
├── train_plate_character_ocr.ipynb               <-- 18-cell Character OCR training & evaluation notebook
├── run_all_in_colab.py                           <-- One-command automated Colab runner script
├── scripts/
│   ├── auto_annotate_plates.py                   <-- Auto-annotation tool for full-image plate detection
│   └── train_plate_detector.py                   <-- YOLOv8/YOLOv11 plate detector fine-tuning script
├── checkpoints/
│   ├── baseline/
│   │   ├── plate_char_reader_baseline.pt         <-- Original Indonesian 36-class YOLO11 checkpoint
│   │   └── plate_char_reader_baseline.onnx       <-- Original Indonesian ONNX checkpoint
│   └── trained_models/
│       ├── plate_char_reader_uganda.pt           <-- Fine-tuned Ugandan 36-class Character OCR (.pt)
│       ├── plate_char_reader_uganda.onnx         <-- Fine-tuned Ugandan 36-class Character OCR (.onnx)
│       └── training_metrics_summary.json         <-- Verified before- vs. after-training benchmark metrics
└── datasets/                                     <-- (Git-ignored raw image crops & Colab ZIP bundles)
    ├── dataset_char_ocr/                         <-- 1,156-crop Ugandan 36-class character dataset
    └── dataset_char_ocr_colab.zip                <-- 24.36 MB Colab upload bundle
```

---

## 2. Stage 1 Completed: Ugandan 36-Class Plate Character OCR (`plate_char_reader`)

### Dataset Summary
* **Source**: Mined from **3,631 verified pairs** (179 local + 3,452 EC2 production pairs), isolating **4,922 hard OCR cases** and exporting **1,156 balanced plate crops** (8,917 character boxes across all 34 valid Ugandan alphanumeric symbols `0-9`, `A-H`, `J-N`, `P-Z`).
* **Generator Command**:
  ```bash
  python manage.py prepare_char_ocr_dataset --output-dir training/datasets/dataset_char_ocr --max-hard 500 --max-easy 300 --zip
  ```

### Verified Validation Metrics (`training/checkpoints/trained_models/training_metrics_summary.json`)

| Metric | Baseline (Pre-Trained Indonesian Weights) | Fine-Tuned (Ugandan Hard-Case Weights) | Improvement Delta |
| :--- | :---: | :---: | :---: |
| **`mAP@50`** | `0.3063` (30.63%) | **`0.7454` (74.54%)** | **`+0.4391` (+43.91%)** |
| **`mAP@50-95`** | `0.2327` (23.27%) | **`0.6211` (62.11%)** | **`+0.3884` (+38.84%)** |
| **`Precision`** | `0.4879` (48.79%) | **`0.7524` (75.24%)** | **`+0.2645` (+26.45%)** |
| **`Recall`** | `0.2915` (29.15%) | **`0.7241` (72.41%)** | **`+0.4326` (+43.26%)** |

### Active Production Weights
The fine-tuned weights are deployed to:
* `models/plate_char_reader.pt`
* `models/plate_char_reader.onnx`

---

## 3. Next Planned Training Stages

1. **Stage 2: Ugandan Plate Detector (`models/license-plate-finetune-v1n.pt` / `v1s.pt`)**
   * Uses `training/scripts/auto_annotate_plates.py` and `training/scripts/train_plate_detector.py` to fine-tune plate localization on Ugandan 2-line PSV white and PMO yellow motorcycle plates.
2. **Stage 3: Front vs. Rear Vehicle Orientation Classifier (`core/vision/orientation.py`)**
   * Uses the 100% human-verified `front_image` (`FRONT`) and `rear_image` (`REAR`) labels from `SUBMITTED` pairs to replace the HSV color heuristic with a lightweight neural orientation classifier.
