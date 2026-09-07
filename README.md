# Intelligent Vehicle Installation-Photo Recognition & Verification System

Django/PostgreSQL-backed pipeline that ingests unsorted vehicle installation photos,
localizes and reads number plates, classifies front/rear orientation, matches evidence
against an installation-order registry, and drives a simulated ITMS submission workflow --
all reviewed through a keyboard-driven Textual TUI. See `docs/propsal.md` and
`docs/ROADMAP.md` for the full design rationale.

## 1. Prerequisites

* Python 3.11 recommended for PaddleOCR; Python 3.12+ uses the Tesseract fallback
* PostgreSQL 14+ running locally (or reachable over network)
* ~4 GB free disk for model weights (ultralytics + paddleocr download models on first use)

## 2. Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# edit .env: set POSTGRES_* to your local Postgres, DJANGO_SECRET_KEY, superuser creds

createdb itms                      # or: psql -c "CREATE DATABASE itms;"

python manage.py migrate
python manage.py create_superuser_if_none
```

> **OCR note**: PaddlePaddle currently provides Windows wheels only through Python 3.11.
> Python 3.12+ skips the PaddleOCR packages and uses the `pytesseract` fallback instead.
> Install Tesseract separately (`brew install tesseract`, `apt install tesseract-ocr`, or
> the Windows installer), then set `USE_PADDLEOCR=False` in `.env` to skip trying PaddleOCR.

## 3. Running the full pipeline, end to end

```bash
# 1. Load the expected installation orders (bounded search space)
python manage.py seed_orders --csv sample_data/orders.csv
# Options:
#   --clear                Wipe existing orders before seeding

# 2. Ingest raw photos from a folder (or upload via browser at http://localhost:8000/upload/)
python manage.py ingest_photos "C:\Users\KIREX\Pictures\image" --recursive --batch-label "Shift 1 Entebbe"
# Options:
#   --recursive            Traverse subdirectories
#   --batch-label "name"   Human-readable label for this ingestion batch

# Web Upload UI & REST API:
#   - Browser UI:   http://localhost:8000/upload/   (drag-and-drop, multiple files, mobile camera)
#   - REST API:     POST http://localhost:8000/api/upload/ (multipart form with 'photos' field)

# 3. Run detection + OCR + normalization + orientation over evidence
python manage.py process_vision
# Options:
#   --batch <batch_id>     Process only images belonging to a specific IngestionBatch
#   --reprocess-failed     Retry images that previously encountered errors
#   --include-needs-review Reprocess NEEDS_REVIEW images that lack a detected plate
#   --reprocess-all        Reset retry budgets and re-run all unsubmitted images
#   --save-crops           Save localized plate crops to media/crops/ for inspection
#   --cleanup-crops        Clean up intermediate crops in media/crops/ after run
#   --max-retries N        Maximum retry attempts before permanently failing (default: 3)
#   --limit N              Process at most N images in this run

# 4. Group images into front/rear pairs and fuzzy-match against orders
python manage.py associate_pairs
# Options:
#   --skip-matching        Group into pairs only; skip fuzzy matching against orders

# 5. Interactive Operator Dashboard (TUI): Review queue, history, and live console
python manage.py run_tui
# TUI Navigation & Hotkeys:
#   1 / 2 / 3              Switch Tabs: [1] Review Queue, [2] History & Audit, [3] Batches
#   I                      Add Photos via Native Desktop Dialog (select files or entire folder/SD card)
#   W                      Launch Web Upload Interface in Browser (auto-falls back to native dialog if server offline)
#   F                      Cycle History Label Filter (ALL -> SUBMITTED -> FAILED -> APPROVED -> AUDIT)
#   P                      Run Vision Pipeline (background thread, streams to bottom console)
#   M                      Run Pair Matcher (background thread, streams to bottom console)
#   U                      Submit Selected Pair to ITMS (live step-by-step progress logging)
#   A                      Approve selected pair for submission
#   S                      Swap front and rear image assignments
#   V                      View evidence side-by-side in standalone Python window (fast, 50Hz, no Photo Viewer)
#   C                      Clean temporary crops & enforce vault retention policy
#   R                      Refresh data tables and metrics from database
#   Q                      Quit the TUI

# 6. Submit approved pairs to ITMS (simulated sandbox or live server)
python manage.py submit_itms --auto-approve
# Options:
#   --auto-approve         Promote complete high-confidence pairs to APPROVED first
#   --backend mock         Submit to simulated sandbox (default)
#   --backend live         Submit to live ITMS web app (https://stock.itms.ug)
```

### 3.1 ITMS Web App Live Connection & Token Management

The system connects to the ITMS web application (`https://stock.itms.ug`) using dual-token JWT authentication (access token + refresh token) with automatic token refresh on 401:

```bash
# Ping the ITMS server and verify SSL/latency
python manage.py itms_auth --ping

# Inspect current stored token status and remaining TTL
python manage.py itms_auth --status

# Authenticate against ITMS and securely cache access + refresh tokens
python manage.py itms_auth --login --username "operator@itms.ug" --password "secret"

# Test refreshing the access token via the refresh token
python manage.py itms_auth --refresh

# Clear cached tokens and logout
python manage.py itms_auth --logout
```

### 3.2 Plate Crop Management & Storage Cleanup

When running vision processing or troubleshooting detection accuracy, intermediate plate crops can be saved to a organized date-partitioned directory (`media/crops/YYYY-MM-DD/{image_id}_plate.jpg`) instead of polluting the project root. Immutable vault originals (`media/vault/`) are protected and never touched.

```bash
# Clean up temporary crops and remove any stray root debug images
python manage.py clean_crops

# Preview files that would be deleted without removing them
python manage.py clean_crops --dry-run

# Only delete temporary crops older than 7 days
python manage.py clean_crops --older-than-days 7
```

