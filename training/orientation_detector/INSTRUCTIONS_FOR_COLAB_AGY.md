# Google Colab AGY Instructions — Stage 3: Front vs. Rear Orientation Classifier (`training/orientation_detector/`)

> **STRICT RUNBOOK FOR COLAB AGY AGENT**: This folder (`orientation_detector/`) is 100% self-contained for training the **Front vs. Rear Vehicle Orientation Classifier (`orientation_classifier_uganda.pt`)** used in `core/vision/orientation.py`. Follow every step below in exact order.

---

## 1. Folder Layout (`orientation_detector/`)

* `INSTRUCTIONS_FOR_COLAB_AGY.md` — This instruction file.
* `train_orientation_detector.ipynb` — 14-cell notebook with HSV heuristic baseline confusion matrix, hard-case previews, hue-decoupled training, and `.pt`/`.onnx` export.
* `run_orientation_in_colab.py` — One-command automated Python runner (`!python run_orientation_in_colab.py --epochs 50 --batch 32 --imgsz 224`).
* `dataset_orientation_colab.zip` — Compact zip bundle containing human-verified `train/FRONT`, `train/REAR`, `val/FRONT`, `val/REAR` images + hue-shifted color-decoupled augmentations.
* `dataset_orientation/` — Extracted dataset folder (`train/`, `val/`, `dataset_manifest.csv`, `orientation_dataset_report.json`, `orientation_previews/`).
* `trained_models/` — Output folder where trained `.pt`, `.onnx`, and `orientation_metrics_summary.json` are saved.

---

## 2. Copy-Paste Prompt for Your Colab AGY Session

Upload or open the **`training/orientation_detector/`** folder in Google Colab and paste this prompt to your Colab AGY agent:

```text
Read INSTRUCTIONS_FOR_COLAB_AGY.md inside this orientation_detector folder and follow every step strictly:
1. Confirm the working directory is inside `orientation_detector/` and check `torch.cuda.is_available()`.
2. Run `!python run_orientation_in_colab.py --epochs 50 --batch 32 --imgsz 224` (or execute all cells in `train_orientation_detector.ipynb`).
3. Keep hue randomization (`hsv_h=0.35`) and random erasing (`erasing=0.25`) active so the model learns vehicle geometry rather than body paint color.
4. Verify that `trained_models/orientation_classifier_uganda.pt`, `trained_models/orientation_classifier_uganda.onnx`, and `trained_models/orientation_metrics_summary.json` are created on disk.
5. Report the baseline OpenCV HSV heuristic accuracy vs. the fine-tuned neural Top-1 accuracy from `trained_models/orientation_metrics_summary.json`.
```

---

## 3. Mandatory Checklist for the Colab AGY Agent

1. **Working Directory**: Always `cd` into the `orientation_detector/` folder first so `./dataset_orientation_colab.zip` resolves in the current directory.
2. **Color Decoupling (`hsv_h=0.35`)**: Must keep `hsv_h=0.35` and `erasing=0.25` enabled so the classifier generalizes across Spiro, Bajaj, TVS, and Haojue bikes regardless of paint color.
3. **Verify Outputs Before Concluding**: Confirm that all 3 files exist in `trained_models/` before finishing:
   * `trained_models/orientation_classifier_uganda.pt`
   * `trained_models/orientation_classifier_uganda.onnx`
   * `trained_models/orientation_metrics_summary.json`
