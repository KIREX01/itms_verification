# Custom YOLOv8 License Plate Detector Training Guide

This guide provides end-to-end instructions for collecting, auto-annotating, training, evaluating, and deploying a custom **YOLOv8** plate detection model fine-tuned specifically for Ugandan vehicle and motorcycle number plates.

---

## 1. Why Custom Training is Recommended

Out of the box, the system defaults to generic COCO weights (`PLATE_YOLO_WEIGHTS=yolov8n.pt`), which were trained to detect 80 general object classes (e.g., cars, dogs, chairs) but **not** number plates. The pipeline currently uses our multi-strategy OpenCV heuristic detector as a fallback.

While the heuristic detector is robust, training a dedicated YOLO model provides key production benefits:

| Feature | OpenCV Heuristic Fallback | Custom Fine-Tuned YOLOv8 |
| :--- | :--- | :--- |
| **Inference Speed** | 80–220 ms per photo | **15–35 ms per photo** |
| **Motorcycle Plates** | Struggles with nearby exhaust/brackets | **High precision localization** |
| **Harsh Lighting** | Sensitive to extreme glare and shadows | **Robust across lighting conditions** |
| **Tilted / Angled Shots**| Limited to $\pm 15^\circ$ tilt | **Detects up to $\pm 45^\circ$ angles** |
| **CPU / Edge Viability** | Moderate CPU load across filters | **Ultralytics ONNX / OpenVINO ready** |

---

## 2. Ugandan Plate Profiles

Your training dataset should reflect the two distinct plate geometries used in Uganda:

1. **Standard Vehicle Plates (Cars, Trucks, Buses)**:
   * **Geometry**: Long single-line rectangular plate (~520 mm $\times$ 110 mm).
   * **Aspect Ratio**: `~3.8:1` to `~5.2:1`.
   * **Layout**: Blue EAC emblem/Ugandan flag on the left, followed by 3 letters, 3 digits, and 1–2 letters (e.g. `UBB 456C`).

2. **Motorcycle Plates (Boda-Bodas, Bikes)**:
   * **Geometry**: Compact 2-line squarish plate (~200 mm $\times$ 160 mm).
   * **Aspect Ratio**: `~1.1:1` to `~1.5:1`.
   * **Layout**:
     * Line 1 (Top): Country flag/emblem on the left (~20% width), followed by registration series (`UMA`).
     * Line 2 (Bottom): 3 digits and 1–2 letters (`145PD`) centered across the plate width.

---

## 3. Dataset Collection Guidelines

To achieve $\ge 95\%$ detection recall in production, follow these collection targets:

* **Dataset Size**:
  * *Proof-of-Concept / Pilot*: 250–500 images.
  * *Production Target*: 1,000–2,500 images.
* **Balanced Distribution**:
  * 50% Vehicle front views, 50% Vehicle rear views.
  * 60% Cars / Commercial vehicles (rectangular plates).
  * 40% Motorcycles (squarish 2-line plates).
* **Environmental Variety**:
  * Direct equatorial sunlight (harsh glare on metal).
  * Shaded garage / installation bays.
  * Overcast / rainy daylight.
  * Varied camera distances (tight crops vs full vehicle shots).

---

## 4. Automated Dataset Bootstrapping (Auto-Annotation)

