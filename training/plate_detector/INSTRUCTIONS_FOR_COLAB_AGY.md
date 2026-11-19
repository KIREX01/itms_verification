# Google Colab AGY Instructions — Stage 2: Universal Plate Detector (`training/plate_detector/`)

> **STRICT RUNBOOK FOR COLAB AGY AGENT**: This folder (`plate_detector/`) is 100% self-contained for training the **Full-Image Universal Ugandan License Plate Detector (`license-plate-finetune-v1n.pt`)** with multi-bike (Spiro, Bajaj, TVS, Haojue) and car/truck protection (`freeze=10`). Follow every step below in exact order.

---

## 1. Folder Layout (`plate_detector/`)

* `INSTRUCTIONS_FOR_COLAB_AGY.md` — This instruction file.
* `train_plate_detector.ipynb` — 14-cell notebook with 2-row vs. 1-row aspect-ratio EDA, preview grid, baseline evaluation, `freeze=10` training, and export.
* `run_plate_detector_in_colab.py` — One-command automated Python runner (`!python run_plate_detector_in_colab.py --epochs 80 --batch 16 --freeze 10`).
* `license-plate-finetune-v1n.pt` — Base pre-trained YOLOv11n license plate detector checkpoint right in the root of this folder.
* `dataset_plate_detector_colab.zip` — 29.4 MB zip bundle containing 418 labeled 640p frames (`356` 2-row motorcycle frames + `62` synthesized 1-row horizontal car plate frames).
* `dataset_plate_detector/` — Extracted dataset folder (`data.yaml`, `images/train`, `images/val`, `labels/train`, `labels/val`).
* `trained_models/` — Output folder where trained `.pt`, `.onnx`, and `detector_metrics_summary.json` are saved.

---

## 2. Copy-Paste Prompt for Your Colab AGY Session

Upload or open the **`training/plate_detector/`** folder in Google Colab and paste this prompt to your Colab AGY agent:

```text
Read INSTRUCTIONS_FOR_COLAB_AGY.md inside this plate_detector folder and follow every step strictly:
1. Confirm the working directory is inside `plate_detector/` and check `torch.cuda.is_available()`.
2. Run `!python run_plate_detector_in_colab.py --epochs 80 --batch 16 --freeze 10` (or execute all cells in `train_plate_detector.ipynb`).
3. Ensure backbone freezing (`freeze=10`) and anti-mirroring (`fliplr=0.0`, `flipud=0.0`) remain active so the model preserves car and multi-bike detection.
4. Verify that `trained_models/license_plate_uganda_v1n.pt`, `trained_models/license_plate_uganda_v1n.onnx`, and `trained_models/detector_metrics_summary.json` are created on disk.
5. Report the exact baseline vs. fine-tuned mAP50, mAP50-95, Precision, and Recall from `trained_models/detector_metrics_summary.json`.
```

---

## 3. Mandatory Checklist for the Colab AGY Agent

1. **Working Directory**: Always `cd` into the `plate_detector/` folder first so `./license-plate-finetune-v1n.pt` and `./dataset_plate_detector_colab.zip` are found immediately in the current directory.
2. **Backbone Freezing (`--freeze 10`)**: MUST keep `freeze=10` enabled so the first 10 backbone layers of `license-plate-finetune-v1n.pt` retain their pre-trained features for cars, trucks, vans, and non-Spiro motorcycles.
3. **No Mirroring (`fliplr=0.0`, `flipud=0.0`)**: Never mirror license plates horizontally or vertically.
4. **Verify Outputs Before Concluding**: Confirm that all 3 files exist in `trained_models/` before finishing:
   * `trained_models/license_plate_uganda_v1n.pt`
   * `trained_models/license_plate_uganda_v1n.onnx`
   * `trained_models/detector_metrics_summary.json`
