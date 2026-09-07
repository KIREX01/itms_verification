# Project Proposal: Intelligent Vehicle Installation-Photo Recognition and Verification Automation System

## 1. Problem Statement

The current Intelligent Transport Management System (ITMS) installation verification workflow requires an operator to manually process installation orders and associate photographic evidence with the corresponding vehicle record. The installation-order registry provides a list of registration numbers for vehicles whose number plates are scheduled to have been fitted. For each installation, the operator must:
1. Search for the registration number.
2. Open the corresponding vehicle record.
3. Verify installation data such as plate serial numbers and trackers.
4. Manually identify, verify, and upload two critical photographic evidence files: **a front photograph** and **a rear photograph** showing the number plate installed cleanly and correctly.

### The Operational Challenge
When conducted across hundreds or thousands of vehicles, this manual process creates significant operational bottlenecks:
* **High Operator Latency**: Operators spend the majority of their time matching files, rotating photos, and navigating web forms.
* **Transcription & Misidentification Errors**: Human operators may misread plates (confusing similar characters like `8`/`B` or `0`/`O`), link photos to the wrong vehicle, or swap front and rear evidence.
* **Incomplete Records**: Orders are frequently stalled or submitted improperly when an image is missing or degraded without immediate feedback.

The core challenge is therefore not merely isolated plate recognition. It is an **end-to-end vehicle image identification, orientation classification, cross-evidence association, and workflow automation problem**.

---

## 2. Proposed Solution & Study Case Architecture

To address this challenge, the proposed project develops an **intelligent vehicle installation-photo recognition and ITMS automation system**. The system acts as an intelligent copilot and automation layer that automatically ingests unorganized photographic evidence, identifies plate numbers, classifies vehicle orientation (front vs. rear), matches candidates against active installation orders, and prepares verified records for submission.

### Study Case Scope & Technology Stack
To enable rapid prototyping, systematic evaluation, and reproducible benchmarking without risking live production systems, this project operates under a focused **Study Case Architecture**:

1. **Backend & Data Persistence (Django + PostgreSQL)**:
   * Rather than relying on external production APIs initially, the system utilizes **Django** paired with a local **PostgreSQL** database.
   * Django models manage the state of `InstallationOrder`, `Vehicle`, `EvidenceImage`, `PlateDetectionResult`, and `SubmissionAuditLog`.
   * Django provides an administrative registry, fixtures for ground-truth testing, and data integrity constraints.

2. **Simulated ITMS Environment**:
   * A mock ITMS endpoint/service is implemented within Django to replicate the multi-step external workflow (Order Lookup $\rightarrow$ Serial Verification $\rightarrow$ Front/Rear Photo Upload $\rightarrow$ Validation $\rightarrow$ Submission).
   * This decoupled design enables stress-testing edge cases (network timeouts, validation rejections, server errors) in a safe, controlled sandbox.

3. **User Interface (Python-Powered TUI)**:
   * The primary operational interface is a keyboard-driven **Terminal User Interface (TUI)** built using modern Python TUI frameworks (such as `Textual`).
   * The TUI allows operators to inspect processing batches, view real-time pipeline status, resolve ambiguous/low-confidence matches, and trigger automated uploads with minimal latency.

4. **Computer Vision & OCR Pipeline**:
   * **Pre-processing**: Perspective transformation, CLAHE contrast enhancement, resolution normalization.
   * **Plate Detection**: Deep-learning bounding box detector (e.g., YOLOv8/YOLOv11 nano).
   * **Text Recognition (OCR)**: Specialized recognition engine (PaddleOCR / Fast-Plate-OCR) paired with regex format constraints.
   * **Orientation Classifier**: Front vs. Rear classification model analyzing vehicle cues (headlights, grille, taillights, boot lid).

---

## 3. End-to-End System Workflow

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Installation Order List                         │
│             (Loaded into Django / PostgreSQL Local DB)                 │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Raw Photo Collection Ingestion                  │
│                     (Unlabeled Front & Rear Evidence)                  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         Computer Vision Layer                          │
│   ├── Image Pre-processing (Denoise, CLAHE, Angle Correction)          │
│   ├── Plate Detection (YOLO Bounding Box Localization)                 │
│   ├── Plate Character OCR (PaddleOCR Engine)                           │
│   └── Orientation Classification (Vehicle Front vs. Rear)              │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Association & Validation Engine                    │
│   ├── Plate Normalization (Standardize Spacing, Canonical Format)      │
│   ├── Fuzzy Matching against Installation Orders (RapidFuzz / Levenshtein)│
│   └── Pairwise Linking: Front Image + Rear Image = Complete Vehicle    │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  Operator Verification (Python TUI)                    │
│   ├── Status Dashboard (READY, NEEDS_REVIEW, INCOMPLETE, FAILED)       │
│   ├── Side-by-Side Front/Rear Preview & Confidence Metrics             │
│   └── Keyboard Actions: [ACCEPT], [OVERRIDE / EDIT], [RE-PAIR]         │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│               Simulated ITMS Submission Worker & Audit Log             │
│   ├── Multi-Step Automated Record Entry & Image Upload                 │
│   ├── Comprehensive Audit Logging (Timestamps, Plate, Status, Hash)    │
│   └── Robust Fallback Mechanism on Any Failure                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Component Operation