### 3.3 Evidence Vault 1-Week Lifecycle Retention (`prune_vault`)

To prevent storage exhaustion when operating at high volume, the system implements an automated retention lifecycle:
* **Successfully Submitted Evidence (`SUBMITTED`)**: Photos that have already been validated, verified, and submitted to ITMS have a **1-week (7-day)** retention window. After 7 days, heavy JPG files on disk are safely pruned to reclaim storage, while all database records (plate numbers, orientation, confidence, timestamps, and audit trail) are permanently preserved with status `PRUNED`.
* **Evidence with Issues (`FAILED`, `NEEDS_REVIEW`, `INCOMPLETE`, etc.)**: Photos requiring human intervention, unresolved pairs, or pending uploads are **STRICTLY PROTECTED** and retained indefinitely until explicitly resolved by an operator.

```bash
# Preview files that would be pruned under the 7-day retention policy
python manage.py prune_vault --dry-run

# Execute lifecycle pruning (defaults to 7 days)
python manage.py prune_vault

# Enforce custom retention window (e.g. 14 days)
python manage.py prune_vault --days 14

# Prune expired files for a specific ingestion batch only
python manage.py prune_vault --batch BATCH-20260907-142030-ab12
```

## 4. Running the test suite

Tests use SQLite in-memory (no Postgres required) via `itms_project/settings_test.py`:

```bash
pytest
# or
DJANGO_SETTINGS_MODULE=itms_project.settings_test python manage.py test core
```

## 5. Benchmarking

```bash
python manage.py benchmark_pipeline --iterations 200
```

Prints per-stage latency and throughput for the normalizer, preprocessing, and fuzzy
matcher. (Detector/OCR latency depends heavily on which backend loads -- YOLO+PaddleOCR
on CPU vs. GPU vs. the heuristic/tesseract fallback -- so it isn't included in the
synthetic benchmark; time it directly against your own photo set if you need real numbers.)

## 6. Training your own plate detector (recommended before production use)

> **Full Documentation**: See [docs/training.md](file:///F:/website%20backup/projects/itms_verification/itms_verification/docs/training.md) for the complete guide on dataset collection, auto-annotation bootstrapping, hyperparameters, and evaluation benchmarks.

Out of the box, `PLATE_YOLO_WEIGHTS=yolov8n.pt` points at a generic COCO-pretrained
checkpoint, which was **not** trained to find license plates specifically -- it exists so
the pipeline is runnable immediately, and the OpenCV heuristic fallback in
`core/vision/detector.py` picks up the slack when YOLO doesn't find a confident box.

For maximum accuracy on Ugandan vehicle and motorcycle plates:

### Step 1: Collect & Label Dataset
1. Collect 300–1,000 photos of Ugandan vehicles and motorcycles from typical installation angles (front and rear).
2. Annotate bounding boxes using **CVAT**, **Roboflow**, or **LabelImg**.
   * Label class: `0: license_plate` (or separate classes `0: car_plate`, `1: motorcycle_plate`).
   * Export format: **YOLOv8 PyTorch TXT** format:
     ```text
     <class-id> <center-x> <center-y> <width> <height>
     ```
     (All coordinates normalized between 0.0 and 1.0).

### Step 2: Dataset Directory Structure
Organize your dataset into train and validation splits:

```text
dataset/
├── data.yaml
├── images/
│   ├── train/
│   └── val/
└── labels/
    ├── train/
    └── val/
```

Example `data.yaml`:
```yaml
path: ./dataset
train: images/train
val: images/val

names:
  0: license_plate
```

### Step 3: Train YOLOv8 / YOLOv11
Run fine-tuning using the Ultralytics CLI or Python API:

```bash
# Using CLI:
yolo detect train data=dataset/data.yaml model=yolov8n.pt epochs=100 imgsz=640 batch=16 plots=True device=0

# Or on CPU:
yolo detect train data=dataset/data.yaml model=yolov8n.pt epochs=100 imgsz=640 batch=8 device=cpu
```

### Step 4: Deploy Model Weights
1. When training finishes, the best weights are saved at:
   `runs/detect/train/weights/best.pt`
2. Copy `best.pt` into the project (e.g. `models/plate_yolov8n.pt`).
3. Point `.env` to the trained weights:
   ```env
   PLATE_YOLO_WEIGHTS=models/plate_yolov8n.pt
   PLATE_DETECTOR_CONF_THRESHOLD=0.45
   ```
4. Restart or re-run `process_vision`. The pipeline will automatically load your custom weights and achieve instant, high-confidence detections before falling back to heuristics.

## 7. Project layout

```
itms_project/          Django settings/urls/wsgi
core/
  models.py             InstallationOrder, EvidenceImage, VehicleInstallationPair, SubmissionAuditLog
  admin.py               Django admin registration
  vision/                preprocess.py, detector.py, ocr_engine.py, normalizer.py, orientation.py
  matcher/               association.py (pairwise linking), order_matcher.py (fuzzy matching)
  services/              itms_mock.py, itms_client.py, submission_worker.py, viewer.py
  tui/                   app.py (Textual dashboard)
  management/commands/   seed_orders, ingest_photos, process_vision, associate_pairs,
                          submit_itms, itms_auth, run_tui, benchmark_pipeline, create_superuser_if_none
  tests*.py              pytest/Django test suite
sample_data/orders.csv  Demo installation-order registry
docs/                   propsal.md, ROADMAP.md
```

## 8. Security note

`.env` is git-ignored and must never be committed. `.env.example` contains only
placeholders. If you're migrating from an earlier draft of this project that had real
database/superuser credentials checked into `ROADMAP.md`, rotate those credentials --
anything committed to version control should be treated as compromised.
