# Project Roadmap: Intelligent Vehicle Installation Verification System

This roadmap outlines the phased development of the Python-powered, Django/PostgreSQL-backed
vehicle installation photo verification system with a Terminal User Interface (TUI).

---

## System Access & Administrative Credentials

**Credentials are never committed to this repository.** Copy `.env.example` to `.env` and
fill in real values locally:

* **PostgreSQL Database**: configured via `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` /
  `POSTGRES_HOST` / `POSTGRES_PORT` in `.env`.
* **Django Admin Portal**: `http://127.0.0.1:8000/admin/` (after `runserver`).
* **Superuser bootstrap**: set `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_EMAIL` /
  `DJANGO_SUPERUSER_PASSWORD` in `.env`, then run
  `python manage.py create_superuser_if_none` (idempotent, safe to re-run) --
  or just `python manage.py createsuperuser` interactively.

> An earlier draft of this file included real credentials in plaintext. Those were rotated
> and removed. If this repository was ever pushed anywhere with the old credentials in it,
> treat that database password and superuser password as compromised and rotate them.

---

## Roadmap Overview & Progress Status

```text
[x] Level 0: Foundation (Django + PostgreSQL + Evidence Vault + Deduplication)
     ↓
[x] Level 1: Vision Subsystem (Plate Detection, Preprocessing & Syntax Normalizer)
     ↓
[x] Level 2: Orientation & Pairwise Association (Front vs. Rear Linking)
     ↓
[x] Level 3: Fuzzy Order Matcher & State Transitions (RapidFuzz Bounded Matching)
     ↓
[x] Level 4: Python Operator TUI (Textual Dashboard + Side-by-Side Visual Viewer)
     ↓
[x] Level 5: Simulated ITMS Automation Worker & Fallback Engine
     ↓
[x] Level 6: End-to-End Benchmarking & Test Suite
     ↓
[x] Level 7: Bond Stock Reconciliation, Mobile Camera Scanning & TUI Standardization (v1.0.6)
     ↓
[x] Level 8: High-Capacity 100-Page Deep Crawling, 3-Hour Safe Room Daemon & Floor Discrepancy Isolation (v1.0.7)
```

Every level below has real, runnable code in this repository (see file paths). The one
piece that is intentionally left as a task for you: **the YOLO plate detector ships with
generic COCO weights (`yolov8n.pt`) as a placeholder** -- for production-grade accuracy on
Ugandan plates you need to fine-tune YOLO on a labeled plate dataset and point
`PLATE_YOLO_WEIGHTS` at your trained weights. See "Training your own plate detector" in
`README.md`. Until then, the OpenCV heuristic fallback keeps the full pipeline runnable
end-to-end on any machine with no extra downloads.

---

## Level 0: Foundation & Data Architecture (Django + PostgreSQL)

* **Goal**: Establish the local data store, evidence vault directory, migrations, and
  management utilities to manage installation orders and photo lifecycles without depending
  on external production systems.
* **Key Deliverables**:
  1. `[x]` **Django Project & Database Setup** -- `itms_project/settings.py`, `.env.example`.
  2. `[x]` **Core Data Models** (`core/models.py`): `InstallationOrder`, `EvidenceImage`,
     `VehicleInstallationPair`, `SubmissionAuditLog`.
  3. `[x]` **Order Seeder Command** (`core/management/commands/seed_orders.py`).
  4. `[x]` **Vault Ingest Command** (`core/management/commands/ingest_photos.py`) --
     SHA-256 dedup, immutable copy into `media/vault/YYYY-MM-DD/<uuid>.<ext>`.

## Level 1: Computer Vision & Plate OCR Subsystem

* `[x]` **Image Preprocessing** (`core/vision/preprocess.py`): CLAHE, bilateral denoise,
  Hough-based deskew, resolution normalization.
* `[x]` **Plate Detection** (`core/vision/detector.py`): YOLOv8 (ultralytics) primary,
  OpenCV Sobel/contour heuristic fallback.
* `[x]` **OCR** (`core/vision/ocr_engine.py`): PaddleOCR primary, pytesseract fallback.
* `[x]` **Normalization** (`core/vision/normalizer.py`): canonical formatting, Ugandan
  plate regex, positional character correction (`8`↔`B`, `0`↔`O`, `1`↔`I`, etc).
* `[x]` **Vision Pipeline Command** (`core/management/commands/process_vision.py`).

## Level 2: Orientation Classification & Pairwise Association

* `[x]` **Orientation Classifier** (`core/vision/orientation.py`): edge-weighted color-cue
  heuristic (red taillight clusters vs. white/yellow headlight clusters).
* `[x]` **Pairwise Association Engine** (`core/matcher/association.py`):
  - U-Turn / Snake traversal sequence alignment (row of $N$ bikes).
  - Walk session clustering ($>10$ min gap threshold).
  - Candidate photo ranking via time proximity, text similarity, and camera sequential numbers.
  - See full specification: [PAIRWISE_ASSOCIATION_TIME_AND_NAMING.md](PAIRWISE_ASSOCIATION_TIME_AND_NAMING.md).
