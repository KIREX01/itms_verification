# Google Colab AGY — One-Folder Character OCR Training Guide

Everything needed to train, evaluate, and export the **Ugandan 36-Class License Plate Character OCR Model (`plate_char_reader`)** is packaged inside this single folder (`colab_char_ocr_bundle/`).

---

## 1. What Is Inside This Folder (`colab_char_ocr_bundle/`)

| File / Folder | Purpose |
| :--- | :--- |
| `INSTRUCTIONS_FOR_COLAB_AGY.md` | This step-by-step instruction & agent prompt file for your Google Colab AGY session. |
| `train_plate_character_ocr.ipynb` | The complete 18-cell Jupyter/Colab notebook (EDA charts, hard-case confusion analysis, baseline evaluation, 80-epoch fine-tuning, and `.pt` / `.onnx` export). |
| `run_all_in_colab.py` | One-command automated Python runner that executes the entire pipeline from start to finish in a single step (`!python run_all_in_colab.py`). |
| `plate_char_reader.pt` | Pre-trained 36-class (`0-9`, `A-Z`) YOLO11 character detector base checkpoint included locally (no external download required). |
| `dataset_char_ocr_colab.zip` | Zip bundle of all cropped Ugandan motorcycle/vehicle plates (`images/train`, `images/val`, `labels/train`, `labels/val`, `data.yaml`, `dataset_manifest.csv`, `hard_cases_report.json`, and `hard_crops_preview/`). |
| `dataset_char_ocr/` | Pre-extracted dataset directory ready for immediate use. |

---

## 2. Step-by-Step Guide for You (Setting Up in Google Colab)

### Option A: If Your Google Colab Is Connected to This Local Workspace Folder
1. In Google Colab (or VS Code / Antigravity connected to a Colab GPU runtime), open **`colab_char_ocr_bundle/train_plate_character_ocr.ipynb`**.
2. Make sure the runtime hardware accelerator is set to **T4 GPU** (*Runtime -> Change runtime type -> T4 GPU*).
3. Tell your **Colab AGY agent** the prompt in **Section 3** below (or click **Run All**).

### Option B: If Your Google Colab Is Running in the Cloud Browser (`colab.research.google.com`)
1. Upload `colab_char_ocr_bundle/train_plate_character_ocr.ipynb` to Google Colab.
2. In the Colab left sidebar (**Files** tab `/content`), upload:
   * `colab_char_ocr_bundle/dataset_char_ocr_colab.zip`
   * `colab_char_ocr_bundle/plate_char_reader.pt`
   * `colab_char_ocr_bundle/run_all_in_colab.py`
   * `colab_char_ocr_bundle/INSTRUCTIONS_FOR_COLAB_AGY.md`
3. Paste the prompt in **Section 3** below into your Colab AGY session.

---

## 3. Copy-Paste Prompt to Give Your Colab AGY Agent

Copy and paste the block below directly to the AGY agent inside your Google Colab session:

```text
Read INSTRUCTIONS_FOR_COLAB_AGY.md in the current folder.
Execute the entire Ugandan 36-class License Plate Character OCR training pipeline in train_plate_character_ocr.ipynb (or via run_all_in_colab.py), verify that data.yaml points to the active dataset_char_ocr path, run baseline evaluation vs. 80-epoch GPU fine-tuning with fliplr=0.0 and flipud=0.0, and export the final trained weights to trained_models/plate_char_reader_uganda.pt and trained_models/plate_char_reader_uganda.onnx. Report the before-and-after mAP50, mAP50-95, Precision, and Recall metrics when done.
```

---

## 4. Direct Execution Runbook for the Colab AGY Agent

> **Instructions for the Colab AGY Agent:** Follow these exact rules when executing this task in Google Colab:

1. **Verify GPU & Dependencies**:
   * Confirm `torch.cuda.is_available()` is `True` if a GPU runtime is attached.
   * Ensure `ultralytics`, `onnx`, and `onnxruntime` are installed (`%pip install -q ultralytics onnx onnxruntime`).
2. **Dataset Extraction & Path Resolution**:
   * If `dataset_char_ocr/data.yaml` does not exist yet, unzip `dataset_char_ocr_colab.zip` into `./dataset_char_ocr/`.
   * Update the `path:` line inside `dataset_char_ocr/data.yaml` to the current working directory's absolute path (e.g. `/content/dataset_char_ocr` or `/content/colab_char_ocr_bundle/dataset_char_ocr`).
3. **36-Class Specification (Strict Compatibility with `core/vision/character_recognizer.py`)**:
   * Do **NOT** change the class IDs or class ordering in `data.yaml`.
   * Classes `0..9` must remain digits `'0'..'9'` and classes `10..35` must remain letters `'A'..'Z'` (`CHARS_VOCAB = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"`).
4. **Execute Either All Notebook Cells or the One-Shot Script**:
   * **To run via Notebook**: Execute all cells in `train_plate_character_ocr.ipynb` sequentially from top to bottom so all 3 EDA visualizations, baseline validation, 80-epoch training, and the Baseline vs. Fine-Tuned comparison bar chart render inline.
   * **To run via One-Command Script**: Run:
     ```bash
     !python run_all_in_colab.py --epochs 80 --batch 32 --imgsz 320
     ```
5. **Mandatory Hyperparameter Guardrails**:
   * Always keep `fliplr=0.0` and `flipud=0.0` (mirroring license plate characters corrupts character geometry).
   * Start from `plate_char_reader.pt` (36-class pre-trained character detector).
   * Use `imgsz=320`, `cos_lr=True`, `patience=20`.
6. **Verify Output Artifacts**:
   * Confirm that both files are created and non-empty:
     * `trained_models/plate_char_reader_uganda.pt` (or `models/plate_char_reader_uganda.pt`)
     * `trained_models/plate_char_reader_uganda.onnx` (or `models/plate_char_reader_uganda.onnx`)
     * `trained_models/training_metrics_summary.json`

---

## 5. Returning Trained Weights to Production After Colab Finishes

Once Colab finishes training and generates `plate_char_reader_uganda.pt` and `plate_char_reader_uganda.onnx`:
1. Download both files from Colab into our project's `models/` folder:
   * `models/plate_char_reader.pt`
   * `models/plate_char_reader.onnx`
2. Tell me here in this workspace **"Colab training is done, deploy the new character OCR weights"** — and I will benchmark them locally, commit them, and deploy them to the EC2 production server (`16.170.39.187`).
