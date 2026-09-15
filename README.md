# Intelligent Vehicle Installation-Photo Recognition & Verification System (ITMS Verification Copilot)

Django/SQLite/PostgreSQL-backed computer vision verification pipeline that ingests unsorted vehicle installation photos, localizes and reads number plates via fine-tuned YOLOv8 models, pairs front/rear photos, matches against ITMS installation orders, and facilitates 1-click bulk submission via Web Console and Textual TUI.

---

## ⚡ 1-Click Quick Install

### Windows (PowerShell)
Open PowerShell and run:
```powershell
irm https://raw.githubusercontent.com/KIREX01/itms_verification/main/install.ps1 | iex
```
* Installs to `%LOCALAPPDATA%\Programs\ITMS-Verification` (no admin rights needed)
* Creates Desktop & Start Menu shortcuts
* Automatically provisions Python 3.11, virtual environment, and AI plate weights
* Opens `http://127.0.0.1:8000/` in your default browser

### macOS / Linux (Terminal)
```bash
curl -fsSL https://raw.githubusercontent.com/KIREX01/itms_verification/main/install.sh | bash
```

---

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
# 1. Load installation orders (choose either live sync or mock CSV)
# Option A: Fetch & sync live installation orders from ITMS WebApp (stock.itms.ug)
python manage.py fetch_itms_orders --page 1 --sync
# Option B: Seed mock/test orders from CSV
python manage.py seed_orders --csv sample_data/orders.csv

# 2. Ingest raw photos from a unified batch folder (or upload via browser at http://localhost:8000/upload/)
# Standard Batch Folder Layout:
#   Batch_Folder/
#     ├── front/   (Front-facing photos / headlamps / front plates)
#     └── rear/    (Rear-facing photos / brackets / rear plates)
python manage.py ingest_photos "C:\path\to\Batch_Folder" --recursive --batch-label "Shift 1 Entebbe"
# Options:
#   --recursive            Traverse subdirectories
#   --batch-label "name"   Human-readable label for this ingestion batch
#   --strict-counts        Enforce 1:1 front and rear photo counts, aborting on mismatch
#   --allow-asymmetric     Allow ingestion when front and rear counts differ (e.g. 5F / 4R)

# Web Upload UI & REST API:
#   - Browser UI:   http://localhost:8000/upload/   (live 1:1 symmetry validator & dropzones)
#   - REST API:     POST http://localhost:8000/api/upload/ (multipart form with 'front_photos', 'rear_photos')

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

# 5. Interactive Operator Dashboard (TUI): Review queue, history, batches, and ITMS Hub
python manage.py run_tui
# TUI Navigation & Hotkeys:
#   1 / 2 / 3 / 4          Switch Tabs: [1] Review Queue, [2] History & Audit, [3] Batches, [4] 🌐 ITMS WebApp
#   I                      Add Photos via Native Desktop Dialog (select files or entire folder/SD card)
#   W                      Launch Web Upload Interface in Browser (auto-falls back to native dialog if server offline)
#   F                      Cycle History Label Filter (ALL -> SUBMITTED -> FAILED -> APPROVED -> AUDIT)
#   P                      Run Vision Pipeline (background thread, streams to bottom console)
#   M                      Run Pair Matcher (background thread, streams to bottom console)
#   U                      Submit Selected Pair to ITMS (live step-by-step progress logging)
#   A                      Approve selected pair for submission
#   S                      Swap front and rear image assignments
#   L                      Link / Pick Photo to pair manually
#   V                      View evidence side-by-side in standalone Python window (fast, 50Hz, no Photo Viewer)
#   C                      Clean temporary crops & enforce vault retention policy
#   R                      Refresh data tables and metrics from database
#   X                      Sign out operator
#   Q                      Quit the TUI

# 6. Submit approved pairs to ITMS (simulated sandbox or live server)
python manage.py submit_itms --auto-approve
# Options:
#   --auto-approve         Promote complete high-confidence pairs to APPROVED first
#   --backend mock         Submit to simulated sandbox (default)
#   --backend live         Submit to live ITMS web app (https://stock.itms.ug)
```

### 3.1 Live ITMS WebApp Orders Synchronization (`fetch_itms_orders`)

The system directly interfaces with the live ITMS web application (`https://stock.itms.ug`) using reverse-engineered Yii2 session cookies (`_identity-frontend`, `advanced-frontend`, and `_csrf-frontend`), persisted locally in `media/vault/.itms_web_session.json` (mode 0600).