* `[x]` **Camera Naming & Sequential Parser** (`core/services/camera_naming.py`):
  - Apple iPhone (`IMG_####`), Samsung (`YYYYMMDD_HHMMSS`), Google Pixel, Tecno/Xiaomi, WhatsApp (`WA####`).
* `[x]` **Association Command** (`core/management/commands/associate_pairs.py`).

## Level 3: Fuzzy Order Matcher & Lifecycle Engine

* `[x]` **Fuzzy Order Matcher** (`core/matcher/order_matcher.py`): RapidFuzz against the
  bounded set of active orders.
* `[x]` **Confidence Threshold Logic**: Exact (100) -> `EXACT`; Close (>=85) -> `FUZZY`;
  Low (<75) -> `UNREGISTERED_VEHICLE`; 75-85 -> held for operator review.

## Level 4: Python Operator Terminal User Interface (TUI)

* `[x]` **Modular Textual TUI Architecture**:
  - Modularized into `core/tui/app.py`, `tables.py`, `handlers.py`, `actions.py`, `inspectors.py`, `commands.py`, `auth_screens.py`, `dialogs.py`.
* `[x]` **Centralized Command Palette (`Ctrl+P` / `^P`)**:
  - Categorized execution: Pipeline, Review, ITMS Submission, Ingestion, Navigation, Filter, Storage, and System.
  - Dedicated closing mechanisms: `[✕ Close [Esc]]` header button, click backdrop outside dismiss, `Esc` key priority, and `Ctrl+P` toggle.
* `[x]` **Fast-Path Plate Typing & Order Autocomplete (`[T]`)**:
  - `PlateQuickEntryModal` dialog with live Tab autocomplete, prefix filtering, and arrow-key row selection sync.
  - Prefix matching resolution (e.g. `UMA94` -> `UMA946DQ`) with fallback database scan.
  - Automatic hardware sync (`front_plate_serial`, `gps_tracker_id`) from ITMS if missing locally.
* `[x]` **Isolated Operator Authentication Portal (`LandingAuthScreen`)**:
  - PBKDF2 SHA-256 operator account creation & authentication.
  - Streamlined account creation form: strictly Username, Password, and Confirm Password.
  - Keybinding isolation: filtered `_binding_chain` preventing operator shortcuts (`1-4`, `P`, `M`, `U`, `^P`) from leaking or triggering on login/register screens.
* `[x]` **Side-by-Side Visual Evidence Viewer** (`core/services/viewer.py`).
* `[x]` **Interactive Closest Photo Picker Modal** (`core/tui/dialogs.py`): Press `[L]` to browse, preview with BBox (`[V]`), and link (`[Enter]` / `[1-9]`).
* `[x]` **Single-Key Actions**: `[T]`ype/Match, `[V]`iew, `[A]`pprove, `[S]`wap, `[L]`ink/Pick, `[U]` Submit Single, `[B]` Batch Submit All, `[Y]` Sync Orders, `[R]`efresh, `[Q]`uit.
* `[x]` **TUI Command**: `python manage.py run_tui`.

## Level 5: Production ITMS Automation Worker & Live WebApp Integration

* `[x]` **Live ITMS Web Connector** (`core/services/itms_web.py`):
  - Authenticated session management against ITMS portal.
  - Step 1: Order lookup & parameter validation.
  - Step 2: Multipart photo upload replacing existing or uploading new front & rear evidence.
  - Step 3: Approval and final confirmation submission.
* `[x]` **Production Verification Milestone**:
  - Sample installation pairs successfully submitted, validated, and archived with status `Installed`, full audit trail, and replacement front/rear photos verified.
* `[x]` **Automated Order Synchronization Service** (`core/services/order_sync.py`):
  - Ingests active orders, serial numbers, and tracker IDs into local PostgreSQL cache.
  - Intelligent rate limiting and cache-time checks to prevent redundant network overhead.
* `[x]` **Simulated ITMS Mock Service** (`core/services/itms_mock.py`): 4-step workflow sandbox with configurable latency and failure injection.
* `[x]` **Submission Worker** (`core/services/submission_worker.py`): Unified interface for both `mock` and `web` backends with dry-run safety modes.
* `[x]` **Submission Commands**:
  - `python manage.py submit_itms [--backend=web|mock] [--dry-run]`
  - `python manage.py sync_itms_orders`

## Level 6: Benchmarking, Validation & Documentation

* `[x]` **Benchmarking Command** (`core/management/commands/benchmark_pipeline.py`).
* `[x]` **Comprehensive Test Suite**: `core/tests.py`, `core/tests_vision.py`, `core/tests_association.py`, `core/tests_auth.py`.
* `[x]` **Documentation**: `docs/propsal.md`, `docs/ROADMAP.md`, `docs/ITMS_WEBAPP_CONNECTION_AND_SESSION_ARCHITECTURE.md`, `README.md`.

## Level 7: Bond Stock Reconciliation, Mobile Camera Scanning & TUI Standardization (v1.0.6)

