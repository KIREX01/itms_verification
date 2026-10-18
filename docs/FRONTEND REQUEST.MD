# Frontend Requests & UI Integration Tasks

This document tracks all frontend tasks, UI component requests, and template/JS specifications requested by the Backend & DevOps team for integration with backend REST endpoints.

---

## 1. Feature: Replace Image & Delete Pair UI Components - [COMPLETED]

The backend endpoints and frontend UI templates, modals, CSS styling, and client-side JavaScript controllers for **Replace Image** and **Delete Pair** are fully implemented and verified:

### 1.1 Queue Tab: Replace Photo & Delete Pair Buttons
- **Files**:
  - `core/templates/core/tabs/tab_queue.html`
  - `core/static/core/js/tab_queue.js`
- **Requirements**:
  1. **Replace Photo Buttons**:
     - Inside the Front and Rear evidence photo cards, add a "Replace" button next to the orientation label:
       `<button type="button" class="btn btn-secondary btn-replace-photo" onclick="openReplaceImageModal('front')">🔁 Replace</button>`
       `<button type="button" class="btn btn-secondary btn-replace-photo" onclick="openReplaceImageModal('rear')">🔁 Replace</button>`
  2. **Delete Pair Button**:
     - In the operator decision bar, add a "Delete Pair" button:
       `<button class="btn-decision btn-danger" id="btn-action-delete" onclick="openDeletePairModal()" title="Delete pair and purge evidence files">🗑️ Delete Pair</button>`
  3. **Queue JS Controller (`tab_queue.js`)**:
     - Implement `openDeletePairModal()` and `confirmDeletePair()`:
       - Sends POST to `/api/pairs/${selectedPairId}/action/` with `action=delete_pair&purge_files=true`.
       - Optimistically removes pair from `pairsData` array, selects next pair in queue, and decrements queue count.
     - Implement `openReplaceImageModal(side)`:
       - Fetches candidates from `/api/batches/${batchId}/candidates/` when opening "Pick from Batch" tab.
       - Sends POST to `/api/pairs/${selectedPairId}/action/` with `action=replace_image&side=${side}&source=upload` (with FormData) or `source=batch&image_id=${id}`.
       - Updates the current pair image in-place upon success.

### 1.2 Batch Tab: Photo Card Replace Button
- **Files**:
  - `core/templates/core/tabs/tab_batches.html`
  - `core/static/core/js/tab_batches.js`
- **Requirements**:
  - In each batch photo card in the expanded photo grid, add a "Replace Photo" action button.
  - Clicking it opens `modal-replace-image` pre-targeted to `POST /api/images/${imageId}/replace/`.
  - Refreshes the photo card and plate badge dynamically upon successful replacement.

### 1.3 Modals: Confirmation & File Picker
- **File**: `core/templates/core/modals.html`
- **Requirements**:
  1. **Delete Pair Confirmation Modal** (`modal-delete-pair`):
     - Displays confirmation message with soft-delete explanation.
     - Includes checkbox: *"Purge heavy image files from vault storage"* (checked by default).
     - Action buttons: *"Confirm Delete & Purge"* (red) and *"Cancel"*.
  2. **Replace Image Modal** (`modal-replace-image`):
     - Dual-tab selector:
       - Tab A: **Upload New File** (drag-and-drop dropzone or file input).
       - Tab B: **Pick from Batch** (grid of candidate thumbnails loaded from `/api/batches/<batch_id>/candidates/`, with orientation and unassigned filter toggles).
     - Checkbox: *"Purge old image file from disk to free storage"* (checked by default).
     - Submit button: *"Replace Photo"*.

---

## 2. Frontend Refactoring & Architecture Requests (Ledger)

| ID | Component / File | Description & Concern | Priority | Status |
| :--- | :--- | :--- | :--- | :--- |
| **FR-01** | `mobile_companion.js` | **IndexedDB Storage Extraction**: Extract raw IndexedDB database transactions (`openOutboxDB`, `savePhotoToOutbox`, `drainOutboxQueue`) into a dedicated `services/outboxStorage.js` service module. | Medium | Identified |
| **FR-02** | `mobile_companion.js` | **Image Compression & Canvas Pipeline**: Separate client-side canvas resizing (`compressImageForUpload`) and Laplacian sharpness estimator into an isolated `services/imageProcessor.js` worker/module. | Low | Identified |
| **FR-03** | `mobile_companion.js` | **Web Audio Synthesizer**: Move Web Audio API tone synthesis (`playScanBeep`) into a reusable sound utility (`utils/audioEffects.js`). | Low | Identified |
| **FR-04** | `mobile_companion.js` | **Stock API Centralization**: Centralize scattered stock endpoints (`/api/stock/dispatch/`, `delivery`, `return`, `audit`) into a dedicated `api/stockClient.js` with structured response envelopes and error types. | Medium | Identified |

---

## Architecture Principles for Frontend Refactoring