Features:
* **Safe Read-Only Ingestion**: Performs pure `GET` requests to inspect and retrieve fitment orders without sending any live modification/fitment changes to the ITMS server.
* **Smart Plate Spacing**: Automatically detects and normalizes unspaced Ugandan plates (e.g. converting `UMA946DQ` to `UMA 946DQ`) so ITMS SQL substring queries match reliably.
* **Pagination Support**: ITMS renders 20 records per page. Query page 1 (records 1–20), page 2 (records 20–40), etc.
* **Browser Query URL Pasting**: Paste the full search URL directly from your browser's address bar.
* **Rate-Limiting Cooldown**: 5-minute session caching prevents repetitive requests and protects against ITMS API limits.
* **Local Database Upsert**: The `--sync` flag upserts live orders into the local `InstallationOrder` table with canonical plate normalization (`normalizer.canonicalize`), immediately enabling automatic pairing with incoming photos.

```bash
# 1. Fetch first page of live installation orders (records 1-20)
python manage.py fetch_itms_orders --page 1

# 2. Fetch page 2 (records 20-40) and synchronize into local database
python manage.py fetch_itms_orders --page 2 --sync

# 3. Filter by Registration Number (auto-formats unspaced 'UMA946DQ' -> 'UMA 946DQ')
python manage.py fetch_itms_orders --plate "UMA946DQ" --sync

# 4. Filter by Vehicle VIN / Chassis number
python manage.py fetch_itms_orders --vin "LC6PCJBJ3S0052092" --sync

# 5. Filter by Status code (1: Ready for installation, 2: Under installation, 3: Ready for approve)
python manage.py fetch_itms_orders --status 3 --sync

# 6. Query by pasting the exact URL from the ITMS browser search bar
python manage.py fetch_itms_orders --query-url "https://stock.itms.ug/installation-orders/index?InstallationOrderSearch%5Bservice_type%5D=&InstallationOrderSearch%5Bregistration_number%5D=UMA946DQ&InstallationOrderSearch%5Bwarehouse_id%5D=&InstallationOrderSearch%5Bvin%5D=&InstallationOrderSearch%5Bold_registration_number%5D=&InstallationOrderSearch%5Bstatus%5D=" --sync

# 7. Query completed orders in Archive (/installation-orders/archive)
python manage.py fetch_itms_orders --archive --page 1 --sync
python manage.py fetch_itms_orders --archive --plate "UMA 282PG" --sync

# 8. Inspect complete order details, hardware inventory & download plate photos
# (Accepts ITMS UUID, order number, plate number, or URL)
python manage.py fetch_itms_orders --info "PO-UMA282PG-080926" --download-photos --sync
python manage.py fetch_itms_orders --info "ca7845a2-2488-495a-82cf-6a2e1502fa12"
python manage.py fetch_itms_orders --info "UMA 282PG"
```

> **Interactive TUI Hub**: In the TUI dashboard (`python manage.py run_tui`), press **`[4]`** to open the **`🌐 ITMS WebApp`** NavTab:
> - Query Active Orders (`[📥 Active Orders]`) or Completed Archive (`[🏛️ Archive (Installed)]`)
> - Inspect full order details, hardware inventory, and photo links (`[🔍 View Details & Photos]`)
> - Safely download evidence photos to local vault (`[💾 Download Photos]`)
> - Seamless pagination with `[◄ Prev]` and `[Next ►]`, and 1-click database synchronization (`[Sync Orders to DB]`).

### 3.2 Standardized Unified Ingestion & Pairwise Synergy

#### 1. Why Standardize on a Single Unified Batch Folder?
Previously, separate ingestion options allowed operators to upload Front photos and Rear photos independently. This fragmented the evidence into isolated single-orientation batches (e.g. Batch A with 5 Fronts, Batch B with 5 Rears), completely breaking pair association and leaving photos as orphaned, incomplete records.

The standardized workflow mandates **ONE root batch folder** with classified subfolders:
```text
📁 Batch_Folder/
   ├── 🚘 front/   (Front-facing motorcycle photos / headlamps / front plates)
   └── 🏍️ rear/    (Rear-facing motorcycle photos / taillights / rear plates)
```
Both orientations are ingested together into the **SAME** `IngestionBatch`, guaranteeing that photos belong to a unified evidence session and can be reliably paired.

#### 2. Photo Count Validation
Before proceeding with ingestion, the system validates that `/front` and `/rear` photo counts match 1:1:
* **Symmetric Counts ($N_{front} == N_{rear}$)**: Verified as 1:1 ratio. Ingestion proceeds immediately.
* **Asymmetric Counts ($N_{front} \neq N_{rear}$)**: Triggers an explicit discrepancy alert. In the Desktop Dialog / Web UI, operators must confirm before proceeding; in CLI, `--strict-counts` aborts while `--allow-asymmetric` permits ingestion. Unmatched surplus photos are registered as `INCOMPLETE` pairs.

#### 3. Synergy of File Naming & Timestamps ("When to Use What")
The pair association engine (`core/matcher/association.py`) solves the vehicle pairing problem using a transparent, multi-tier strategy:
1. **Tier 1: Plate OCR Match (Ground Truth)**
   * **When Used**: Both front and rear photos have high OCR confidence and matching plate text ($\ge 85\%$ similarity).
   * **Pairing Note**: `Auto-paired via Plate OCR Match ('UMA 123A', 100% similarity)`