Instead of manually drawing bounding boxes on hundreds of photos from scratch, use the included bootstrapping script: [`scripts/auto_annotate_plates.py`](file:///F:/website%20backup/projects/itms_verification/itms_verification/scripts/auto_annotate_plates.py).

This tool runs our multi-strategy OpenCV detector over your raw photo collection, scales localized coordinates to the raw image resolution, formats them into standard YOLO `.txt` labels, and creates an 80/20 train/validation split.

### Running Auto-Annotation:

```bash
# Auto-annotate all images in media/vault or an external photos directory
python scripts/auto_annotate_plates.py --input-dir "media/vault" --output-dir "dataset" --split 0.8
```

### Options:
* `--input-dir`: Path to folder containing raw vehicle images (scanned recursively).
* `--output-dir`: Output destination for the formatted YOLO dataset (default: `dataset`).
* `--split`: Training vs validation split ratio (default: `0.8` = 80% train, 20% val).

### Generated Directory Layout:

```text
dataset/
├── data.yaml              <-- Ultralytics dataset configuration
├── images/
│   ├── train/             <-- 80% of photos for training
│   └── val/               <-- 20% of photos for validation
└── labels/
    ├── train/             <-- YOLO format bounding boxes (.txt)
    └── val/               <-- YOLO format bounding boxes (.txt)
```

Each label `.txt` file contains one line per plate in normalized YOLO format:
```text
<class_id> <x_center> <y_center> <width> <height>
```
*(Coordinates are normalized between 0.0 and 1.0).*

---

## 5. Visual Inspection & Manual Fine-Tuning

Before training, it is recommended to review the auto-generated annotations using any standard labeling tool:

* **[Roboflow](https://roboflow.com)**: Upload the `dataset` folder directly.
* **[CVAT](https://www.cvat.ai/)**: Open source, self-hostable or cloud-based.
* **[LabelImg](https://github.com/HumanSignal/labelImg)**: Lightweight local desktop tool:
  ```bash
  pip install labelImg
  labelImg dataset/images/train dataset/labels/train/classes.txt
  ```

Because 85–95% of plates are already accurately boxed by the auto-annotator, you only need to adjust edge cases (taking ~2–5 seconds per photo instead of drawing boxes from scratch).

---

## 6. Training the Model

Use the fine-tuning script: [`scripts/train_plate_detector.py`](file:///F:/website%20backup/projects/itms_verification/itms_verification/scripts/train_plate_detector.py).

### Prerequisites:

Ensure `ultralytics` is installed in your Python environment:
```bash
pip install ultralytics
```

### Running Training:

```bash
# On GPU (NVIDIA CUDA device 0) - Recommended:
python scripts/train_plate_detector.py --data dataset/data.yaml --epochs 100 --imgsz 640 --batch 16 --device 0

# On CPU (if no dedicated GPU is available):
python scripts/train_plate_detector.py --data dataset/data.yaml --epochs 100 --imgsz 640 --batch 8 --device cpu
```

### Command Arguments:
* `--data`: Path to `dataset/data.yaml`.
* `--epochs`: Number of training epochs (default: `100`).
* `--imgsz`: Input image resolution (default: `640`).
* `--batch`: Batch size (default: `16` for GPU, `8` for CPU).
* `--base-model`: Base pre-trained checkpoint (default: `yolov8n.pt`).
* `--output`: Output path for the best weights (default: `models/plate_yolov8n.pt`).

### Crucial Augmentation Rules Configured in the Script:
1. **Flipping Disabled (`fliplr=0.0`, `flipud=0.0`)**:
   Horizontal mirroring is explicitly turned **off**. Mirroring plates reverses alphanumeric characters and national emblems, degrading detection accuracy.
2. **Lighting Jitter (`hsv_v=0.4`, `hsv_s=0.7`)**:
   Aggressive value/saturation jitter teaches the model to locate plates in both blinding midday sun and deep shadow.
3. **Perspective & Tilt (`degrees=10.0`, `translate=0.1`, `scale=0.5`)**:
   Simulates variations in camera distance and handheld phone angles.

---

## 7. Model Evaluation & Benchmarks

During training, Ultralytics saves progress logs and evaluation graphs to `runs/detect/train/`.

### Key Metrics to Monitor:

* **`mAP50` (Mean Average Precision @ IoU 0.50)**: Should reach **$\ge 95\%$** by epoch 50–80.
* **`mAP50-95`**: Should exceed **$\ge 75\%$**.
* **Precision & Recall**: Both should stabilize above **$90\%$**.

### Visual Artifacts to Inspect:
* `runs/detect/train/results.png`: Training/validation loss and mAP curves.
* `runs/detect/train/val_batch0_pred.jpg`: Side-by-side ground truth vs model predictions on validation images.
* `runs/detect/train/confusion_matrix.png`: Confirms zero background false-positive confusion.

---

## 8. Deploying Your Model to the Pipeline

Once training completes, the best checkpoint is saved automatically to `models/plate_yolov8n.pt`.

### Step 1: Update `.env` Configuration
Open your project `.env` file and point to the trained model:

```env
# Point to your custom trained plate detector
PLATE_YOLO_WEIGHTS=models/plate_yolov8n.pt

# Set a confident threshold (0.40 - 0.50 is recommended for custom models)
PLATE_DETECTOR_CONF_THRESHOLD=0.45
```

### Step 2: Verify in the Pipeline
Run the vision pipeline over vaulted evidence:

```bash
python manage.py process_vision --reprocess-all
```

The system will automatically:
1. Load `models/plate_yolov8n.pt` into GPU/CPU memory once.
2. Use YOLO bounding boxes with top priority.
3. Fall back to OpenCV heuristics only if a photo has zero confident YOLO detections.

---

## 9. Troubleshooting & FAQ

#### Q: "CUDA out of memory" error during training
**Solution**: Reduce batch size in the training command:
```bash
python scripts/train_plate_detector.py --batch 8 --imgsz 640
```

#### Q: Model detects vehicle grilles or square bumper stickers
**Solution**: Add 50–100 negative background images (photos of cars/bikes with number plates masked out) to the training set with empty `.txt` label files. This teaches YOLO to suppress false alarms on grilles and stickers.

#### Q: Motorcycle plates are cropped too tightly (cutting off the outer digits)
**Solution**: In `core/vision/detector.py`, `crop_detection()` applies an automatic padding margin (`pad_pct=0.04`). You can increase this to `0.06` if your labels are drawn strictly on character borders.

#### Q: How can I train on a cloud VM or Google Colab?
**Solution**:
1. Run `python scripts/auto_annotate_plates.py` locally to create the `dataset/` folder.
2. Zip the dataset: `zip -r dataset.zip dataset/`.
3. Upload to Google Colab / AWS / RunPod and run:
   ```bash
   yolo detect train data=dataset/data.yaml model=yolov8n.pt epochs=100 imgsz=640 device=0
   ```
4. Download the resulting `best.pt` into your local `models/plate_yolov8n.pt`.