### Step 1: Controlled Order List Ingestion (Django / PostgreSQL)
* Active installation orders are loaded into PostgreSQL via Django management commands or CSV/Excel imports.
* Each order contains expected registration details (e.g., Ugandan standard plates: `UMA 123AA`, `UBB 456C`).
* The registry provides a **bounded search space**, shifting the problem from open-ended character recognition to **closed-set hypothesis testing**.

### Step 2: Photograph Ingestion, Evidence Vault & Traceability
To ensure zero data loss, prevent re-processing identical files, and guarantee forensic traceability, the ingestion subsystem operates as an **Evidence Vault**:

1. **Source Folder Prompt**: The operator specifies the folder containing the raw photos (e.g., from a field camera or flash drive).
2. **Cryptographic Deduplication (SHA-256)**:
   * Each image's byte stream is hashed using `SHA-256`.
   * The database is queried for `file_hash`. If the hash already exists, the file is skipped (`DUPLICATE_SKIPPED`), saving processing time and avoiding duplicate records.
3. **Safe Vault Copying (Zero Data Loss)**:
   * New images are immediately duplicated into an internal immutable vault: `media/vault/YYYY-MM-DD/<uuid>.<ext>`.
   * Even if the operator deletes the source folder or unplugs the drive, the system retains the permanent evidence copy.
4. **Relational Traceability**:
   * The database stores both the `original_source_path` and the `vault_file` path.
   * Internal states: `NEW`, `PROCESSING`, `PLATE_DETECTED`, `MATCHED`, `INCOMPLETE`, `NEEDS_REVIEW`, `READY`, `SUBMITTED`, `FAILED`.

### Step 3: Image Preprocessing & Plate Localization
* Images are checked for resolution, rotation, and lighting variations.
* A YOLO-based object detection model localizes the number plate bounding box, isolating the license plate from the background clutter.
* Perspective warping is applied where necessary to flatten tilted plates for optimal OCR readability.

### Step 4: Optical Character Recognition (OCR) & Normalization
* The isolated plate crop is processed by an OCR engine optimized for alphanumeric plate fonts.
* Plate strings are canonicalized: stripping extraneous spaces, hyphens, and uniform capitalization (e.g., `uma 123-aa` $\rightarrow$ `UMA123AA`).
* Syntax masks validate the alphanumeric sequence against standardized regional patterns.

### Step 5: Vehicle Orientation Classification (Front vs. Rear)
* To satisfy ITMS verification rules, the system must differentiate front from rear views.
* A lightweight convolutional network (or semantic visual cue detector) identifies vehicle components (headlights/grille vs. taillights/tailgate) and tags the image as `FRONT` or `REAR` with an associated confidence score.

### Step 6: Pairwise Vehicle Linking & Order Matching
* **Pairwise Association**: The system groups images sharing the same normalized registration number into pairs (`FRONT` + `REAR`).
* **Fuzzy Order Matcher**: When OCR yields minor character ambiguities (e.g., `UMA1238A` vs. order `UMA123BA`), fuzzy matching calculates Levenshtein distance and character substitution weights, flagging the entry for operator confirmation rather than rejecting it.
* **Completeness Assessment**: Vehicles missing either the front or rear image are flagged as `INCOMPLETE` with visual alerts in the TUI.

### Step 7: Human-in-the-Loop TUI Verification
* The operator utilizes a high-efficiency **Python TUI (Terminal User Interface)**.
* **Key TUI Capabilities**:
  * Real-time summary of batch health (Matched, Incomplete, Ambiguous).
  * High-speed keyboard navigation (Arrow keys, hotkeys for `[A]ccept`, `[R]eview`, `[E]dit`, `[S]ubmit All`).
  * Image inspection trigger (opening image previews in native viewers or terminal graphic protocols).
  * Clear confidence indicators (color-coded green/yellow/red).

### Step 8: ITMS Submission Automation & Fallback Safety
* Once an operator approves a record (or high-confidence batches are cleared), an automation worker simulates the ITMS multi-step submission:
  1. Locates the vehicle order record.
  2. Verifies tracker and plate identifiers.
  3. Uploads the front photograph into the front evidence field.
  4. Uploads the rear photograph into the rear evidence field.
  5. Validates submission success and logs audit metadata.
* **Fail-Safe Guarantee**: Automation will never block operations. Any failure (network timeout, format rejection, missing payload) triggers an immediate failure alert and enables standard manual fallback.

---

## 5. Summary of Core Project Objectives

The project delivers a cohesive solution across four primary dimensions:

1. **Accurate Recognition**: High-precision localization and character extraction on vehicle plates in natural, non-studio lighting.
2. **Intelligent Association**: Automated grouping of corresponding front and rear photographs into verified vehicle evidence pairs.
3. **Ergonomic Operator Interface**: A streamlined, lightweight Python TUI replacing tedious multi-click web workflows.
4. **Reliable Simulated Automation**: A resilient Django-backed automation layer capable of processing complete batches while preserving comprehensive audit trails.