2. **Tier 2: Filename Synergy (Matching Stems & Shutter Counters)**
   * **When Used**: Filenames in `front/` and `rear/` share identical clean stems (e.g. `01.jpg` <-> `01.jpg`, `bike1.jpg` <-> `bike1.jpg`) or identical camera sequence counters (`CAM01_0007.jpg` <-> `CAM02_0007.jpg` -> `#7`).
   * **Pairing Note**: `Auto-paired via Filename Stem Alignment ('01.jpg' <-> '01.jpg') | Identical stem '01'`
3. **Tier 3: Temporal Trajectory Alignment (Parallel vs U-Turn Walk)**
   * **When Used**: Filenames are sequential / single-phone continuous numbers (`IMG_0001` through `IMG_0020`) where front and rear numbers differ.
   * **Parallel Walk**: Photos taken simultaneously or front-then-rear at each vehicle ($\Delta t \approx 0$).
   * **U-Turn Walk**: Technician walked forward down rears (Bike $1 \to N$), turned around, and walked backward down fronts (Bike $N \to 1$). Whichever side started later is reversed.
   * **Pairing Note**: `Auto-paired via Temporal Trajectory (U-Turn Walk, Δt turnaround = 14s)`
4. **TUI Inspector Transparency**:
   * Every pair displays its plain-English `Pairing Reason:` directly in the Inspector Pane (`core/tui/inspectors.py`), so operators always know exactly when and why each strategy was used.

### 3.3 ITMS API Token Management (`itms_auth`)

The system also supports direct REST API token authentication using dual-token JWT (access token + refresh token) with automatic token refresh on 401:

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

### 3.4 Plate Crop Management & Storage Cleanup

When running vision processing or troubleshooting detection accuracy, intermediate plate crops can be saved to a organized date-partitioned directory (`media/crops/YYYY-MM-DD/{image_id}_plate.jpg`) instead of polluting the project root. Immutable vault originals (`media/vault/`) are protected and never touched.

```bash
# Clean up temporary crops and remove any stray root debug images
python manage.py clean_crops

# Preview files that would be deleted without removing them
python manage.py clean_crops --dry-run

# Only delete temporary crops older than 7 days
python manage.py clean_crops --older-than-days 7
```

### 3.5 Evidence Vault 1-Week Lifecycle Retention (`prune_vault`)

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

### 3.6 Resetting / Clearing Test Data (`clear_data`)

When testing new photo sets, onboarding new batches, or re-evaluating the vision pipeline from scratch, use `clear_data` to wipe all temporary evidence state and remove vaulted files on disk so SHA-256 hash deduplication does not mark re-uploaded photos as skipped duplicates:

```bash
# Clear all evidence images, batches, pairs, audit logs, and vaulted/crop files on disk
python manage.py clear_data

# Also clear the InstallationOrder registry table
python manage.py clear_data --include-orders

# Clear database records only, keeping files on disk in media/vault
python manage.py clear_data --keep-files
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

Out of the box, `PLATE_YOLO_WEIGHTS=models/license-plate-finetune-v1n.pt` points at a fine-tuned
YOLOv11 license plate detection model (cached from Hugging Face `morsetechlab/yolov11-license-plate-detection`).
The system supports both YOLOv11 and custom YOLOv8 models, with automatic fallback to the OpenCV
heuristic detector if weights are unavailable.

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
  models.py             InstallationOrder, EvidenceImage, VehicleInstallationPair, SubmissionAuditLog, IngestionBatch
  admin.py              Django admin registration
  vision/               preprocess.py, detector.py, ocr_engine.py, normalizer.py, orientation.py
  matcher/              association.py (pairwise linking), order_matcher.py (fuzzy matching)
  services/             itms_web_client.py (Yii2 WebApp session & order fetcher),
                        auth_service.py, file_dialog.py, vault_service.py,
                        itms_mock.py, itms_client.py, submission_worker.py, viewer.py
  tui/                  app.py (Textual dashboard), itms_pane.py (ITMS WebApp Hub),
                        auth_screens.py, widgets.py, inspectors.py, tables.py,
                        handlers.py, actions.py, styles.tcss
  management/commands/  fetch_itms_orders, seed_orders, ingest_photos, process_vision,
                        associate_pairs, submit_itms, itms_auth, run_tui, benchmark_pipeline,
                        clean_crops, prune_vault, clear_data, create_superuser_if_none
  tests*.py             pytest/Django test suite
sample_data/orders.csv  Demo installation-order registry
docs/                   propsal.md, ROADMAP.md, training.md
```

## 8. Security note

`.env` is git-ignored and must never be committed. `.env.example` contains only
placeholders. If you're migrating from an earlier draft of this project that had real
database/superuser credentials checked into `ROADMAP.md`, rotate those credentials --
anything committed to version control should be treated as compromised.
