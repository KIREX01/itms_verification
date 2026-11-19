# Google Colab AGY Instructions — Stage 1: Character OCR (`training/char_detector/`)

> **STRICT RUNBOOK FOR COLAB AGY AGENT**: This folder (`char_detector/`) is 100% self-contained for training the **36-Class Ugandan License Plate Character OCR Model (`plate_char_reader`)**. Follow every step below in exact order.

---

## 1. Folder Layout (`char_detector/`)

* `INSTRUCTIONS_FOR_COLAB_AGY.md` — This instruction file.
* `train_plate_character_ocr.ipynb` — 18-cell notebook with EDA charts, confusion analysis, baseline evaluation, and fine-tuning.
* `run_all_in_colab.py` — One-command automated Python runner (`!python run_all_in_colab.py --epochs 80 --batch 32`).
* `plate_char_reader.pt` — Base pre-trained 36-class (`0-9`, `A-Z`) YOLO11 character detector checkpoint.
* `dataset_char_ocr_colab.zip` — 24.36 MB zip bundle containing 1,156 labeled Ugandan plate crops across all 34 valid Ugandan symbols.
* `dataset_char_ocr/` — Extracted dataset folder (`data.yaml`, `images/train`, `images/val`, `labels/train`, `labels/val`).
* `trained_models/` — Output folder where trained `.pt`, `.onnx`, and `training_metrics_summary.json` are saved.

---

## 2. Copy-Paste Prompt for Your Colab AGY Session

```text
Read INSTRUCTIONS_FOR_COLAB_AGY.md inside this char_detector folder and follow every step strictly:
1. Check `torch.cuda.is_available()` to confirm GPU status.
2. Run `!python run_all_in_colab.py --epochs 80 --batch 32` (or execute all cells in `train_plate_character_ocr.ipynb`).
3. Verify that `trained_models/plate_char_reader_uganda.pt`, `trained_models/plate_char_reader_uganda.onnx`, and `trained_models/training_metrics_summary.json` are created.
4. Report the exact baseline vs. fine-tuned mAP50, mAP50-95, Precision, and Recall from `trained_models/training_metrics_summary.json`.
```

---

## 3. Mandatory Checklist for the Colab AGY Agent

1. **Working Directory**: Always `cd` into the `char_detector/` folder before running any command so `./plate_char_reader.pt` and `./dataset_char_ocr_colab.zip` resolve locally.
2. **Never Mirror Characters**: `fliplr=0.0` and `flipud=0.0` are mandatory.
3. **Preserve 36-Class Vocabulary**: Keep `nc: 36` (`0..9` = digits `'0'..'9'`, `10..35` = letters `'A'..'Z'`).
4. **Verify Outputs Before Concluding**: Do not report completion until `trained_models/plate_char_reader_uganda.pt`, `trained_models/plate_char_reader_uganda.onnx`, and `trained_models/training_metrics_summary.json` exist on disk.