* `[x]` **Bond Stock Reconciliation & Daily Ledger Engine**:
  - `core/services/stock_monitoring_service.py`: Automated daily stock ledger reconciliation, discrepancy detection, and dynamic audit docket generation with semantic release versioning.
  - Management commands: `sync_stock_kits.py` (multi-page background crawler up to 35 pages) and `reconcile_shift.py` (shift audit with timestamped CSV exports).
* `[x]` **Mobile Companion Live Camera QR & Barcode Scanner**:
  - Web & PWA scanner (`/mobile/stock-scanner/`) with native `BarcodeDetector` + client-side ZXing-JS fallback.
  - Live video stream with animated laser guide, audio scan confirmation, camera switcher, torch toggle, and real-time stock ledger synchronization.
  - Terminal CLI direct launcher: `python itms_cli.py scan-camera`.
* `[x]` **Textual TUI Modernization & Standardization**:
  - Standardized non-scrolling tab bar with clean uppercase single-word titles: `DASHBOARD (1)`, `ITMS (2)`, `BATCHES (3)`, `REVIEW (4)`, `AUDIT (5)`, `REPORTS (6)`, `SETTINGS (7)`.
  - Reordered reports before settings (`REPORTS` = Tab 6, `SETTINGS` = Tab 7) across all keyboard bindings (`1..7`, `F1..F7`, `Ctrl+1..7`).
  - Review queue order unlinking (`X` / `Ctrl+U`) and modal action with database status reset and audit logging.
  - Synchronized Command Palette (`Ctrl+P`) with all newly added shortcuts: Joint Vision (`J`), Stock Manager (`K`), Offline Drain (`O`), Date Scope (`D`).
  - Restored live page number indicators across active orders, archives, and installation kits in ITMS Connect panel.
* `[x]` **System Standardization & User vs. Developer Settings**:
  - Partitioned settings hierarchy into Developer options (YOLO weight selector, confidence thresholds, sync budgets) and User options (reports export directory, batch folder, OCR preferences).
  - Native OS folder selector tool integration for report directories and batch paths.

## Level 8: High-Capacity Deep Crawling, 3-Hour Safe Room Daemon & Floor Discrepancy Isolation (v1.0.7)

* `[x]` **Deep Installation Kit Crawler Scaled to 100 Pages (2,000+ Kits)**:
  - Scaled default crawler traversal from 35 to 100 pages (`min 100 pages = 2,000+ installation kits`) across all UI tabs, background sync commands, and REST APIs.
  - Polite crawl pacing (`0.35s` sleep + randomized jitter) to maintain low load against `stock.itms.ug`.
  - HTTP 429 `Retry-After` header parsing, dynamic exponential backoff, and circuit breaker.
  - Replaced row-by-row queries with atomic chunked bulk upserts (`bulk_create` / `bulk_update` in chunks of 200 items), eliminating database table locking.
* `[x]` **3-Hour Asynchronous Safe Room Sync Daemon**:
  - `MorningKitSyncDaemon` configured with a 3-hour interval (10,800s) and 10-minute cooldown window, auto-starting in `run_web.py` and `app.py`.
  - Protected by `_global_kit_crawl_lock` to prevent overlapping crawls between UI triggers and background jobs.
* `[x]` **Floor Discrepancy Isolation & Elimination of Vision Pipeline OCR Noise**:
  - Completely decoupled safe-room warehouse stock (`InstallationKit` status `"New"`) from `#table-report-unallocated` and MVR exception dockets. Safe-room stock belongs exclusively in inventory ledger balances.
  - Eliminated camera OCR detections (`EvidenceImage`, `VehicleInstallationPair`) from stock reconciliation, guaranteeing zero partial text fragments or misread plates in stock dockets.
  - Ground truth floor liabilities are strictly anchored to physical barcode/plate scans (`StockDispatchScan`).
* `[x]` **Safe-Room "Set Aside" Pre-Dispatch Verification**:
  - Pre-dispatch validation against synchronized safe-room ITMS stock with 3-tier flow (Local check -> Live ITMS query & auto-provision -> Audio alert "Not On Stock").
  - Automatic set-aside blocking for kits not registered in ITMS, preventing unregistered plates from reaching assembly fitters.
  - Substitution workflow: Replace with valid in-stock kit while unlisted plate awaits ITMS Stock Transfer Officer confirmation.
* `[x]` **Mobile Companion U-Turn Walk Isolation & Batch Flow**:
  - Discrete session batches for off-conveyor walk photos with independent state reset on submit.
  - Clean pairing algorithm preventing previous batches (rear 1&2, front 2&1) from mixing with new batch images (rear 3&4, front 4&3).
  - Independent batch limits for off-conveyor walk photos without 100-pair conveyor limit conflicts.
* `[x]` **Mobile Web Companion Touch UI & Full-Screen Responsive Inspectors**:
  - Relocated Queue decision action buttons from fixed bottom sticky bar to natural scroll flow on mobile devices.
  - Responsive Mobile Inspector overlays with dedicated `◀ Back` navigation for ITMS table row inspection and Stock Ledger inspection on phone screens.



