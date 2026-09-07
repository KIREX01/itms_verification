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
* `[x]` **Pairwise Association Engine** (`core/matcher/association.py`).
* `[x]` **Association Command** (`core/management/commands/associate_pairs.py`).

## Level 3: Fuzzy Order Matcher & Lifecycle Engine

* `[x]` **Fuzzy Order Matcher** (`core/matcher/order_matcher.py`): RapidFuzz against the
  bounded set of active orders.
* `[x]` **Confidence Threshold Logic**: Exact (100) -> `EXACT`; Close (>=85) -> `FUZZY`;
  Low (<75) -> `UNREGISTERED_VEHICLE`; 75-85 -> held for operator review.

## Level 4: Python Operator Terminal User Interface (TUI)

* `[x]` **Textual TUI Dashboard** (`core/tui/app.py`): live metrics, `DataTable`,
  inspector pane.
* `[x]` **Side-by-Side Visual Evidence Viewer** (`core/services/viewer.py`).
* `[x]` **Single-Key Actions**: `[V]`iew, `[A]`pprove, `[S]`wap, `[R]`efresh, `[Q]`uit.
* `[x]` **TUI Command**: `python manage.py run_tui`.

## Level 5: Simulated ITMS Automation Worker & Fallback Engine

* `[x]` **Simulated ITMS Service** (`core/services/itms_mock.py`): 4-step workflow with
  configurable latency and failure injection.
* `[x]` **Automated Submission Worker** (`core/services/submission_worker.py`).
* `[x]` **Resilience & Fallback Handler**: any failure -> `FAILED` + `FALLBACK` audit entry.
* `[x]` **Submission Command**: `python manage.py submit_itms [--auto-approve]`.

## Level 6: Benchmarking, Validation & Documentation

* `[x]` **Benchmarking Command** (`core/management/commands/benchmark_pipeline.py`).
* `[x]` **Test Suite**: `core/tests.py`, `core/tests_vision.py`, `core/tests_association.py`.
* `[x]` **Documentation**: `docs/propsal.md`, `docs/ROADMAP.md`, `README.md`.