1. **Separation of Presentation & Business Logic**:
   - UI templates (`.html`) should contain strictly presentation markup and ARIA accessibility attributes.
   - Event listeners and DOM bindings belong in view controllers (`tab_*.js`, `mobile_companion.js`).
   - Business calculations, validation rules, and offline queue storage belong in dedicated services or client state stores.
2. **Data Layer / API Client Isolation**:
   - Direct `fetch()` calls scattered across UI rendering functions should be centralized into dedicated API client wrappers with uniform error handling and retry mechanisms.
3. **State Management**:
   - Shared client state (e.g. current batch, selected pair, queue filters) should use an observable or deterministic state store rather than relying on DOM element attributes.
---

## 3. Feature: Stock Monitoring Tool - Manual Entry & Report Email UI Integration - [COMPLETED]

The frontend UI templates, modals, CSS styling, and client-side JavaScript controllers for **Quick Count Inbound Delivery**, **Direct Ledger Entry / Overrides (9-Row Full Metric Editor)**, and **Report Email Dispatch** are fully implemented and verified:

### 3.1 Implemented Components & Files
- **Modals (`core/templates/core/modals.html`)**:
  - `#modal-quick-delivery`: Fast safe-room bulk intake by quantity and category (PSV/PMO) with waybill ref and auto-provisioning option.
  - `#modal-edit-ledger`: Full 9-row direct numeric override editor for Opening, Received, Scheduled, Installed, Transfers In/Out, and Physical Count, with auto-fill from current shift and live calculated totals.
  - `#modal-email-stock-report`: Official reconciliation report email dispatch with recipient list, operator/ITMS account inclusion toggles, and shift notes.
- **Stock Tab Navigation & Action Strips (`core/templates/core/tabs/tab_stock.html`)**:
  - Universal Command Bar: Added `[📦 Quick Delivery]` button next to `[📋 Paste]`.
  - Mode 2 (Movements) action bar: Added `[📦 Quick Delivery]` button for fast inbound intake without scanning.
  - Mode 4 (Ledger) action bar: Added `[✏️ Edit Ledger (9-Row Overrides)]` and `[📧 Email Report]` buttons alongside `[✎ Opening & Target]` and `[📊 Export Reconciliation CSV]`.
- **View Controller (`core/static/core/js/tab_stock.js`)**:
  - `openQuickDeliveryModal()`, `closeQuickDeliveryModal()`, `submitQuickDelivery()`: Dispatches to `POST /api/stock/delivery/`, refreshes ledger and movement metrics.
  - `openEditLedgerModal()`, `closeEditLedgerModal()`, `prefillEditLedgerFromCurrent()`, `calcEditLedgerTotals()`, `submitEditLedger()`: Pre-fills metrics from `lastReconData`, computes row totals, and dispatches to `POST /api/stock/ledger/entry/`.
  - `openEmailStockReportModal()`, `closeEmailStockReportModal()`, `submitEmailStockReport()`: Dispatches to `POST /api/stock/report/email/` and notifies recipient count.
- **Cache Busting (`core/templates/core/base.html`)**:
  - Bumpered `tab_stock.js?v=14`.

### 3.2 Endpoint Specifications

#### A. Inbound Count Delivery (`POST /api/stock/delivery/`)
- **Payload (`application/json` or `multipart/form-data`)**:
  ```json
  {
    "quantity": 200,
    "plate_category": "PSV",
    "supplier": "Factory / Central Depot",
    "paper_note_reference": "DN-89211",
    "target_date_suffix": "181026",
    "notes": "Morning bulk delivery"
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "delivery_number": "DN-261018-01",
    "plates_count": 200,
    "plate_category": "PSV",
    "reconciliation": { ... }
  }
  ```

#### B. Direct Ledger Entry / Overrides (`POST /api/stock/ledger/entry/`)
- **Payload (`application/json`)**:
  ```json
  {
    "target_date_suffix": "181026",
    "storage_bond_name": "AGM / SPIRO",
    "opening_balance_pmo": 90,
    "opening_balance_psv": 857,
    "kits_received_pmo": 0,
    "kits_received_psv": 200,
    "scheduled_pmo": 0,
    "scheduled_psv": 300,
    "scheduled_total": 300,
    "kits_installed_pmo": 0,
    "kits_installed_psv": 317,
    "bond_transfer_in_pmo": 0,
    "bond_transfer_in_psv": 0,
    "bond_transfer_out_pmo": 90,
    "bond_transfer_out_psv": 0,
    "physical_count": 740,
    "notes": "Shift sign-off"
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "message": "Daily stock ledger updated successfully.",
    "reconciliation": { ... }
  }
  ```

#### C. Report Email Dispatch (`POST /api/stock/report/email/`)
- **Payload (`application/json`)**:
  ```json
  {
    "target_date_suffix": "181026",
    "recipients": ["supervisor@example.com", "operations@spiro.ug"],
    "include_operator_email": true,
    "include_itms_email": true,
    "notes": "Daily stock reconciliation report attached"
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "message": "Stock reconciliation report sent successfully.",
    "recipients": ["operator@test.com", "supervisor@example.com"],
    "work_date_suffix": "181026"
  }
  ```
