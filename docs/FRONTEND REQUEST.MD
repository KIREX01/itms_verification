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

## 4. Standardized CSV Export Categories & Spiked Kit Audit UI - [COMPLETED]

The backend now provides standardized, distinct CSV exports and explicit spiked / undispatched kit detection.

### 4.1 New Export Category: Spiked / Undispatched Kits
- **Endpoint**: `GET /api/stock/export/category/spiked/?date=<suffix>` (alias: `/api/stock/export/category/undispatched/?date=<suffix>`)
- **Headers in CSV**: `Row, Plate Number, Plate Category, Order Number, Order Status, System Stage, Safe Room Dispatch Scan, VIN, Operating Facility, Audit Alert`
- **Purpose**: Exports all kits that bypassed the safe-room dispatch scanner but appear in the ITMS installation queue or archive.

### 4.2 Updated Reconciliation & Date-Driven Payload (`GET /api/stock/reconcile/` & `GET /api/reports/totals/`)
- In `reconciliation.floor_operations`:
  ```json
  {
    "spiked_undispatched_count": 2,
    "spiked_installed_count": 1,
    "spiked_pending_count": 1,
    "spiked_undispatched": ["UMA901SP", "UMA902SP"],
    "graduated_scale": {
      "spiked_undispatched": {
        "count": 2,
        "plates": ["UMA901SP", "UMA902SP"],
        "installed_count": 1,
        "pending_count": 1,
        "label": "Undispatched System Plates (Spiked on Line without Safe Room Scan)",
        "badge": "warning"
      }
    }
  }
  ```
- In `reports.date_driven.spiked_undispatched`:
  ```json
  {
    "total_count": 2,
    "installed_count": 1,
    "pending_count": 1,
    "items": [
      {
        "plate": "UMA901SP",
        "display_plate": "UMA 901SP",
        "status": "In ITMS Archive (Installed)",
        "reason": "Kit verified/installed in ITMS without safe room dispatch scan",
        "date": "18.10.2026"
      }
    ]
  }
  ```

### 4.4 Implemented Components & Verification
- **Stock Tab Export Dropdown (`core/templates/core/tabs/tab_stock.html`)**:
  - Added `⚠️ Spiked Undispatched Kits CSV` calling `exportCategoryFromStock('spiked')`.
- **Stock Dashboard Audit Alert (`core/static/core/js/tab_stock.js`)**:
  - Updated `updateDashboard(recon)` to evaluate `recon.floor_operations.spiked_undispatched_count`. When > 0, renders a warning badge (`⚠️ Spiked Undispatched: N`) with click-to-download integration.
- **Reports Tab Category 3 Audit Alert (`core/static/core/js/tab_reports.js`)**:
  - Updated `renderDateDrivenSection(dd)` to detect `dd.spiked_undispatched`. When count > 0, updates `#report-cat3-badge` to `⚠️ N Spiked Undispatched` with amber alert styling and click-to-download action.
- **Reports Shift Dropdown (`core/templates/core/tabs/tab_reports.html`)**:
  - Added `⚠️ Spiked Undispatched Kits CSV` option invoking `downloadCategoryCsv('spiked')` with active shift suffix fallback.

### 4.3 High-Performance Shift Verification Export (`GET /api/export/report/?date=<suffix>`)
- Now streams directly as `text/csv; charset=utf-8` using HTTP chunked transfer encoding.
- Includes UTF-8 BOM (`\xef\xbb\xbf`) and sequential `Row` numbers (1..N).
- Zero blank spacer rows between records for seamless Excel and spreadsheet processing.


## 5. Centralized Shift Date & Shift Selector Migration (Tab Cleanup) - [COMPLETED]

### 5.1 Architecture & Objectives
To streamline operator workflows and eliminate state desynchronization across the platform, all local shift/date selectors in individual tabs (such as the Stock Tab and Reports Tab) are being migrated to the **top centralized header selector** (`#global-shift-select` in `core/templates/core/base.html`). Individual tabs must no longer maintain independent date dropdowns. Instead, they will:
1. Rely on `#global-shift-select` as the single source of truth for the active shift date.
2. Listen for the `shiftDateChanged` window event dispatched by `pipeline.js`.
3. Display the currently active global shift in their status badge/pill rather than an interactive select.
4. Auto-populate modal dates (e.g., Stock Inbound Delivery, Ledger Overrides, Email Dispatch) with the active global shift date.

### 5.2 Actions Required by UI/UX & Frontend Developer

#### 1. Stock Tab (`core/templates/core/tabs/tab_stock.html` & `tab_stock.js`)
- **HTML Cleanup**:
  - In `tab_stock.html`, remove `<select id="stock-shift-select" ...>`.
  - Replace it with a read-only shift indicator pill/badge:
    ```html
    <div class="stock-shift-pill-container d-flex align-items-center gap-2">
      <span class="badge badge-dark d-flex align-items-center gap-1 py-2 px-3 fs-6">
        <i class="fas fa-calendar-alt text-warning"></i>
        <span>Active Shift:</span>
        <strong id="stock-active-shift-badge" class="text-info">Loading...</strong>
      </span>
    </div>
    ```
- **JavaScript Updates (`tab_stock.js`)**:
  - Remove all event listeners and DOM references attached to `#stock-shift-select`.
  - Listen for the global shift change event:
    ```javascript
    window.addEventListener("shiftDateChanged", function(e) {
      const activeShift = e.detail; // { suffix, formatted_date, display, is_today, mode }
      updateStockActiveShiftDisplay(activeShift);
      loadStockReconciliation(); // Automatically reloads stock data for the new global shift
    });
    ```
  - When opening modals (Delivery Intake modal, Ledger Override modal, Email Dispatch modal):
    - Default the date input field to `window.activeShiftDateInfo?.suffix || activeShift.suffix`.
  - In `loadStockReconciliation(shiftSuffix)`:
    - If no `shiftSuffix` is passed, retrieve from `window.activeShiftDateInfo?.suffix` or let the backend use its active shift fallback by omitting `?date=`.

#### 2. Reports Tab (`core/templates/core/tabs/tab_reports.html` & `tab_reports.js`)
- **HTML Cleanup**:
  - In `tab_reports.html`, remove `<select id="report-date-select" ...>`.
  - Replace with an active shift indicator badge matching the Stock tab styling:
    ```html
    <div class="report-shift-pill-container d-flex align-items-center gap-2">
      <span class="badge badge-dark d-flex align-items-center gap-1 py-2 px-3 fs-6">
        <i class="fas fa-calendar-alt text-warning"></i>
        <span>Active Shift:</span>
        <strong id="report-active-shift-badge" class="text-info">Loading...</strong>
      </span>
    </div>
    ```
- **JavaScript Updates (`tab_reports.js`)**:
  - Remove event listeners on `#report-date-select`.
  - Hook into `shiftDateChanged`:
    ```javascript
    window.addEventListener("shiftDateChanged", function(e) {
      const activeShift = e.detail;
      updateReportActiveShiftDisplay(activeShift);
      loadReportTotals(); // Reload report totals and breakdown for the active shift
    });
    ```
  - When triggering exports (`/api/export/report/`, `/api/stock/export/category/...`):
    - Omit the `?date=` parameter or pass `window.activeShiftDateInfo?.suffix`. When omitted, the backend automatically uses the centralized shift.

#### 3. Global Pipeline Controller (`core/static/core/js/pipeline.js`)
- In `populateAllShiftDropdowns(availableDates, activeShift)`:
  - Populate `#global-shift-select` in the header bar.
  - Ensure the options include an "All Dates (Aggregate)" option (`<option value="ALL">All Dates (Aggregate)</option>`) alongside available shift dates.
  - Remove DOM population logic for removed IDs (`stock-shift-select`, `report-date-select`).
  - Cache active shift details on `window.activeShiftDateInfo = activeShift;`.

### 5.3 Implementation Status & Component References - [COMPLETED]
All local shift selectors on Stock and Reports tabs have been dismantled and migrated to the centralized header selector:
1. **Global Pipeline Controller (`core/static/core/js/pipeline.js`)**:
   - `populateAllShiftDropdowns(dates, activeShift)`: Exclusively populates `#global-shift-select` in the header bar with `ALL` (Aggregate) and daily shift options. Removed obsolete DOM population for removed local dropdowns.
   - `syncAllDateSelectorsUI(shiftInfo)`: Synchronizes header selector, updates read-only badges (`#stock-active-shift-badge` and `#report-active-shift-badge`), and caches active shift data in `window.activeShiftDateInfo`.
2. **Stock Tab (`core/templates/core/tabs/tab_stock.html` & `core/static/core/js/tab_stock.js`)**:
   - Replaced interactive `<select id="stock-shift-select">` with read-only badge container `#stock-active-shift-badge`.
   - Added `updateStockActiveShiftDisplay(activeShift)` and bound window `shiftDateChanged` listener to trigger auto-reload of stock reconciliation ledger.
3. **Reports Tab (`core/templates/core/tabs/tab_reports.html` & `core/static/core/js/tab_reports.js`)**:
   - Replaced interactive `<select id="report-date-select">` with read-only badge container `#report-active-shift-badge`.
   - Added `updateReportActiveShiftDisplay(activeShift)` and bound window `shiftDateChanged` listener to re-fetch report totals and category breakdowns.
4. **Cache Busting (`core/templates/core/base.html`)**:
   - Bumped version parameters: `pipeline.js?v=13`, `tab_stock.js?v=17`, `tab_reports.js?v=15`, `tab_stock.css?v=13`, `tab_reports.css?v=15`.


## 6. History Page Audit Enhancements for Email & Ledger Events

### 6.1 Backend Enhancements
- Email dispatches (`STOCK_REPORT_EMAILED`) and ledger updates (`STOCK_LEDGER_UPDATED`) now write complete `SubmissionAuditLog` records with explicit `ResultStatus.SUCCESS` or `ResultStatus.FAILURE`.
- When an email dispatch succeeds:
  - `result`: `"SUCCESS"` (badge green).
  - `plate`: `"SHIFT_<work_suffix>"` (e.g. `SHIFT_181026`).
  - `order_number`: `"EMAIL_DISPATCH"`.
  - `message`: Lists recipients and delivery confirmation message.
- When an email dispatch fails:
  - `result`: `"FAILURE"` (badge red).
  - `plate`: `"SHIFT_<work_suffix>"`.
  - `order_number`: `"EMAIL_DISPATCH"`.
  - `message`: Specific error diagnosis (e.g. `FAILED emailing stock report for shift 181026 to ...: Resend API error: Rate limit exceeded`).
- `GET /api/history/?filter=FAILED` now captures both failed pair submissions and failed email dispatches.
- `GET /api/history/?filter=EMAIL` filters directly to email dispatches.

### 6.2 UI/UX & Frontend Developer Specifications
1. **Filter Chips in `core/templates/core/tabs/tab_history.html`**:
   - Add an optional filter chip for email dispatches:
     ```html
     <button class="history-chip" data-hfilter="EMAIL" onclick="setHistoryFilter('EMAIL')">Emails</button>
     ```
2. **Detail Modal in `core/templates/core/modals.html` (`#modal-history-detail`)**:
   - When inspecting an email dispatch log, the modal cleanly displays the shift suffix in the plate field, the action as `STOCK_REPORT_EMAILED`, the result as `SUCCESS` or `FAILURE`, and the full recipient and error information in the execution trace box.


## 7. Bug Fix: `confirmDeletePair` Uncaught ReferenceError (`renderQueueList`)

### 7.1 Defect Description
When an operator deletes a pair in the Queue tab via `confirmDeletePair()`, the backend API call `POST /api/pairs/<pair_id>/action/` (with `action=delete_pair`) succeeds and soft-deletes the pair.
However, immediately after removing the pair from `pairsData`, line 807 of `core/static/core/js/tab_queue.js` calls:
```javascript
renderQueueList();
```
Because no function named `renderQueueList` exists (the rendering function defined and used across `tab_queue.js` is `renderQueueCards()`), JavaScript throws:
```
Uncaught ReferenceError: renderQueueList is not defined
```
This unhandled error aborts execution of the remaining logic:
- Metric refresh `fetchStats()` is skipped.
- Selection of the adjacent pair (`selectPair(...)`) or clearing the inspector is aborted.
- The `catch` block catches the ReferenceError and displays a false failure toast to the operator: `Error deleting pair: ReferenceError: renderQueueList is not defined`.

### 7.2 Required Frontend Fix
In `core/static/core/js/tab_queue.js` at line 807:
```javascript
// Replace line 807:
renderQueueList();

// With:
renderQueueCards();
```


## 8. Brand Transition to "CLOSE Copilot" (Phase 1: Surface & Frontend Rebranding) - [COMPLETED]

### 8.1 Context & Strategic Objectives
The platform is transitioning its user-facing brand identity from *"ITMS Verification Copilot"* to **`CLOSE Copilot`** with the official tagline:
> **"Focus on plate audit, inspection, and inventory"**

This transition aligns with our domain `close.kirex.online`, insulates operational workflows, and eliminates misrepresentation risks prior to formal central procurement.

**Strict Boundaries for Phase 1**:
- **Scope**: Frontend templates, headers, page titles, meta tags, and footers only.
- **Unchanged**: All backend logic, REST APIs, internal database models, and the **ITMS Registry Tab (`tab_itms.html`)** remain untouched and fully functional.

---

### 8.2 Checklist of Required Template Updates

#### 1. Global Shell & Navigation (`core/templates/core/base.html`)
- **Page `<title>`**:
  ```html
  <title>{% block title %}CLOSE Copilot | Plate Audit & Inspection Platform{% endblock %}</title>
  ```
- **Social & Open Graph Metadata**:
  - `meta[property="og:site_name"]`: `content="CLOSE Copilot"`
  - `meta[property="og:title"]`: `content="CLOSE Copilot - Plate Audit, Inspection & Inventory"`
  - `meta[property="og:description"]`: `content="Depot plate audit, vehicle evidence inspection, and hardware inventory management."`
  - `meta[name="twitter:title"]`: `content="CLOSE Copilot - Plate Audit, Inspection & Inventory"`
  - `meta[name="twitter:description"]`: `content="Depot plate audit, vehicle evidence inspection, and hardware inventory management."`
- **Header Brand Section (`.brand-section .brand-titles`)**:
  ```html
  <div class="brand-titles">
      <span class="brand-name">CLOSE <span class="accent">COPILOT</span></span>
      <span class="brand-sub">Focus on plate audit, inspection, and inventory</span>
  </div>
  ```
- **Application Footer (`.app-footer`)**:
  - Replace:
    ```html
    <span>Uganda National ITMS Verification Copilot • v2.6</span>
    ```
  - With:
    ```html
    <span>CLOSE Copilot • Depot Verification & Inventory Platform • v2.6</span>
    ```

---

#### 2. Operator Authentication Suite

##### A. Sign In (`core/templates/core/login.html`)
- **Page `<title>`**:
  ```html
  <title>Sign In - CLOSE Copilot</title>
  ```
- **Metadata**:
  - `meta[property="og:site_name"]`: `content="CLOSE Copilot"`
  - `meta[property="og:title"]`: `content="Sign In - CLOSE Copilot"`
  - `meta[property="og:description"]`: `content="Depot plate audit, vehicle evidence inspection, and hardware inventory management."`
- **Header Branding (`.auth-card-head`)**:
  ```html
  <h1 style="font-size:1.35rem; font-weight:800; color:#ffffff; letter-spacing:0.5px;">
      CLOSE <span style="color:var(--ug-yellow);">COPILOT</span>
  </h1>
  <p style="font-size:0.75rem; color:var(--ug-text-muted); margin-top:2px;">
      Focus on plate audit, inspection, and inventory • Operator Sign In
  </p>
  ```

##### B. Sign Up (`core/templates/core/signup.html`)
- **Page `<title>`**:
  ```html
  <title>Create Operator Account - CLOSE Copilot</title>
  ```
- **Metadata**:
  - `meta[property="og:site_name"]`: `content="CLOSE Copilot"`
  - `meta[property="og:title"]`: `content="Create Operator Account - CLOSE Copilot"`
- **Header Branding (`.auth-card-head`)**:
  ```html
  <h1 style="font-size:1.35rem; font-weight:800; color:#ffffff; letter-spacing:0.5px; margin:0;">
      CLOSE <span style="color:var(--ug-yellow);">COPILOT</span>
  </h1>
  <p style="font-size:0.75rem; color:var(--ug-text-muted); margin-top:4px;">
      Focus on plate audit, inspection, and inventory • Create Account
  </p>
  ```

##### C. 2FA Security Code Verification (`core/templates/core/signup_verify.html`)
- **Page `<title>`**:
  ```html
  <title>Verify Security Code - CLOSE Copilot</title>
  ```
- **Metadata**:
  - `meta[property="og:site_name"]`: `content="CLOSE Copilot"`
  - `meta[property="og:title"]`: `content="Verify Security Code - CLOSE Copilot"`
- **Header Branding (`.auth-card-head`)**:
  ```html
  <h1 style="font-size:1.35rem; font-weight:800; color:#ffffff; letter-spacing:0.5px; margin:0;">
      CLOSE <span style="color:var(--ug-yellow);">COPILOT</span>
  </h1>
  <p style="font-size:0.75rem; color:var(--ug-text-muted); margin-top:4px;">
      Two-Factor Authentication • Security Code Verification
  </p>
  ```

---

#### 3. Mobile Companion Scanner (`core/templates/core/mobile_companion.html`)
- **Page `<title>`**:
  ```html
  <title>CLOSE Mobile Scanner - Rapid Plate & Barcode Capture</title>
  ```
- **Metadata**:
  - `meta[property="og:site_name"]`: `content="CLOSE Mobile Scanner"`
  - `meta[property="og:title"]`: `content="CLOSE Mobile Companion - Rapid Evidence & Barcode Capture"`
  - `meta[property="og:description"]`: `content="Mobile evidence capture, barcode validation, and depot inspection assistant."`
- **Mobile Header (`.header-branding`)**:
  ```html
  <div>
      <h1 class="brand-title">CLOSE MOBILE SCANNER</h1>
      <div class="brand-subtitle">
          PLATE AUDIT & INSPECTION COMPANION
          {% if is_cloud_sync %}<span style="color:#10b981; font-weight:800; font-size:0.68rem; margin-left:4px;">• ☁️ CLOUD SYNCED</span>{% endif %}
      </div>
  </div>
  ```

---

#### 4. Settings View & System Banner (`core/templates/core/tabs/tab_settings.html`)
- In the System Diagnostics / About card (around line 265):
  - Replace:
    ```html
    Running ITMS Verification Copilot v{{ app_version|default:"1.0.2" }}. Updates are distributed via GitHub Releases.
    ```
  - With:
    ```html
    Running CLOSE Copilot v{{ app_version|default:"1.0.8" }}. Updates are distributed via GitHub Releases.
    ```

---

#### 5. Web App Manifest (`core/static/core/logos/site.webmanifest`)
- Update application display names:
  ```json
  {
    "name": "CLOSE Copilot - Plate Audit & Inspection",
    "short_name": "CLOSE Copilot"
  }
  ```

---

#### 6. Preservation of ITMS Registry Tab (`core/templates/core/tabs/tab_itms.html`)
- **DO NOT MODIFY** the title, layout, or functional sync controls of `tab_itms.html`.
- The Registry tab continues to represent the external ITMS order synchronization interface as requested.

---

### 8.4 Implementation & Verification Status - [COMPLETED]
- **Verification Status**: Fully verified on local and remote EC2 production environment (`https://close.kirex.online/`).
- **Commit Reference**: `39385bb` (*feat(ui): transition surface branding to CLOSE Copilot with plate audit & inspection tagline*).
- **Deliverables Completed**:
  - `index.html`: Header logo, page `<title>`, OpenGraph/Twitter social meta tags, and footer badge updated to CLOSE Copilot.
  - `login.html`: Branded head section and `<title>` updated to CLOSE Copilot.
  - `signup.html`: Branded head section and `<title>` updated to CLOSE Copilot.
  - `auth_2fa.html`: Security code header and `<title>` updated to CLOSE Copilot.
  - `mobile_companion.html`: Header title updated to `CLOSE MOBILE SCANNER` with `PLATE AUDIT & INSPECTION COMPANION` subtitle.
  - `tab_settings.html`: System diagnostics card displays `CLOSE Copilot v1.0.8`.
  - `site.webmanifest`: Progressive Web App (PWA) manifest names updated.
  - `tab_itms.html`: Preserved without modification as required.


## 9. Bugfix: History Tab Filter Handlers & Variable Scope Resolution - [COMPLETED]
- **Context & Error Encountered**:
  - When operators toggled history filter pills in `tab_history.html`, the browser console reported `Uncaught ReferenceError: setHistoryFilter is not defined`.
  - Concurrently, loading `tab_history.js` caused `Uncaught SyntaxError: Identifier 'currentHistoryFilter' has already been declared`.
- **Resolution Implemented**:
  1. **Global Handler Definition**: Defined `window.setHistoryFilter = function(filter)` in `tab_history.js` to ensure the onclick attribute in `tab_history.html` invokes the filter controller without reference scoping issues.
  2. **Deduplication**: Removed duplicate `let currentHistoryFilter` declaration, standardizing on a single module-scoped state variable with default value `"ALL"`.
- **Verification**: Toggling All, Approved, Rejected, Live Writes, and Email Dispatches works seamlessly with zero console errors.


## 10. Feature Specification: Direct Single-Order Installation & 1-Click Submission UI
- **Status**: Completed & Verified
- **Target Surfaces**:
  - Orders UI & Modal: `core/templates/core/tabs/tab_orders.html`, `core/templates/core/modals.html`, `core/templates/core/tabs/tab_itms.html`
  - Controllers: `core/static/core/js/tab_orders.js`, `core/static/core/js/tab_itms.js`
  - Mobile Companion: `core/templates/core/mobile_companion.html`, `core/static/core/js/mobile_companion.js`
- **Background & Workflow Objectives**:
  - For operators handling individual walk-in bikes or single orders, enable a direct "search -> save & proceed -> capture/attach photos -> 1-click submit" workflow without needing the bulk batch photo ingestion pipeline or leaving the dashboard.
- **Implemented Components**:
  1. **Order Row "⚡ Quick Install" Action**:
     - Added an Action column with `⚡ Quick Install` button to active orders table rows (`tab_itms.html` & `tab_itms.js`).
     - Added a prominent `⚡ Quick Install Order` button to the Live Inspector panel when selecting an order.
  2. **Stage-Aware Inspector Modal (`#order-install-modal`)**:
     - Created `core/templates/core/tabs/tab_orders.html` included in `modals.html`.
     - Displays live stage detection badge (`STAGE_1_INSTALLATION`, `STAGE_2_EVIDENCE`, `STAGE_3_CONFIRMATION`, `STAGE_ARCHIVED`).
     - Prepopulated hardware fitment inputs with Step 1 save action (`POST action=save_step1`).
     - Dual photo dropzones with camera capture (`<input type="file" accept="image/*" capture="environment">`), instant thumbnails, and OCR detection badges.
     - 1-Click final submission button (`POST action=submit`) with dry-run toggle, step-by-step progress feedback, toast dispatch, and real-time row status badge update to `INSTALLED`.
  3. **Mobile Companion Mode 4: Quick Walk-in Scanner**:
     - Added `⚡ Quick Install` mode switch in `mobile_companion.html`.
     - Built dedicated container (`#container-order`) with order lookup, stage badge, hardware serials, dual camera capture, and 1-tap final submission.
     - Handled in `mobile_companion.js` with `lookupMobileOrder()`, `saveMobileStep1()`, `triggerMobileOrderPhoto()`, and `submitMobileOrderInstall()`.


## 11. Mobile Responsiveness & Filter Funnel Architecture (Universal Implementation)
- **Status**: Completed & Verified
- **Target Surfaces**:
  - Global Shell & Header: `core/templates/core/base.html`, `core/static/core/css/app_layout.css`
  - JavaScript Controllers: `core/static/core/js/navigation.js`, `core/static/core/js/pipeline.js`
  - Tab Interfaces:
    - Queue Tab: `core/templates/core/tabs/tab_queue.html`
    - History Tab: `core/templates/core/tabs/tab_history.html`
    - ITMS Tab: `core/templates/core/tabs/tab_itms.html`
    - Stock Tab: `core/templates/core/tabs/tab_stock.html`
    - Batches Tab: `core/templates/core/tabs/tab_batches.html`
    - Reports Tab: `core/templates/core/tabs/tab_reports.html`

### 11.1 Problem Statement & Objectives
1. **Missing Shift Date Tool on Mobile**:
   - The desktop shift selector was hidden on mobile devices because `.desktop-badges` was hidden (`display: none !important`), leaving operators on phones unable to view or switch operating shifts from the persistent layout.
2. **Filter & Action Ribbon Screen Congestion**:
   - Complex tabs (Queue, History, ITMS, Stock, Batches, Reports) contained heavy search bars, multi-select dropdowns, bulk utility buttons, and export menus that stacked vertically on small viewports ($\le 768\text{px}$), pushing actionable records off-screen.
3. **Core Requirement**:
   - Provide a persistent, dedicated mobile shift date selector tool matching desktop capabilities.
   - Introduce a tactile mobile filter funnel toggle (`.mobile-filter-funnel-btn`) that collapses dense filters and utility ribbons into a smooth animated drawer (`.collapsible-filter-drawer`).
   - Keep primary subnavigations (status pills, source toggles, operational modes, and scope selectors) 100% visible for immediate 1-tap switching without requiring drawer interaction.
   - Maintain 100% visual and functional parity on desktop viewports ($> 768\text{px}$) with zero regressions.

### 11.2 Architecture & Implementation Details
1. **Mobile Operating Shift Date Selector Tool**:
   - Added `.mobile-shift-strip#mobile-shift-strip` pinned directly under `.header-main-row` in `core/templates/core/base.html` featuring `<select id="mobile-global-shift-select">` and a "Today" reset button.
   - Added duplicate `<select id="drawer-global-shift-select">` in the off-canvas hamburger navigation drawer for quick drawer access.
   - Extended `populateAllShiftDropdowns` and `syncAllDateSelectorsUI` in `core/static/core/js/pipeline.js` to synchronously bind and dispatch `shiftDateChanged` events across all 4 selectors (`#global-shift-select`, `#mobile-global-shift-select`, `#drawer-global-shift-select`, and `select.header-shift-select`).
2. **Universal Filter Funnel & Collapsible Drawer Engine**:
   - **Global Controller** (`navigation.js`):
     ```javascript
     window.toggleMobileFilterPanel = function(drawerId, btnElem) { ... }
     ```
     Toggles the `.is-open` CSS class, manages `aria-expanded="true/false"`, and updates active button state styles.
   - **CSS System** (`app_layout.css`):
     - **Desktop ($> 768\text{px}$)**: `.mobile-filter-funnel-btn` has `display: none !important;`. `.collapsible-filter-drawer` has `display: flex;` with zero extra background or border, preserving original desktop inline ribbon layouts.
     - **Mobile ($\le 768\text{px}$)**: `.mobile-filter-funnel-btn` displays as a 32px tactile pill button (`background: #141722`, border, icon, label). `.collapsible-filter-drawer` collapses by default (`display: none !important;`) and slides down smoothly (`@keyframes mobileFilterSlideDown`) when toggled open.
3. **Tab Migrations**:
   - **Queue (`tab_queue.html`)**: Filter pills (`All`, `Review`, `Approved`, `Issues`, `Done`) remain horizontally visible; search input, ingestion batch dropdown, and scope pills collapse into `#queue-filter-drawer`.
   - **History (`tab_history.html`)**: Filter chips (`All`, `Submitted`, `Failed`, `Issues`, `Stock`, `Emails`) remain visible as a horizontal scroll strip; live search and scope actions collapse into `#history-filter-drawer`.
   - **ITMS (`tab_itms.html`)**: Subview tabs (`Connect`, `Orders`, `Archive`, `Kits`) and live/local source toggle stay visible; sync actions collapse into `#itms-filter-drawer`.
   - **Stock (`tab_stock.html`)**: 4-mode operational buttons (`1. Dispatch`, `2. Movements`, `3. Safe Audit`, `4. Ledger`) remain horizontally scrollable; category select, universal barcode/plate scanner, quick delivery, paste, sync, and export CSVs collapse into `#stock-filter-drawer`.
   - **Batches (`tab_batches.html`)**: Batch scope pills (`Today's Batches`, `Active Batch`, `Prior Carryover`, `All Batches`) remain visible; search field, density switcher, expand/collapse utilities, and upload/camera buttons collapse into `#batches-filter-drawer`.
   - **Reports (`tab_reports.html`)**: Active shift badge and Scope toggle (`📅 Scope [D]`) remain visible; Refresh, Sync Shift Orders, Shift Reconciliation, Stock Kit Sync, and categorized CSV exports collapse into `#reports-filter-drawer`.



---

## 12. Senior Developer Code Review & Rectification Request (Shift Date Selectors & Desktop Toolbar Regressions)

### 12.1 Code Review Verdict & Architectural Findings
- **Status**: ⚠️ **Changes Required (Design & Layout Regressions Identified)**
- **Review Date**: Mon Oct 19, 2026
- **Auditor**: Senior Backend & Systems Architect

#### 1. Shift Date Selector Proliferation & Clutter
- **Finding**: The implementation created 3 separate `<select>` dropdowns for the operating shift date:
  1. Desktop header: `#global-shift-select` in `base.html` (inside `.header-controls.desktop-badges`, hidden on mobile $\le 992\text{px}$).
  2. Mobile header strip: `#mobile-global-shift-select` pinned beneath the header via `.mobile-shift-strip` on viewports $\le 992\text{px}$.
  3. Drawer: `#drawer-global-shift-select` in the off-canvas hamburger drawer.
- **Defects & Anti-Patterns**:
  - **Screen Real Estate Violation on Mobile**: The persistent `.mobile-shift-strip` takes up 44px of vertical height permanently pinned beneath the header on every single tab. On mobile screens (where viewport height is already constrained to 667–844px), consuming nearly 100px for headers pushes operational table rows off-screen, directly canceling out the benefits of collapsing secondary filters!
  - **Triplication of DOM & Synchronization**: Managing 3 DOM dropdowns and keeping their state in sync across `pipeline.js` (`populateAllShiftDropdowns`, `syncAllDateSelectorsUI`) introduces unnecessary fragility.
- **Architectural Decision**:
  - Remove the pinned `.mobile-shift-strip` from `base.html` and `app_layout.css`.
  - Place the primary shift date selector on the **Executive Dashboard** (`tab_dashboard.html`) at the top inside `.dash-shift-banner`, where shift telemetry and daily KPIs are managed.
  - On the desktop header, keep the single canonical `#global-shift-select`.
  - On the mobile hamburger drawer, retain `#drawer-global-shift-select` as a clean secondary access point without cluttering the main viewport.

#### 2. Filter Funnel Desktop Toolbar Regressions
- **Finding**: While `.mobile-filter-funnel-btn` is hidden on viewports $> 768\text{px}$ via `display: none !important;`, wrapping secondary tools inside `<div class="collapsible-filter-drawer" style="width:100%;">` caused desktop toolbars to split into **two stacked vertical rows**:
  - **History Tab (`tab_history.html`)**: Filter chips are on row 1; the search box was pushed into row 2. (Originally sat side-by-side on 1 row).
  - **Reports Tab (`tab_reports.html`)**: Active shift badge is on row 1; all 5 action buttons (Refresh, Sync, Reconcile, Kits, CSVs) were pushed into row 2.
  - **ITMS Tab (`tab_itms.html`)**: Source toggle is on row 1; sync actions were pushed into row 2.
  - **Queue Tab (`tab_queue.html`)**: Status pills are on row 1; search, batch, and scope pills were pushed into row 2.
  - **Stock Tab (`tab_stock.html`)**: Mode 1-4 buttons are on row 1; universal command bar was pushed into row 2.
- **Defects & Anti-Patterns**:
  - On desktop displays (1920×1080), horizontal screen space is abundant. Forcing toolbars into two stacked rows consumes extra vertical room and pushes data tables down on desktop.
  - In `app_layout.css:380`, `.collapsible-filter-drawer` on desktop only defines `display: flex;` with no flex direction or inline layout integration, resulting in layout breakage.
- **Architectural Decision**:
  - On desktop ($> 768\text{px}$), `.collapsible-filter-drawer` must render transparently / inline (`display: contents;` or inline flex flow) so all controls remain on **one single horizontal toolbar row** matching the original desktop design.
  - The collapsible drawer and funnel button behavior must be **strictly isolated to mobile viewports ($\le 768\text{px}$)**.

---

### 12.2 Action Items for Frontend Developer
1. **Remove `.mobile-shift-strip`**:
   - Delete `.mobile-shift-strip` from `core/templates/core/base.html`.
   - Remove its CSS rules from `core/static/core/css/app_layout.css`.
2. **Move Mobile Shift Selector to Dashboard Top Banner**:
   - Embed the shift selector control into `core/templates/core/tabs/tab_dashboard.html` inside `.dash-shift-banner` for mobile viewports.
   - Ensure the hamburger drawer selector (`#drawer-global-shift-select`) remains functional for off-canvas access.
3. **Restore Single-Row Desktop Toolbars**:
   - In `app_layout.css`, style `.collapsible-filter-drawer` for desktop ($> 768\text{px}$) with `display: contents;` (or flex inline) so child inputs, buttons, and badges flow seamlessly on the same row as the subnav pills without taking up two rows.
   - Verify all 6 tabs (Queue, History, ITMS, Stock, Batches, Reports) on desktop: toolbars must fit on 1 row with maximum vertical height reserved for tables.
4. **Preserve Valid Features**:
   - Keep Quick Install Modal (`tab_orders.html`, `tab_orders.js`) and Mobile Companion Mode 4 (`mobile_companion.html`, `mobile_companion.js`) intact—these components are verified and operational.

### 12.3 Resolution & Verification Summary
- **Status**: ✅ **RESOLVED & VERIFIED**
- **Date**: Mon Oct 19, 2026
- **Changes Applied**:
  1. **Shift Date Selector Architecture Rectified**:
     - Removed `.mobile-shift-strip` from `core/templates/core/base.html` and deleted its CSS rules from `core/static/core/css/app_layout.css`. Mobile viewports reclaim 44px of vertical screen real estate for operational data tables.
     - Added `#dash-global-shift-select` into `.dash-shift-banner` in `core/templates/core/tabs/tab_dashboard.html` alongside a "Today" quick-reset button. Operating shift date is cleanly controlled directly from the Executive Dashboard.
     - Retained canonical `#global-shift-select` on the desktop header and `#drawer-global-shift-select` in the off-canvas hamburger drawer.
     - Updated `pipeline.js` (`populateAllShiftDropdowns`, `syncAllDateSelectorsUI`) to synchronize all shift selectors seamlessly.
  2. **Desktop 1-Row Toolbars Restored**:
     - Configured `@media (min-width: 769px) { .collapsible-filter-drawer { display: contents !important; } ... }` in `app_layout.css`.
     - Removed inline `width:100%` overrides from `tab_history.html`, `tab_reports.html`, `tab_itms.html`, `tab_batches.html`, `tab_stock.html`, and `tab_queue.html`.
     - All 6 tabs now render on **1 single compact horizontal row** on desktop screens (> 768px), maximizing vertical table space while strictly isolating the collapsible drawer and funnel icon to mobile screens (≤ 768px).
  3. **Preserved Features**:
     - Quick Install Modal (`tab_orders.html`, `tab_orders.js`) and Mobile Companion Mode 4 (`mobile_companion.html`, `mobile_companion.js`) remain 100% operational.
  4. **Verification**:
     - Django check: `System check identified no issues (0 silenced)`.
     - Static collection: `6 static files copied to 'staticfiles', 163 unmodified`.
     - Automated test suite: `Ran 55 tests in 84.029s, OK (skipped=1)`.


---

## 13. Mobile Companion Blocked ("Not on Stock") Tray Sync & Persistence

- **Date**: Mon Oct 19, 2026
- **Status**: ✅ **RESOLVED & VERIFIED**

### 13.1 Backend Capabilities Delivered
1. **Two-Tier Live ITMS Lookup on Every Scan (`GET /api/stock/inspect/`)**:
   - Tier 1 checks local synced `InstallationKit` records.
   - Tier 2 automatically looks up missing plates on the live ITMS app (`/installation-kits/index`) and syncs them into the local DB if found (`synced_from_itms: true`, `is_blocked: false`).
   - If the kit does not exist on the ITMS app either, it marks `status_code: "NOT_ON_STOCK"`, `is_blocked: true` ("Set physical box aside!") **and automatically persists the plate on the server** in the `StockBlockedScan` database table + shift docket.
   - Response now includes `"blocked_plates": [...]`, `"blocked_count": N`, `"date_suffix": "..."`, and `"synced_from_itms": bool`.
2. **Two-Way Blocked Plates Endpoint (`GET / POST /api/stock/blocked-plates/`)**:
   - `GET /api/stock/blocked-plates/?date_suffix=<suffix>` returns `{"success": true, "date_suffix": "...", "blocked_plates": [...], "count": N, "docket": {...}}`.
   - `POST /api/stock/blocked-plates/` accepts `{"plates": [...], "date_suffix": "...", "operator_name": "...", "action": "add" | "clear"}` to merge or clear blocked plates across devices.

### 13.2 Frontend Integration Completed (`mobile_companion.js`, `tab_stock.js`, `mobile_companion.html`)
1. **Two-Way Blocked Plates Hydration & `localStorage` Persistence (`mobile_companion.js`)**:
   - `handleStockBarcodeDetected` merges `inspectData.blocked_plates` directly into `MobileState.stock.blockedPlates`, saves to `localStorage` (`itms_mobile_blocked_plates` + `itms_mobile_blocked_shift`), and renders the tray immediately.
   - Added `syncBlockedPlatesWithServer()` which pushes any existing phone-only blocked plates to `POST /api/stock/blocked-plates/` (or pulls via `GET /api/stock/blocked-plates/?date_suffix=...`), merges the server list, persists to `localStorage`, and refreshes `renderBlockedPlatesTray()`.
   - Enabled `check_itms_live: true` in `syncStockScansToServer()` so queued batch dispatches also execute the 2-tier lookup.
2. **Web Dashboard Fresh Sync (`tab_stock.js`)**:
   - Updated `openNotOnStockModal()` to always fetch fresh `/api/stock/blocked-plates/?date_suffix=${currentStockDateSuffix}` when opened so blocked kits scanned on the phone appear immediately on the web dashboard (`close.kirex.online`).


---

## 14. High-Speed 100% Reliable Stock Dispatch Scanning & Running Total Counter

- **Date**: Thu Oct 22, 2026
- **Status**: 🔄 **Backend Completed & Deployed / Companion Frontend Enhancements Specified**

### 14.1 Root Causes Resolved in Backend (`kit_provisioning_service.py`, `stock_monitoring_service.py`, `bond.py`)
1. **Spaced ITMS Registration Number Mismatch (`"UMA 970PY"` vs `"UMA970PY"`)**:
   - In the database, 99.9% of `InstallationKit` rows synced from ITMS store `registration_number` with a space (`"UMA 970PY"`). Previously, `verify_scanned_kits_stock`, `record_dispatch_scans`, `enrich_physical_plates_with_itms`, and `api_stock_shift_details` queried `registration_number__in=clean_plates` (canonical `"UMA970PY"` without space).
   - **Fix**: Added `get_kits_matching_plates_q()` and `build_kit_lookup_map()` matching canonical (`"UMA970PY"`), spaced display (`"UMA 970PY"`), and kit codes (`"IK-UMA970PY"`) across all Tier 1 lookups, status updates, and shift detail tables.
2. **Symmetric Immediate Persistence on `GET /api/stock/inspect/` for Mobile Companion**:
   - Previously, `GET /api/stock/inspect/` immediately persisted `NOT_ON_STOCK` scans into `StockBlockedScan`, but did NOT persist `ON_STOCK` scans into `StockDispatchScan`—relying on a secondary `POST /api/stock/dispatch/` 80ms later that could be dropped when `MobileState.stock.isSyncing` was true.
   - **Fix**: When `GET /api/stock/inspect/` verifies an `ON_STOCK` kit from Mobile Companion (`HTTP_REFERER` containing `/mobile` with `sub_mode=DISPATCH`, or `auto_dispatch=1`), it immediately stages the `StockDispatchScan` row (`notes="[AUTO_INSPECT_STAGED]"`) and updates `InstallationKit.status = "Dispatched"`. When `POST /api/stock/dispatch/` arrives 80ms later, `record_dispatch_scans` promotes the staged row and still counts it in `newly_dispatched`.
3. **Removed 3-Plate Live ITMS Cap for Dispatch & Batch Verification (`max_live_itms_queries=500`)**:
   - `record_dispatch_scans` and `api_stock_verify_batch` now pass `max_live_itms_queries=500` to `verify_scanned_kits_stock`, which uses a 6-worker `ThreadPoolExecutor` for parallel ITMS lookups and single-thread DB upserts.
4. **SQLite Lock Contention & Transaction Slimming**:
   - Moved `compute_daily_reconciliation(suffix)` outside the `StockDispatchScan.objects.bulk_create` atomic write transaction, added a 4-attempt SQLite `OperationalError` retry loop, and serialized background telematics enrichment with `_ENRICH_LOCK`.
5. **Live `dispatch_summary` Returned on Both `/api/stock/inspect/` and `/api/stock/dispatch/`**:
   - Both endpoints now return `"dispatch_summary": {"total": N, "psv": X, "pmo": Y}` and `"blocked_count": B` on every scan.

### 14.2 Companion Frontend Specifications for UI/UX Developer (`mobile_companion.js` & `tab_stock.js`)
1. **Re-Trigger Queue Sync in `finally` Block (`core/static/core/js/mobile_companion.js:1515`)**:
   - In `syncStockScansToServer()`, if new plates were scanned while `MobileState.stock.isSyncing` was `true`, they remain in `MobileState.stock.queue`.
   - In the `finally` block of `syncStockScansToServer()`, add:
     ```javascript
     if (MobileState.stock.autoSync && MobileState.stock.queue.length > 0) {
         setTimeout(() => { void syncStockScansToServer(); }, 60);
     }
     ```
2. **Pass `sub_mode` Parameter on Mobile Inspect (`core/static/core/js/mobile_companion.js:1112`)**:
   - Include `&sub_mode=${encodeURIComponent(MobileState.stock.subMode)}` in the `fetch('/api/stock/inspect/?...')` URL so `GET /api/stock/inspect/` knows whether the phone is in `DISPATCH`, `DELIVERY`, `RETURN`, or `AUDIT` mode.
3. **Display Live Server Dispatched Total Counter (`mobile_companion.js` & `tab_stock.js`)**:
   - Bind `data.dispatch_summary.total` (returned by both `/api/stock/inspect/` and `/api/stock/dispatch/`) to the Dispatched counter badge so operators see the exact database-confirmed count of kits dispatched to the line (`300 Dispatched • 10 Set Aside`) in real time after every scan.


---

## 15. Feature: Mobile Companion Off-Conveyor (Mode 2: U-Turn Yard Walk) Session Resilience & Recovery - [COMPLETED]

- **Date**: Thu Oct 22, 2026
- **Status**: 🟢 **Completed & Verified**

### 15.1 Summary of Changes Implemented in Companion (`mobile_companion.js` & `mobile_companion.html`)
1. **Persistent Session State (`localStorage['itms_uturn_session_state']`)**:
   - `saveUturnSessionState()` records the active walk state on every capture, undo, or phase switch:
     - `walkSessionId`: Dedicated session UUID ensuring consistent batch affinity across reloads.
     - `batchIndex`: Current yard walk batch sequence index.
     - `phase`: Active walk leg (`"REAR"` vs `"FRONT"`).
     - `rearCount`, `frontCount`, `totalPhotos`: Accurate real-time photo counts.
     - `recentTiles`: Metadata list of recent photos for inspection and undo.
2. **Seamless Session Restoration on Page Reload (`loadUturnSessionFromStorage`)**:
   - Automatically executed on `DOMContentLoaded`.
   - Restores the active `walkSessionId`, counters, and active phase (`FRONT` or `REAR`).
   - Automatically re-activates the correct shutter button color and label (`Capture FRONT` vs `Capture REAR`), guidance bar, road steps, return match progress bar, and recent capture tiles.
   - Re-checks and verifies photo counts against the backend via `/api/mobile/uturn/session_status/?session_id=...`.
3. **Explicit Walk Discard / Abort Action (`abortUturnBatch`)**:
   - Added `[ ✕ Abort Walk ]` red action button (`btn-abort-uturn`) alongside Complete Batch.
   - Prompts confirmation: *"Discard this U-Turn walk (Batch #N with X photos)?"*.
   - Sends `POST /api/mobile/uturn/abort_session/` to server to delete unsubmitted photos, clears outbox queue in IndexedDB, removes local session state, and cleanly resets UI without generating incomplete pairs.
4. **Outbox Synchronization & Completion Guard (`finishUturnBatch`)**:
   - Checks if any photos for the session are still uploading in IndexedDB; drains outbox queue before attempting batch completion.
   - **Blocks Single-Orientation Completion**: If only REAR photos (0 FRONTS) were captured, completion is blocked with a clear warning instructing the operator to turn around and capture fronts.
   - **Count Asymmetry Confirmation**: If `rears != fronts`, prompts explicit confirmation detailing how many pairs will match and how many surplus will remain.
   - **Safe State Clearance**: Only advances `batchIndex` and resets counters AFTER the server returns `{"success": true}` from `POST /api/mobile/uturn/finish_batch/`.


---

## 16. Feature: Truthful Batch Submission, Archive Verification UI & Concurrency Lock Handling - [COMPLETED]

- **Date**: Sun Oct 25, 2026
- **Status**: 🟢 **Completed & Verified**
- **Priority**: High (Eliminates the ~3% Silent Upload Drop & Concurrent Session Collisions)

### 16.1 Problem Statement
1. **The "Silent 3% Drop"**: When a batch of 25 pairs has 1 pair with an OCR discrepancy (in `CONFLICT`) or network drop, line 429 of `pipeline.js` displays:
   `"All pairs processed! 24 succeeded, 0 failed. Batch Complete (24 / 24)"`.
   Operators believe 100% of the physical batch was pushed to ITMS, while the unapproved or failed pair is silently skipped or moved into `OFFLINE_OUTBOX`.
2. **Concurrent Submissions**: If two operators or tabs click "Submit Approved", overlapping requests fire against the ITMS PHP upload buffer, causing timeouts and corrupted multipart streams.
3. **Missing Archive Feedback**: The UI currently considers an HTTP 302 as success without reflecting Step 4 Post-Submission Archive confirmation.

---

### 16.2 Backend REST Contracts Implemented & Available

#### 1. Concurrency Lock Status (`GET /api/stats/` & `GET /api/pipeline/status/`)
- In `api_stats`, the backend now returns real-time lock metadata:
  ```json
  {
    "submission_locked": true,
    "submission_lock_info": {
      "pid": 1420,
      "pair_id": 3206,
      "order_number": "PO-UMA391QL-231026",
      "registration_number": "UMA 391QL",
      "operator": "john_doe",
      "started_at": 1792879500.0,
      "elapsed_seconds": 4.2
    }
  }
  ```
- When `submission_locked == true`, another submission is currently in progress.

#### 2. Mutual Exclusion Conflict Response (`HTTP 409 Conflict`)
- Both `POST /api/pairs/batch-submit/` and `POST /api/pairs/<id>/action/` (action=`submit`) return `409 Conflict` if a submission is already active:
  ```json
  {
    "success": false,
    "code": "SUBMISSION_IN_PROGRESS",
    "message": "Another submission is currently in progress (UMA 391QL by john_doe). Please wait for it to complete.",
    "lock_info": { ... }
  }
  ```

#### 3. Enhanced Batch Submit Response (`POST /api/pairs/batch-submit/`)
- The response now provides a truthful breakdown:
  ```json
  {
    "success": true,
    "total": 24,
    "succeeded": 23,
    "failed": 1,
    "outbox_queued": 1,
    "failures": [
      {
        "pair_id": 3105,
        "plate": "UMA 654QJ",
        "order_number": "PO-UMA654QJ-231026",
        "error": "Step 3 submitted, but order was not confirmed in ITMS Archive within 6.0s.",
        "queued_outbox": false
      }
    ],
    "message": "Batch submission complete (TODAY): 23 succeeded, 1 failed (Outbox: 1)."
  }
  ```

---

### 16.3 Frontend Requirements & UI Integration Specifications

#### A. Batch Submit Button & Active Lock State (`pipeline.js` & `tab_batches.js`)
1. **Poll Lock State**: In `fetchStats()` (`app.js`), if `data.submission_locked === true`:
   - Disable all batch submit buttons: `#btn-submit-batch`, `#btn-submit-approved`.
   - Show an active lock badge / pulse indicator in the header ribbon or queue bar:
     `"🔒 Submitting UMA 391QL (4s)..."`
   - Prevent operators from double-clicking or triggering simultaneous submissions.
2. **Handle HTTP 409**:
   - If `POST /api/pairs/batch-submit/` or `POST /api/pairs/<id>/action/` returns HTTP 409:
   - Display a non-blocking toast warning using the returned `data.message`.
   - Do NOT mark the pair as failed; simply restore the button state.

#### B. Pre-Submission Physical Discrepancy Banner (`pipeline.js:triggerBatchSubmit`)
1. **Pre-Flight Count Inspection**:
   - Before firing batch submit, compare total pairs in the active batch vs. approved pairs:
     - `total_in_batch = pairsData.length`
     - `approved_in_batch = pairsData.filter(p => p.verification_status === "APPROVED").length`
     - `unapproved_count = total_in_batch - approved_in_batch`
2. **Warning Confirmation**:
   - If `unapproved_count > 0`:
     Display an explicit confirmation modal or banner:
     ```html
     <div class="alert alert-warning mb-3">
       <strong>⚠️ Partial Batch Submission:</strong> 
       Only <strong>${approved_in_batch}</strong> of <strong>${total_in_batch}</strong> pairs in this batch are APPROVED.
       <br>
       <strong>${unapproved_count}</strong> pair(s) in CONFLICT / PENDING REVIEW will NOT be sent to ITMS until reviewed.
     </div>
     ```
   - Give the operator two buttons: `[ Review Conflicts First ]` and `[ Submit Approved Only (${approved_in_batch}) ]`.

#### C. Truthful Batch Completion Modal (`pipeline.js:updateBatchModal`)
1. **Replace the misleading line 429**:
   - Instead of blindly reporting `"All pairs processed! X succeeded, 0 failed. Batch Complete"`:
   - Read the server response `data.succeeded`, `data.failed`, `data.outbox_queued`, and `data.failures`.
2. **Display Color-Coded Breakdown**:
   - If `failed > 0` or `outbox_queued > 0`:
     - Show **amber/red** header: `"⚠️ Batch Completed with Exceptions: ${succeeded} Succeeded, ${failed} Failed"`.
     - List each failed plate with its exact error reason (e.g., *"Not confirmed in ITMS Archive"*, *"Offline Outbox"*).
     - Add a direct button: `[ 🔍 Jump to Failed Pairs in Queue ]` to immediately filter the queue to those specific pair IDs.
   - Only show green `"100% Batch Complete"` when `failed === 0` AND all pairs in the batch were approved and archived.


---

## 17. Feature: Robust Quick Install, Serial-to-UUID Resolution, Fresh Form CSRF & Hardware Carry-Through - [COMPLETED]

- **Date**: Sun Oct 25, 2026
- **Status**: 🟢 **Completed & Verified (Backend Services, REST APIs, Desktop & Mobile UI)**
- **Priority**: High (Eliminates Quick Install Validation Drops & Form Submission Rejections)

### 17.1 Problem Statement & Root Cause Resolution
1. **Serial String vs. ActiveForm UUID Mismatch**:
   - In the ITMS Yii2 web application, hardware selection dropdowns expect a 36-character inventory UUID (e.g., `a90e3bf1-5176-422e-9e3f-ac8235e2d243`).
   - When operators input human-readable serials (`001182812`, `8BAE470808CF`), the ITMS server returned:
     `Form validation rejected by ITMS: {'installationorderform-front_license_plate_id': ['Front License Plate is not a valid UUID']}`.
   - **Resolution**: Implemented `ITMSWebClient.resolve_option_uuid()` which automatically matches serial numbers, plate numbers, and leading-zero variations against the parsed ITMS dropdown options, resolving to valid UUIDs automatically.
2. **Missing Fresh Form CSRF Token**:
   - Calling `submit_installation_step1` previously relied on stale login session CSRF cookies instead of the fresh page token rendered on `/installation-orders/installation?id=<uuid>`.
   - **Resolution**: Backend now automatically fetches Step 1 form HTML, extracts fresh `_csrf-frontend` tokens, and passes them to ActiveForm validation and POST submissions.
3. **Hardware Serials Discarded on 1-Click Direct Submit (`action="submit"`)**:
   - When operators clicked "1-Tap Finalize & Submit", `orders.py` attached photos and immediately dispatched to `submit_pair`, discarding the hardware serial inputs and leaving Step 1 blank on ITMS.
   - **Resolution**: The backend now persists hardware inputs to the `InstallationOrder` record, passes `front_plate_id`, `rear_plate_id`, and `tracker_id` into `submission_worker.submit_pair()`, which forwards them into `execute_installation_order_workflow()` to complete Step 1 before uploading Step 2 photos.
4. **Empty Hardware Pre-fill on New Orders**:
   - **Resolution**: `GET /api/itms/orders/<ident>/install/` now automatically resolves provisioned kit inventory from `InstallationKit` and ITMS Step 1 dropdown selections to pre-populate blank serials.

---

### 17.2 Backend REST Contracts Implemented & Available

#### 1. Hardware Inspection & Step 1 Form Data (`GET /api/itms/orders/<ident>/install/`)
The endpoint now returns pre-filled order serials and the complete `step1_form` dropdown structure:
```json
{
  "success": true,
  "order": {
    "order_number": "PO-UMA300P-100926",
    "registration_number": "UMA 300P",
    "status": "PENDING",
    "vin": "VIN1234567890",
    "warehouse_name": "AGM Bond",
    "itms_uuid": "a90e3bf1-5176-422e-9e3f-ac8235e2d243",
    "front_plate_serial": "001182812",
    "rear_plate_serial": "001182813",
    "gps_tracker_id": "8BAE470808CF"
  },
  "stage": "STAGE_1_INSTALLATION",
  "stage_info": { "stage": "STAGE_1_INSTALLATION", "order_uuid": "a90e3bf1-5176-422e-9e3f-ac8235e2d243" },
  "step1_form": {
    "csrf_token": "fresh-token-xyz-789",
    "front_plate": {
      "selected_id": "a90e3bf1-5176-422e-9e3f-ac8235e2d243",
      "selected_text": "001182812 (UMA 300P)",
      "options": [
        {
          "value": "a90e3bf1-5176-422e-9e3f-ac8235e2d243",
          "text": "001182812 (UMA 300P)",
          "serial": "001182812",
          "selected": true
        }
      ]
    },
    "rear_plate": {
      "selected_id": "bbbbbbbb-2222-3333-4444-555555555555",
      "selected_text": "001182813 (UMA 300P)",
      "options": [
        {
          "value": "bbbbbbbb-2222-3333-4444-555555555555",
          "text": "001182813 (UMA 300P)",
          "serial": "001182813",
          "selected": true
        }
      ]
    },
    "tracker": {
      "selected_id": "cccccccc-3333-4444-5555-666666666666",
      "selected_text": "8BAE470808CF",
      "options": [
        {
          "value": "cccccccc-3333-4444-5555-666666666666",
          "text": "8BAE470808CF",
          "serial": "8BAE470808CF",
          "selected": true
        }
      ]
    }
  },
  "existing_pair": null
}
```

#### 2. Save Step 1 (`POST /api/itms/orders/<ident>/install/`)
Payload:
- `action`: `"save_step1"` (or `"save_continue"`)
- `front_plate_id`: Human-readable serial `001182812` OR option UUID
- `rear_plate_id`: Human-readable serial `001182813` OR option UUID
- `tracker_id`: IMEI/tracker serial `8BAE470808CF` OR option UUID
- `dry_run`: `"true"` or `"false"` (default: config setting)

Response:
```json
{
  "success": true,
  "stage": "STAGE_2_EVIDENCE",
  "message": "Step 1 saved successfully. Order is ready for photo upload.",
  "details": { "success": true, "step": 1, "status_code": 302, "redirect_url": "..." }
}
```

#### 3. 1-Tap Finalize & Submit (`POST /api/itms/orders/<ident>/install/`)
Payload:
- `action`: `"submit"`
- `front_photo`: File
- `rear_photo`: File
- `front_plate_id`: Serial or option UUID (optional override)
- `rear_plate_id`: Serial or option UUID (optional override)
- `tracker_id`: Tracker serial or option UUID (optional override)
- `dry_run`: `"true"` or `"false"`

Response:
```json
{
  "success": true,
  "message": "Order #PO-UMA300P-100926 (UMA 300P) submitted successfully to ITMS!",
  "is_finalized": true,
  "dry_run": false,
  "order_number": "PO-UMA300P-100926",
  "registration_number": "UMA 300P",
  "pair_id": 3412,
  "token": "a90e3bf1-5176-422e-9e3f-ac8235e2d243"
}
```

---

### 17.3 Frontend UI Integration Guidance (`tab_orders.js` & `mobile_companion.js`)

1. **Auto-Populate Input Fields on Load**:
   - In `openOrderInstallModal()` (`tab_orders.js`) and `lookupMobileOrder()` (`mobile_companion.js`):
     - Check `data.order.front_plate_serial`, `data.order.rear_plate_serial`, and `data.order.gps_tracker_id`.
     - If non-empty, auto-populate the respective input values:
       ```javascript
       if (data.order?.front_plate_serial) {
           frontSerialInput.value = data.order.front_plate_serial;
       }
       if (data.order?.rear_plate_serial) {
           rearSerialInput.value = data.order.rear_plate_serial;
       }
       if (data.order?.gps_tracker_id) {
           gpsIdInput.value = data.order.gps_tracker_id;
       }
       ```
2. **Support Native `<datalist>` Auto-Suggest / Dropdown Selection**:
   - If `data.step1_form` is provided and contains `options` arrays:
     - Populate `<datalist id="order-install-front-options">` and `<datalist id="order-install-rear-options">` so operators can select matching kits directly or view provisioned hardware without typing.
3. **Zero Breaking Changes for Operators**:
   - Operators can continue typing serial numbers or scanning barcodes directly; the backend handles UUID resolution and fresh CSRF verification transparently.

---

### 17.4 Summary of Frontend Changes Implemented

1. **Desktop Native Datalists & Auto-Population (`tab_orders.js` & `tabs/tab_orders.html`)**:
   - Added native `<datalist id="order-install-front-options">`, `<datalist id="order-install-rear-options">`, and `<datalist id="order-install-gps-options">` linked to hardware inputs via `list` attributes.
   - Implemented `populateHardwareDatalist()` in `tab_orders.js` to dynamically inject matching serial numbers, UUIDs, and plate labels parsed from ITMS ActiveForm dropdowns.
   - Auto-populates inputs from `data.order` serials with fallback to `data.step1_form` selected hardware.

2. **Mobile Companion Datalists & Auto-Population (`mobile_companion.js` & `mobile_companion.html`)**:
   - Added native `<datalist id="m-order-front-options">`, `<datalist id="m-order-rear-options">`, and `<datalist id="m-order-gps-options">` to the mobile companion order fitment view.
   - Implemented `populateMobileHardwareDatalist()` in `mobile_companion.js` to inject ITMS inventory suggestions on order lookup.
   - Updated `lookupMobileOrder()` to pre-fill front plate, rear plate, and GPS tracker IDs with fallback to `step1_form.selected_text`.

3. **Cache Invalidation**:
   - Bumped asset cache versions to `?v=22` in `core/templates/core/base.html` and `core/templates/core/mobile_companion.html`.


---

## 18. Feature: Heartbeat Cadence Optimization & Upstream Reachability Caching - [COMPLETED]

- **Date**: Sun Oct 25, 2026
- **Status**: 🟢 **Completed & Verified (Backend Services, REST APIs & Desktop Frontend)**
- **Priority**: Medium / Performance (Eliminates 96% of Periodic Upstream ITMS Pings)

### 18.1 Problem Statement
- Previously, every 25 seconds, open browser tabs (`app.js:17` via `setInterval(fetchItmsStatus, 25000)`) called `/api/itms/status/`.
- On the backend, `auth_service.get_itms_status()` called `client.test_connection()`, which fired an un-cached HTTP GET request directly to `https://stock.itms.ug/site/login`.
- If 5 technicians had the web application open on laptops and mobile scanners simultaneously, the system sent **12 requests every minute** directly to the ITMS server just for the idle heartbeat badge.

---

### 18.2 Backend REST Contracts & Caching Architecture Implemented

#### 1. In-Memory Reachability Cache (`ITMSWebClient._connection_cache`)
- Reachability checks to `stock.itms.ug` are now cached across all requests and tabs with a **120-second (2-minute) cooldown window**.
- Multiple browser tabs and mobile scanners polling `/api/itms/status/` are served instantly (< 1ms response time) from local process memory without firing upstream HTTP requests.
- **Traffic Reduction**: Upstream heartbeat traffic dropped from 12+ requests/minute down to **0.5 requests/minute** (at most 1 request every 2 minutes across the entire fleet of operators).
- When the **ITMS Master Switch is PAUSED**, `test_connection()` returns immediately with `paused: true, cached: true`, firing 0 upstream network calls.

#### 2. Enhanced Heartbeat Response (`GET /api/itms/status/`)
```json
{
  "success": true,
  "status": {
    "url": "https://stock.itms.ug",
    "online": true,
    "online_error": "",
    "authenticated": true,
    "user_email": "operator@kirex.ug",
    "user_uuid": "c3f8709e-7bd1-4f11-9f6b-76fca55490bf",
    "expires_in_days": 28.5,
    "last_status_message": "Session active and authenticated.",
    "cached": true,
    "cache_age_seconds": 18.4,
    "latency_ms": 145.2
  },
  "active_bond": { "code": "AGM", "name": "AGM Bond Warehouse" }
}
```

#### 3. Forced Live Probe Parameter (`GET /api/itms/status/?force=1`)
- When an operator explicitly clicks **"Test Connection"** or **"Sign In"**, the frontend can pass `?force=1` to immediately bypass the 120s cache and probe upstream reachability in real time.

---

### 18.3 Frontend UI Integration Guidance (`app.js`, `tab_itms.js`)

1. **Adjust Polling Interval in `app.js:17`**:
   - Change:
     ```javascript
     setInterval(fetchItmsStatus, 25000); // 25s
     ```
   - To:
     ```javascript
     setInterval(fetchItmsStatus, 60000); // 60s
     ```
2. **Page Visibility Awareness (Battery & Network Friendly)**:
   - When an operator switches to another tab or locks their phone screen, avoid running heartbeat timers while hidden:
     ```javascript
     let itmsHeartbeatTimer = null;

     function startItmsHeartbeat() {
         if (!itmsHeartbeatTimer) {
             itmsHeartbeatTimer = setInterval(fetchItmsStatus, 60000);
         }
     }

     function stopItmsHeartbeat() {
         if (itmsHeartbeatTimer) {
             clearInterval(itmsHeartbeatTimer);
             itmsHeartbeatTimer = null;
         }
     }

     document.addEventListener("visibilitychange", () => {
         if (document.visibilityState === "visible") {
             fetchItmsStatus();
             startItmsHeartbeat();
         } else {
             stopItmsHeartbeat();
         }
     });

     startItmsHeartbeat();
     ```
3. **Manual Probe Bypass in Modals**:
   - In `tab_itms.js` or `tab_settings.js` when triggering a manual "Test Connection" button click:
     ```javascript
     const res = await fetch("/api/itms/status/?force=1");
     ```

---

### 18.4 Summary of Frontend Implementation

1. **Adjusted Cadence to 60s (`app.js`)**:
   - Replaced aggressive 25-second `setInterval(fetchItmsStatus, 25000)` with a 60-second heartbeat interval.
2. **Page Visibility Awareness (`app.js`)**:
   - Added `visibilitychange` listener that stops the heartbeat timer when the operator minimizes or navigates away from the browser tab, preventing background battery and network drain.
   - Automatically executes an immediate refresh and resumes the 60s timer when the tab returns to `visible`.
3. **Manual Probe Bypass Support (`tab_itms.js` & `tab_itms.html`)**:
   - Updated `fetchItmsStatus(force = false)` to support `?force=1` for manual actions.
   - Wired `loadOperatingBonds(true)` to pass `force=1` when clicking the "Test Connection" button.
   - Ensured account connect and disconnect actions bypass the reachability cache with immediate live verification.
4. **Cache Invalidation**:
   - Bumped assets to `?v=23` in `core/templates/core/base.html` and `core/templates/core/mobile_companion.html`.

     ```


## 19. Settings Cleanup, Desktop Navigation Header Simplification & Order Sync Cadence (27 October 2026) - [COMPLETED]

### 19.1 Context & User Specifications
Operators requested three specific interface improvements:
1. **Settings Page Cleanup**: Under the settings page where controls reside, remove the warning/card telling operators to "refer to TUI" and listing CLI commands (`itms --tui`).
2. **Desktop Navigation Header**: Remove the date selector from the top desktop navigation header to maintain clean, unencumbered top-level branding, while preserving the mobile off-canvas drawer and dashboard date selectors.
3. **ITMS Order Sync Cadence**: Expose the background active installation orders synchronization cadence directly in the Web UI Settings tab under Synchronization.

### 19.2 Implemented Changes
1. **Server Administration TUI Exclusive Card Removal (`core/templates/core/tabs/tab_settings.html`)**:
   - Removed the `Server Administration Exclusive Card` that displayed `TUI Console Exclusive` and instructed operators to run `itms --tui`.
   - Updated the Evidence Storage & Retention row badge from `Managed via TUI Console` to `● Automated`.
2. **Desktop Navigation Date Selector Removal (`core/templates/core/base.html`)**:
   - Removed `<div class="header-shift-control">` and `#global-shift-select` from the desktop header ribbon.
   - Retained the mobile off-canvas drawer shift selector (`#drawer-global-shift-select`) and Dashboard shift selectors, ensuring JavaScript `querySelectorAll` gracefully adapts without errors.
3. **ITMS Order Sync Cadence Input (`core/templates/core/tabs/tab_settings.html`)**:
   - Added an interactive input `#settings-order-sync-interval-input` under the Synchronization card.
   - Wired with `onchange="updateSetting('sync.order_sync_interval_seconds', parseInt(this.value, 10))"`, supporting intervals from 30s to 86400s (default: 600s).


## 20. Queue Review Studio & Replace Image Modal UI Refinements (02 November 2026) - [COMPLETED]

### 20.1 Overview & Requirements
Operators identified usability bottlenecks during queue review and photo batch candidate replacement:
1. **Replace Image Candidate Grid**:
   - In `#modal-replace-image` ("Pick from Batch" tab), candidate thumbnails are too small (previously 5–6 items crammed per row with 95px height), making it difficult to discern subtle plate details.
   - **Requirement**: Display a **maximum of three (3) images per row** with increased container height (160px–180px) and proper aspect ratio preservation (`object-fit: contain`).
2. **Queue Review / Inspection Studio Sticky Header**:
   - When inspecting high-resolution evidence in `.inspection-studio-pane` (especially on mobile/tablets or vertical scrolling on laptops), operators have to scroll extensively to reach the "◀ Back to Queue" or modal close buttons.
   - **Requirement**: A **fixed/sticky top header** (`position: sticky; top: 0; z-index: 100`) so the navigation and Back action remain visible at all times.
3. **Hardware & Order Details Card at Top of Inspection Studio**:
   - Add a prominent, structured telematics & hardware specification card at the top of the Queue inspection pane (directly below the sticky banner) showing:
     - **Order Details**: Service Type (`First Time Registration`), Vehicle VIN/Chassis No., Old Plate Number (`—`).
     - **Plate Details**: Registration Number, Front Plate Serial, Rear Plate Serial.
     - **Tracker Details**: GPS Tracker ID, Front Beacon ID/MAC, Rear Beacon ID/MAC.

---

### 20.2 Backend Implementation Summary (Completed)
The backend REST APIs `/api/pairs/<pair_id>/` and `/api/pairs/` have been updated in `core/views/pairs.py` to supply full hardware and telematics context:
- Enriched `data.pair.order` with `service_type`, `vin`, `old_registration_number`, `front_plate_serial`, `rear_plate_serial`, `gps_tracker_id`, `front_beacon_id`, and `rear_beacon_id`.
- Added `data.pair.hardware` convenience block returning normalized string values with fallback to `InstallationKit` if order hardware fields are blank or unlinked.
- Added `data.pair.kit` convenience block returning associated kit telematics (`front_plate`, `rear_plate`, `gps_tracker`, `front_tracker`, `rear_tracker`, `warehouse`, `status`).

**Example API Response (`GET /api/pairs/<pair_id>/`)**:
```json
{
  "success": true,
  "pair": {
    "id": 142,
    "registration_number_detected": "UMA 959GH",
    "hardware": {
      "service_type": "First Time Registration",
      "vin": "LB5DJ3R23SZ835915",
      "old_registration_number": "—",
      "registration_number": "UMA 959GH",
      "front_plate": "000389975",
      "rear_plate": "000389976",
      "gps_tracker": "8BAE470304CC",
      "front_beacon": "8ADC470655DF",
      "rear_beacon": "8ADC4706645F"
    },
    "kit": {
      "id": 84,
      "kit_code": "IK-UMA959GH",
      "front_plate": "000389975",
      "rear_plate": "000389976",
      "gps_tracker": "8BAE470304CC",
      "front_tracker": "8ADC470655DF",
      "rear_tracker": "8ADC4706645F",
      "warehouse": "AGM Bonded Warehouse",
      "status": "Assigned"
    }
  }
}
```

---

### 20.3 Frontend UI Integration Specifications

#### 1. Replace Image Candidate Grid (`core/templates/core/modals.html` & `core/static/core/css/tab_queue.css`)
- **Target Container**: `#replace-candidates-grid`
- **Target Styles**:
```css
/* Ensure maximum 3 cards per row with readable thumbnail height */
#replace-candidates-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
  gap: 14px !important;
  max-height: 480px;
  overflow-y: auto;
  padding: 8px 4px;
}

@media (max-width: 768px) {
  #replace-candidates-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}

.replace-candidate-card {
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  background: #0a0d14;
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.18s ease, transform 0.15s ease, box-shadow 0.18s ease;
}

.replace-candidate-card:hover {
  border-color: var(--ug-yellow, #f59e0b);
  transform: translateY(-2px);
}

.replace-candidate-card.selected {
  border-color: var(--ug-yellow, #f59e0b);
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.4);
}

.replace-candidate-img {
  width: 100%;
  height: 160px;
  object-fit: contain;
  background: #05070a;
  display: block;
}

.replace-candidate-meta {
  padding: 6px 10px;
  font-size: 11px;
  color: var(--ug-text-secondary, #94a3b8);
  display: flex;
  justify-content: space-between;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
```

#### 2. Fixed Sticky Top Header in Queue Inspection Studio (`core/static/core/css/tab_queue.css`)
- **Target Selector**: `.inspection-studio-pane .detail-banner-card`
- **Target Styles**:
```css
/* Sticky top bar keeps Back/Close and pair summary permanently pinned */
.inspection-studio-pane .detail-banner-card {
  position: sticky !important;
  top: 0 !important;
  z-index: 120 !important;
  background: var(--ug-black-card, #10141d) !important;
  border-bottom: 2px solid var(--ug-yellow, #f59e0b) !important;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5) !important;
  margin-bottom: 16px !important;
  padding: 12px 18px !important;
}

/* Ensure studio scrollable viewport allows sticky children */
.inspection-studio-pane {
  overflow-y: auto;
  position: relative;
}
```

#### 3. Top Hardware & Telematics Details Card (`core/templates/core/tabs/tab_queue.html` & `core/static/core/js/tab_queue.js`)
- **HTML Template Structure** (Insert directly below `.detail-banner-card` inside `.inspection-studio-pane`):
```html
<div class="card hardware-telematics-card mb-3" id="queue-hardware-card" style="display: none; background: #0c1017; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;">
  <div class="card-body p-3">
    <div class="row g-3">
      <!-- Column 1: Order Details -->
      <div class="col-md-4 border-end border-secondary border-opacity-25">
        <h6 class="text-uppercase text-muted fw-bold mb-2" style="font-size: 11px; letter-spacing: 0.5px; color: var(--ug-yellow, #f59e0b) !important;">
          📋 Order Details
        </h6>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">Service Type:</span>
          <span class="fw-semibold small text-light" id="hw-service-type">First Time Registration</span>
        </div>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">Vehicle VIN/Chassis:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-vin">—</span>
        </div>
        <div class="d-flex justify-content-between">
          <span class="text-muted small">Old Plate Number:</span>
          <span class="fw-semibold small text-light" id="hw-old-plate">—</span>
        </div>
      </div>

      <!-- Column 2: Plate Details -->
      <div class="col-md-4 border-end border-secondary border-opacity-25">
        <h6 class="text-uppercase text-muted fw-bold mb-2" style="font-size: 11px; letter-spacing: 0.5px; color: var(--ug-yellow, #f59e0b) !important;">
          🏷️ Plate Details
        </h6>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">Registration Number:</span>
          <span class="fw-bold small text-warning" id="hw-reg-num">—</span>
        </div>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">Front Plate:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-front-plate">—</span>
        </div>
        <div class="d-flex justify-content-between">
          <span class="text-muted small">Rear Plate:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-rear-plate">—</span>
        </div>
      </div>

      <!-- Column 3: Tracker Details -->
      <div class="col-md-4">
        <h6 class="text-uppercase text-muted fw-bold mb-2" style="font-size: 11px; letter-spacing: 0.5px; color: var(--ug-yellow, #f59e0b) !important;">
          📡 Tracker Details
        </h6>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">GPS Tracker:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-gps-tracker">—</span>
        </div>
        <div class="mb-1 d-flex justify-content-between">
          <span class="text-muted small">Front Beacon:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-front-beacon">—</span>
        </div>
        <div class="d-flex justify-content-between">
          <span class="text-muted small">Rear Beacon:</span>
          <span class="fw-semibold small font-monospace text-light" id="hw-rear-beacon">—</span>
        </div>
      </div>
    </div>
  </div>
</div>
```

- **JavaScript Data Binding (`core/static/core/js/tab_queue.js`)**:
In `renderPairDetail(pair)` / `loadPairDetail(pairId)`:
```javascript
const hwCard = document.getElementById('queue-hardware-card');
if (hwCard) {
  const hw = pair.hardware || (pair.order ? {
    service_type: pair.order.service_type,
    vin: pair.order.vin,
    old_registration_number: pair.order.old_registration_number,
    registration_number: pair.order.registration_number,
    front_plate: pair.order.front_plate_serial,
    rear_plate: pair.order.rear_plate_serial,
    gps_tracker: pair.order.gps_tracker_id,
    front_beacon: pair.order.front_beacon_id,
    rear_beacon: pair.order.rear_beacon_id,
  } : {});

  document.getElementById('hw-service-type').textContent = hw.service_type || 'First Time Registration';
  document.getElementById('hw-vin').textContent = hw.vin || '—';
  document.getElementById('hw-old-plate').textContent = hw.old_registration_number || '—';
  document.getElementById('hw-reg-num').textContent = hw.registration_number || pair.registration_number_detected || '—';
  document.getElementById('hw-front-plate').textContent = hw.front_plate || (pair.kit && pair.kit.front_plate) || '—';
  document.getElementById('hw-rear-plate').textContent = hw.rear_plate || (pair.kit && pair.kit.rear_plate) || '—';
  document.getElementById('hw-gps-tracker').textContent = hw.gps_tracker || (pair.kit && pair.kit.gps_tracker) || '—';
  document.getElementById('hw-front-beacon').textContent = hw.front_beacon || (pair.kit && pair.kit.front_tracker) || '—';
  document.getElementById('hw-rear-beacon').textContent = hw.rear_beacon || (pair.kit && pair.kit.rear_tracker) || '—';

  hwCard.style.display = 'block';
}
```


### 20.4 Implementation & Verification Summary (Completed)
- **Queue Studio Sticky Header**: Configured \.detail-banner-card\ in \core/static/core/css/tab_queue.css\ with \position: sticky; top: 0; z-index: 120; border-bottom: 2px solid var(--ug-yellow); box-shadow: 0 4px 14px rgba(0,0,0,0.45);\ across desktop and mobile viewports.
- **Hardware Telematics Top Card**: Integrated \#queue-hardware-card\ directly below \.detail-banner-card\ in \core/templates/core/tabs/tab_queue.html\ with 3 columns (Order Details, Plate Details, Tracker Details). Wired in \core/static/core/js/tab_queue.js\ to bind \p.hardware\ or \p.order\ dynamically.
- **Candidate Grid 3-in-Row**: Updated \#modal-replace-image\ container in \core/templates/core/modals.html\ and grid styling to \grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; max-height: 480px;\. Increased image candidate height to ppx\ with \object-fit: contain\ in \core/static/core/js/tab_queue.js\.
- **Mobile Off-Conveyor (U-Turn) In-Place Replacement**:
  - \core/views_mobile.py\: Added \is_replacement\ and eplace_sequence_number\ handling in \pi_mobile_upload\. Purges old unsubmitted image in vault before ingesting new photo to preserve exact counts and trajectory alignment.
  - \core/views_mobile.py\: Added \pi_mobile_uturn_undo_snap\ endpoint for 'Retake Last'.
  - \core/static/core/js/mobile_companion.js\: Added \MobileState.inspectedContext\ and \MobileState.uturn.replacingTile\. Retaking from photo inspector replaces tile in-place without incrementing photo counts or shifting trajectory index.
- **Automated Tests**: Unit tests \test_uturn_replace_photo_in_place\ and \test_uturn_undo_snap\ in \core/tests/test_mobile_companion.py\ passed with 100% success.

---

## 21. Stock Tab UI/UX Enhancements: Centralized Refresh, Unified Ledger Modal & Contextual Excel Paste (Completed)

### 21.1 Unified 9-Row Daily Stock Ledger Modal
- **Container**: `#modal-edit-ledger` in `core/templates/core/tabs/tab_stock.html`.
- **Button**: Replaced competing `#btn-open-edit-ledger` and `openLedgerTargetModal` with single `✏️ Edit Ledger (O)`.
- **Sections**:
  1. *Opening Balances*: PSV White, PMO Yellow, Total with "⚡ Auto-Carry Previous Closing" helper button.
  2. *Scheduled Targets*: Scheduled Total, PSV Target, PMO Target.
  3. *Shift Overrides*: Deliveries, Installed, Transfers (In/Out), Closing Overrides, and Physical Count.
  4. *Shift Remarks*: Textarea for shift handover notes.
- **Save Action**: Calls `POST /api/stock/ledger/entry/` atomically.

### 21.2 Centralized One-Tap Refresh (`#btn-refresh-stock-tab`)
- **Location**: `#pane-stock .pane-header .header-actions` next to the shift date selector.
- **Behavior**: Triggered by button click or hotkey `R`. Animates rotating icon, fetches reconciliation, shift details, and blocked plate status, and updates dashboard KPIs.

### 21.3 High-Visibility Display of Kits Not On Stock
- **Filter Pills**: Added `.stock-filter-pills-bar` above `#table-stock-dispatch`:
  - `All Dispatched (N)`
  - `✓ In Stock (N)`
  - `⚠️ Not On Stock (N)` (highlighted in red/amber)
  - `❌ Unallocated Floor (N)`
- **Behavior**: Clicking `⚠️ Not On Stock` filters the table immediately in-memory to show kits requiring warehouse stock transfer.

### 21.4 Context-Aware "Paste from Excel" Modal
- **Container**: `#stock-paste-modal`.
- **Dynamic Configuration**:
  - *Dispatch Mode*: "Paste Dispatched Kits from Excel", with "Check Stock Only" and "Dispatch Plates" buttons.
  - *Movements Mode*: "Paste Stock Movements from Excel", featuring explicit radio buttons:
    - 📦 Inbound Delivery (Add Delivered Kits)
    - ↩️ Line Return (Add Returned Kits)
    - ⇄ Bond Transfer In
    With partner and reference fields.
  - *Safe Audit Mode*: "Paste Safe Room Physical Audit from Excel", recording direct physical inventory counts.



---

## 22. Feature: Reports Tab Operational Stages, Scanned vs Skipped Discrepancy UI, and Centralized CSV Export Hub - [COMPLETED]

### 22.1 Dynamic Order Stages Pill Breakdown
- **Location**: `#pane-reports` inside Category 2 card.
- **Container**: `#report-cat2-stages-pills`.
- **Behavior**: Renders dynamic, real-time stage badges (`Ready for installation`, `Under installation`, `Ready for approve`, etc.) with exact order counts populated from `/api/reports/totals/` without any 100-record query limitations.

### 22.2 Scanned vs Skipped Problem Identifier KPI & Table
- **Location**: `#pane-reports` Category 4 card and dedicated discrepancy section below the unallocated floor table.
- **KPI Card**: `#report-scanned-vs-skipped-ratio`, `#report-skipped-count`, `#report-scanned-vs-skipped-desc`, `#report-skipped-breakdown`.
- **Discrepancy Table**: `#report-skipped-tbody` displaying:
  - Plate with high-visibility problem badges (`⚠️ Skipped Safe Room`, `📷 Missing Photos`).
  - ITMS Order Number & current stage.
  - Audit Reason & Root Cause explanation.
  - VIN & Facility code.
  - Actionable resolution guidance.
- **Actions**:
  - `copySkippedRawPlates()`: Quick one-click copy of all problem plates for floor supervisors and MVR officers.
  - `downloadReportCsv('scanned_vs_skipped')`: Instant CSV download.
  - `#report-skipped-search`: Real-time instant text search filter.

### 22.3 Centralized Reports Export Center
- **Menu**: `#shift-csv-dropdown` in Reports Tab header.
- **Endpoints & Types**: All operational exports unified under `downloadReportCsv(type)` calling `/api/export/report/?type=<type>&date=<suffix>`:
  - 📑 Master Verification Pairs CSV (`master`)
  - 📊 Order Stages Breakdown CSV (`stages`)
  - ⚠️ Scanned vs Skipped Discrepancy CSV (`scanned_vs_skipped`)
  - 🔴 Floor Unallocated Kits CSV (`unallocated`)
  - 🔵 Completed Archive Orders CSV (`archived`)
  - 🟡 Active Pending Orders CSV (`pending`)

### 22.4 Scoped Safe-Room Stock Tab Exports
- **Menu**: `#stock-export-dropdown` in Stock Tab header.
- **Scope**: Exclusively exports safe-room warehouse inventory:
  - Daily Reconciliation Ledger CSV
  - Safe Room Dispatches CSV
  - Inbound Deliveries & Movements CSV
  - Safe Room Physical Audit Scans CSV
  - Safe Room Unregistered Plates CSV
  - Blocked / Not On Stock CSV
- Eliminated cross-cutting order and verification exports from the Stock Tab.


### Frontend & UI/UX Update: History Tab Distinct Filter Chips, Dual Photo Thumbnails & Lightbox View (09 November 2026)
- **Status:** COMPLETED & TESTED
- **Key Enhancements Implemented:**
  1. **6 Distinct Subnavigation Filter Chips in `#pane-history`:**
     - `All Events` (`data-hfilter="ALL"`)
     - `Submissions` (`data-hfilter="SUBMISSIONS"`)
     - `Approvals` (`data-hfilter="APPROVALS"`)
     - `Overrides` (`data-hfilter="OVERRIDES"`)
     - `Issues & Fails` (`data-hfilter="ISSUES"`)
     - `Stock & Audits` (`data-hfilter="STOCK"`)
     - Full tooltips, active amber accent styling (`#f59e0b`), backward compatibility with legacy filter strings (`SUBMITTED`, `FAILED`).
  2. **Dedicated Evidence Photos Column in Desktop Grid Table (`.itms-grid-table`):**
     - Dual thumbnail previews (Front and Rear, 44x32px) positioned between Plate and Matched Order #.
     - Single-tap/click triggers full-resolution image zoom in modal lightbox (`openLightbox`).
     - Error fallback gracefully hides failed thumbnails without broken image boxes.
  3. **Touch-Optimized Photo Strips on Mobile Touch Cards (`.history-card-item`):**
     - Side-by-side front and rear cards (height: 74px) with high-contrast `FRONT` and `REAR` tags.
     - Tapping a photo opens full image in lightbox with `event.stopPropagation()`, preserving tap-to-inspect detail on the rest of the card.
  4. **Multi-Angle Audit Detail Modal Enhancements (`#modal-history-detail`):**
     - Front and rear photo preview boxes dynamically show or hide based on available angles.
     - Click handler loads full uncompressed vault photo into lightbox.

---

## 23. Feature: Sibling Series Collision Confirmation Modal & Dual-Category Unallocated Kits UI - [BACKEND READY]

The Backend & DevOps team has completed and verified the core backend services, endpoints, and validation protocols for:
1. **Sibling Plate Collision Detection (`UMA496QV` vs `UMA496QW`)**
2. **Dual-Category Unallocated Kit Tracking (`NOT_ON_STOCK` vs `SKIPPED_BY_MVR`)**
3. **Instant Stock Modal Ingress & Auto-Pruning of Installed Kits**

### 23.1 Sibling Series Collision Confirmation Modal (`tab_queue.js` & `QuickPlateTypeModal`)
- **Trigger**: An operator attempts to link an order or manually override a plate (e.g. `UMA496QV`) when active sibling plates sharing the exact same numeric sequence (e.g. `UMA496QW`) exist in active orders, dispatches, or review queue.
- **Backend Response**:
  `POST /api/pairs/<pair_id>/action/` returns HTTP 200 with JSON:
  ```json
  {
    "success": false,
    "requires_confirmation": true,
    "confirmation_type": "SIBLING_PLATE_COLLISION",
    "error": "Sibling series collision detected! The numeric sequence '496' is shared by sibling plate(s): UMA496QW. Please verify that the vehicle photo and physical plate strictly match 'UMA496QV' before confirming.",
    "target_plate": "UMA496QV",
    "numeric_digits": "496",
    "sibling_plates": ["UMA496QW"],
    "sibling_orders": [
      {
        "order_id": 1045,
        "order_number": "PO-UMA496QW-01",
        "registration_number": "UMA496QW",
        "status": "Pending",
        "chassis": "MD2A..."
      }
    ],
    "code": "SIBLING_PLATE_COLLISION"
  }
  ```
- **Frontend Action Required**:
  1. Catch `res.requires_confirmation === true && res.confirmation_type === 'SIBLING_PLATE_COLLISION'`.
  2. Display a high-visibility warning modal / confirmation dialog `#modal-sibling-collision`:
     - Header: `⚠️ Sibling Plate Series Alert` (amber background).
     - Body: Explains that plate `UMA496QV` shares digits `496` with sibling series `UMA496QW`. Prompts the operator to inspect the front/rear photos to ensure they are not confusing sibling bikes.
     - Buttons: `[Cancel]` and `[Confirm & Link Anyway]` (primary).
  3. When the operator clicks `[Confirm & Link Anyway]`, re-send the POST payload with `confirm_sibling: true`:
     ```javascript
     fetch(`/api/pairs/${pairId}/action/`, {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({
         action: 'link_order',
         order_id: targetOrderId,
         confirm_sibling: true
       })
     });
     ```
  4. The backend will proceed with the link and tag the audit log / operator note with `[SIBLING_OVERRIDE_CONFIRMED: UMA496QW]`.

---

### 23.2 Dual-Category Unallocated Kits Tables (`tab_stock.html` / `tab_reports.html`)
- **Endpoints**:
  - `GET /api/stock/unallocated/?date_suffix=<suffix>` (JSON)
  - `GET /api/stock/unallocated/?date_suffix=<suffix>&format=csv` (Direct CSV download)
- **JSON Structure**:
  ```json
  {
    "success": true,
    "work_date_suffix": "10.11.2026",
    "warehouse_code": "AGM",
    "total_unallocated": 12,
    "not_on_stock": {
      "count": 4,
      "plates": ["UMA496QV", "UMA502B"],
      "items": [
        {
          "plate": "UMA496QV",
          "category": "NOT_ON_STOCK",
          "reason": "Not on ITMS Stock (Awaiting Central Depot Inbound - MVR cannot access)",
          "action": "Request Inbound from ITMS Central Depot / Transfer Manager",
          "has_camera_evidence": true,
          "dispatched_by": "Operator Jane",
          "dispatched_at": "2026-11-10 14:22:10"
        }
      ]
    },
    "skipped_by_mvr": {
      "count": 8,
      "plates": ["UMA501A"],
      "items": [
        {
          "plate": "UMA501A",
          "category": "SKIPPED_BY_MVR",
          "reason": "On Stock in warehouse, but skipped by MVR during order allocation",
          "action": "Notify MVR Officer to allocate order in ITMS",
          "has_camera_evidence": false,
          "dispatched_by": "Operator Jane",
          "dispatched_at": "2026-11-10 08:15:00"
        }
      ]
    }
  }
  ```
- **Frontend Action Required**:
  1. In the Stock & Reports discrepancy views, render two distinct sub-tabs or side-by-side tables:
     - **Table 1: Category A — NOT ON STOCK (Awaiting Inbound)**:
       - Plate badge: Red pill `🔴 Not on ITMS Stock`.
       - Explains: Kit was physically scanned/pasted by operator, but is absent from ITMS database. MVR cannot allocate it until central depot processes inbound!
       - Action: "Request Inbound from Central Depot".
     - **Table 2: Category B — SKIPPED BY MVR (Pending Allocation)**:
       - Plate badge: Amber pill `🟡 Skipped by MVR`.
       - Explains: Kit exists in safe-room stock, but MVR officer skipped creating an order.
       - Action: "Notify MVR Officer to allocate order".
  2. Add `[📥 Export Unallocated CSV]` button linking to `/api/stock/unallocated/?date_suffix=${currentSuffix}&format=csv`.

---

### 23.3 Stock Modal Performance (<50ms Open Time) & Dynamic Pruning
- **Enhancement**:
  - `GET /api/stock/blocked-plates/` no longer executes synchronous external HTTP scraping against ITMS on modal open.
  - Returns local database records in < 50ms.
  - Background thread asynchronously refreshes live ITMS stock without freezing the UI.
  - If a plate was previously in `StockBlockedScan` ("Set Aside / Not on stock") but now appears in an installed/archived order or submitted vehicle pair:
    - Backend automatically prunes the record from the database.
    - Status dynamically displays as `Pruned (Installed / Closed)` rather than `Set Box Aside`.

---

## 24. Feature: Edit Ledger Modal Overrides & Returned Count Support

### 24.1 Endpoint Contract: `POST /api/stock/ledger/entry/`
- **Decorator Update**:
  - Endpoint now allows expired or unlinked authenticated Copilot operators to save overrides without 412 or 401 error blocks (`allow_expired=True, allow_unlinked=True`).
- **Supported Payload Fields**:
  ```json
  {
    "target_date_suffix": "101126",
    "storage_bond_name": "AGM / SPIRO",
    "opening_balance_psv": 175,
    "opening_balance_pmo": 0,
    "kits_received_psv": 200,
    "kits_received_pmo": 0,
    "scheduled_psv": 320,
    "scheduled_pmo": 0,
    "scheduled_total": 320,
    "kits_installed_psv": 319,
    "kits_installed_pmo": 0,
    "bond_transfer_in_psv": 0,
    "bond_transfer_in_pmo": 0,
    "bond_transfer_out_psv": 0,
    "bond_transfer_out_pmo": 0,
    "returned_count": 3,
    "dispatched_count": 322,
    "closing_balance_psv": 56,
    "closing_balance_pmo": 0,
    "closing_stock": 56,
    "physical_count": 56,
    "notes": "Reconciled daily shift: Opening 175, Received 200, Scheduled 320, Installed 319, Returned 03, Closing 56"
  }
  ```

### 24.2 UI Recommendations for `modal-edit-ledger` (`core/templates/core/modals.html` & `tab_stock.js`)
1. **Optional "Returned Kits" Row in Modal Table**:
   - In `modal-edit-ledger`, add an input row for `Returned Kits`:
     ```html
     <tr>
         <td style="font-weight:700; color:#ffffff !important;">Returned to Safe Room</td>
         <td colspan="2"><input type="number" id="edit-ledger-returned" class="form-control" style="width:100%; text-align:center; font-family:var(--font-mono); font-weight:700; background:#0c1017; color:#ffffff; border:1px solid rgba(255,255,255,0.2);" placeholder="e.g. 3"></td>
         <td id="edit-ledger-returned-total" style="text-align:center; font-family:var(--font-mono); font-weight:800; color:#ffffff !important;">0</td>
     </tr>
     ```
2. **`tab_stock.js` Updates**:
   - In `prefillEditLedgerFromCurrent()`:
     - Read `lastReconData.returned_count` or `lastReconData.total.returned` and populate `edit-ledger-returned`.
   - In `submitEditLedger()`:
     - Include `returned_count: optInt('edit-ledger-returned')` in the JSON payload sent to `/api/stock/ledger/entry/`.

---

## 25. Feature: Mobile Companion Stability (IndexedDB), Shutter Auto-Switch, Calendar Date Selector & Quick Install UI - [COMPLETED]

### 25.1 Mobile Companion: Off-Conveyor Camera Shutter Auto-Switch
- **Files**:
  - `core/templates/core/mobile_companion.html`
  - `core/static/core/js/mobile_companion.js`
- **Identified Issue**:
  - When starting off-conveyor walks with FRONT first (`startLeg === "FRONT"`), the camera shutter button does not switch to REAR, and tapping the road steps uses blocking browser `confirm()` calls that mobile browsers often suppress.
- **Frontend Action Items**:
  1. In `safeSwitchLeg(targetLeg)` and `confirmTurnaround()`:
     - Remove blocking native `confirm(...)` modal dialogs which mobile Chrome suppresses.
     - Replace with instant switch + vibrating haptic feedback (`navigator.vibrate([60])`) and toast notification (`showMobileToast("Switched to snapping REARS")`).
  2. In `applyUturnPhaseUI(phase)`:
     - Ensure the primary shutter button (`#btn-big-shutter`) updates its title and styling dynamically to `Snap Rear #...` immediately upon switching phase from FRONT.

### 25.2 Mobile Companion: Low-Memory Crash Prevention (Persistent IndexedDB)
- **Files**:
  - `core/static/core/js/mobile_companion.js`
- **Identified Issue**:
  - Snapping 40–80 high-resolution photos in memory fills JavaScript heap and crashes low-RAM mobile browsers when receiving push notifications or backgrounding.
- **Frontend Action Items**:
  1. **Zero-In-Memory-Blobs Architecture**:
     - Do NOT hold full `File` or `Blob` objects or large Base64 DataURLs in global arrays (`MobileState.uturn.recentTiles`, `MobileOrderState`).
     - Immediately save captured Blobs to IndexedDB store (`STORE_NAME = "outbox_queue"`).
     - Store only lightweight metadata in memory: `{ id, orientation, seqNum, timestamp }`.
  2. **On-Demand Inspection**:
     - When an operator taps a preview tile to open `photo-lightbox-modal`, load the photo Blob on-demand from IndexedDB by `id` using `URL.createObjectURL(blob)`.
     - Call `URL.revokeObjectURL()` upon closing the inspector.
  3. **Auto-Purge on Server Confirmation**:
     - When the background sync worker drains the outbox and receives HTTP 200 `{ success: true, ... }` from `/api/mobile/upload/`, delete the item from IndexedDB immediately to free device storage.

### 25.3 Global Shift Date Selector: Modern Calendar Module
- **Files**:
  - `core/templates/core/base.html`
  - `core/static/core/js/pipeline.js`
- **Identified Issue**:
  - The legacy `<select>` dropdown feels dated and does not allow selecting arbitrary dates easily.
- **Frontend Action Items**:
  1. In `core/templates/core/base.html` (`.drawer-shift-box` and header):
     - Replace or supplement the `<select>` dropdown with an interactive `<input type="date" class="header-shift-datepicker">` matching the design in Settings under Shift Data (`#settings-manual-date-picker`).
  2. In `core/static/core/js/pipeline.js`:
     - Bind date picker `onchange` to `window.setGlobalShiftDate(this.value)`.
     - Ensure all components update when `shiftDateChanged` event fires.

### 25.4 Batches Tab: Global Shift Date Scoping
- **Files**:
  - `core/static/core/js/tab_batches.js`
  - `core/static/core/js/pipeline.js`
- **Identified Issue**:
  - The Batches tab ignores the active operating shift date and only loads today's batches.
- **Backend Contract Added**:
  - `GET /api/batches/?date=<suffix>` or `GET /api/batches/?date=<YYYY-MM-DD>` strictly filters batches for that selected shift date and returns `{ success: true, batches: [...], target_date: "...", date_suffix: "..." }`.
- **Frontend Action Items**:
  1. In `tab_batches.js:loadBatchesList()`:
     - Pass the active shift date: `fetch('/api/batches/?date=' + encodeURIComponent(currentShiftDate))`.
  2. In `pipeline.js:setGlobalShiftDate()`:
     - Trigger batch reload: `if (typeof loadBatchesList === "function") { loadBatchesList(); }`.

### 25.5 Quick Install: Instant Search & Submission Flow
- **Files**:
  - `core/templates/core/mobile_companion.html`
  - `core/static/core/js/mobile_companion.js`
- **Backend Endpoints Verified**:
  - `GET /api/orders/?search=<query>&limit=10` (instant search by Order #, Registration Plate, or VIN).
  - `POST /api/orders/<order_ident>/install/` (FormData: `action=submit`, `front_photo`, `rear_photo`, `front_plate_id`, `rear_plate_id`, `tracker_id`, `dry_run`).
- **Frontend Action Items**:
  1. In `mobile_companion.html`:
     - Replace the bulky `<select id="m-order-picker-select">` with a fast search input `<input type="text" id="m-order-search-input" placeholder="Search Order # or Number Plate...">` with a debounced autocomplete list.
     - Reduce repetitive instructions and card padding for a clean, single-screen layout.
  2. In `mobile_companion.js`:
     - Verify `submitMobileOrderInstall()` reads the selected order, attaches `front_photo` and `rear_photo` from the dropzone files, and submits via `POST /api/orders/${orderNumber}/install/`.
     - On HTTP 200, update status badge to `INSTALLED`, show a confirmation toast, and reset form for the next order.

---

## 26. SonarQube Code Quality & Security Gate Remediation [COMPLETED]

### 26.1 Blocker: Undeclared Global Variables (`javascript:S2703`) [RESOLVED]
- **Files**:
  - `core/static/core/js/tab_itms.js` (lines 223–229, 283, 335)
  - `core/static/core/js/navigation.js` (lines 15, 56)
  - `core/static/core/js/pipeline.js` (lines 1040, 1048)
- **Remediation**:
  - In `tab_itms.js`: Added explicit module-scoped `let` declarations (`itmsCurrentSubview`, `itmsExplorerSource`, `itmsExplorerOrders`, `itmsExplorerPage`, `itmsExplorerSearch`, `selectedItmsOrder`).
  - In `navigation.js`: Declared `let activeNavTab = typeof window.activeNavTab !== "undefined" ? window.activeNavTab : 1; window.activeNavTab = activeNavTab;` and synchronized `window.activeNavTab` in `switchNavTab`.
  - In `pipeline.js`: Declared `let reportCurrentDate = data.active_shift.suffix;` and `let currentStockDateSuffix = data.active_shift.suffix;` with safe `window` property synchronization.

### 26.2 Blocker: DOM XSS via Unsanitized User Input (`jssecurity:S5696`) [RESOLVED]
- **Files**:
  - `core/static/core/js/mobile_companion.js` (lines 33, 1646, 2709, 2721, 3441)
  - `core/static/core/js/tab_stock.js` (lines 14, 1090)
- **Remediation**:
  - In `mobile_companion.js`:
    - Added standard `escapeHtml(str)` utility.
    - Converted `renderBlockedPlatesTray()` to safe DOM element creation (`document.createElement` / `replaceChildren` / `.textContent`) and attached discrete event listeners, eliminating inline string interpolation.
    - Refactored `btnFinish.innerHTML` in off-conveyor walk mode to `btnFinish.textContent`.
    - Sanitized order autocomplete dropdown generation using programmatic DOM node generation (`replaceChildren`, `.textContent`, `addEventListener`).
    - Refactored `m-order-picker-select` option generation using `replaceChildren()` and `document.createElement("option")`.
  - In `tab_stock.js`:
    - Added `escapeHtml(str)` utility.
    - Sanitized all dynamic user / plate / category / reason variables before inserting into `not-on-stock-table-body` innerHTML.

### 26.3 Major: Accessibility - Form Inputs Without Labels (`Web:InputWithoutLabelCheck`) [RESOLVED]
- **Files**:
  - `core/templates/core/base.html` (lines 122, 123, 179, 183)
  - `core/templates/core/mobile_companion.html` (lines 408, 458, 468, 508, 513, 519, 525, 529, 550, 570, 581, 622, 623, 624)
- **Remediation**:
  - Added descriptive `aria-label` attributes to desktop and drawer shift calendar datepickers (`#header-global-shift-date`, `#drawer-global-shift-date`) and shift selects (`#header-global-shift-select`, `#drawer-global-shift-select`).
  - Added descriptive `aria-label` and `for="..."` pairings across all input and select fields in `mobile_companion.html` (order search, hardware serial inputs, file upload camera inputs, and dry-run checkbox).

---

## 27. Feature: Shift Date Selector Consolidation, Laptop Calendar Picker & High-Res Replace Evidence Photo Modal [COMPLETED]

### 27.1 Consolidated Single Shift Date Selector (Header Bar Only) [COMPLETED]
- **User Request & Rationale**:
  - Screenshot `Screenshot 2026-11-11 072757.png` showed multiple redundant shift date selection areas on the desktop view. The user specified: *"one shows many shift date selection areas; yet we need one; we keep the one very top; i have also noted the date picker calender works on phones not laptops"*.
- **Target Files**:
  - `core/templates/core/base.html`
  - `core/templates/core/tabs/tab_dashboard.html`
  - `core/templates/core/tabs/tab_stock.html`
  - `core/static/core/css/app_layout.css`
  - `core/static/core/js/pipeline.js`
- **Frontend Action Items Completed**:
  1. **Hide Mobile Drawer Shift Box on Desktop (`core/static/core/css/app_layout.css`)**:
     - Added `.drawer-shift-box { display: none; }` to desktop scope and `.drawer-shift-box { display: block; }` inside `@media (max-width: 991px)`, eliminating desktop drawer leakage into navigation tabs.
  2. **Remove Redundant Selectors on Dashboard & Stock Tabs**:
     - In `core/templates/core/tabs/tab_dashboard.html`: Removed redundant `.dash-shift-selector-wrap` (`#dash-global-shift-select` and Today button). Retained informative status badge and shift date text.
     - In `core/templates/core/tabs/tab_stock.html`: Removed `#stock-date-display` which duplicated `#stock-active-shift-badge`.
  3. **Laptop Calendar Picker Support (`.showPicker()`)**:
     - In `core/templates/core/base.html`: Added `onclick="try{if(typeof this.showPicker==='function'){this.showPicker();}}catch(e){}"` directly to `#header-global-shift-date` and `#drawer-global-shift-date`.
     - In `core/static/core/js/pipeline.js`: Added `attachLaptopCalendarPicker` to register click handlers invoking `.showPicker()` across all `.header-shift-datepicker` inputs on DOM ready and dynamically during shift sync.
     - In `core/static/core/css/app_layout.css`: Styled `.header-shift-datepicker` and `::-webkit-calendar-picker-indicator` with pointer cursor and bright cyan inverted calendar icon filter.

### 27.2 Crisp High-Resolution Replace Evidence Photo Modal [COMPLETED]
- **User Request & Rationale**:
  - Screenshot `Screenshot 2026-11-10 201742.png` showed blurred, pixelated thumbnail images in the "Pick from Batch" grid inside `#modal-replace-image`.
  - Backend now generates high-fidelity WebP previews (960x720, Lanczos) and returns `preview_url` and full `url`.
- **Target Files**:
  - `core/static/core/js/tab_queue.js`
  - `core/templates/core/modals.html`
- **Frontend Action Items Completed**:
  1. In `core/static/core/js/tab_queue.js`:
     - Updated candidate image source from `thumb_url` to `c.preview_url || c.url || c.thumb_url`.
     - Added `loading="lazy"` and `image-rendering: -webkit-optimize-contrast; object-fit: contain; width: 100%; height: 100%;`.
     - Placed preview inside a clean dark canvas container (`#070a10`) with `height: 165px;`.
  2. In `core/templates/core/modals.html` & `core/static/core/css/modals.css`:
     - Ensured `.candidate-tile` styling provides crisp dark background (`#111622`), subtle 2px border, clean hover lifts, and high-visibility `#ffd100` 2px selected border.

---

## 28. Feature: Conflict Tab Order Linking Fix, Batches Scope Consolidation & Quick Photo-to-Order Link Workflow [COMPLETED]

### 28.1 Queue Tab: Fix Order Linking from Conflict Tab [COMPLETED]
- **Identified Issue**:
  - Operators were unable to link orders to pairs in the Conflict filter.
  - Sibling series collision checks on the backend returned `requires_confirmation: True`, but `tab_queue.js` had zero handling for `requires_confirmation` / `confirm_sibling`, treating it as a fatal error and showing a red toast.
  - Additionally, if `search` was empty or plate was unrecognized, `api_orders_list` threw HTTP 412 if the operator's ITMS web session was unlinked/expired.
- **Backend Fix Landed**:
  - `core/views/pairs.py`: Explicit operator order selection (`order_id` passed) or `confirm_sibling=1` now bypasses blocking and links the order cleanly. If claimed by another unsubmitted pair, it safely reassigns.
  - `core/views/orders.py`: `api_orders_list` now has `allow_unlinked=True, allow_expired=True` so browsing orders for linking never 412s.
- **Target Files**:
  - `core/static/core/js/tab_queue.js`
  - `core/templates/core/tabs/tab_queue.html`
- **Frontend Action Items Completed**:
  1. In `core/static/core/js/tab_queue.js`:
     - In `linkOrderToPair(orderId, confirmSibling=false)`:
       - Sends `confirm_sibling=1` when `confirmSibling` is true.
       - Intercepts `data.requires_confirmation`: Prompts the operator with warning confirmation dialog and, if confirmed, re-calls `linkOrderToPair(orderId, true)`.
     - In `submitUnifiedLinkOrder(confirmSibling=false)`:
       - Intercepts `data.requires_confirmation`: Prompts the operator and re-sends with `confirm_sibling=1`.
     - In `searchOrdersForLinking(q)`:
       - When `q` is empty, "UNKNOWN", or yields no exact match, falls back to fetching all active unlinked orders from `/api/orders/?limit=50`.
       - Renders a clean notice *"Select an active order below to link & close:"* with 1-click `Link ➜` buttons on every row.
     - In `openUnifiedLinkModal(pairId, defaultPlate)`:
       - Pre-populates the modal and queries orders on demand, exported on `window.openUnifiedLinkModal`.

### 28.2 Batches Tab: Scope Filter Consolidation [COMPLETED]
- **Identified Issue**:
  - The Batches tab had multiple competing date scope buttons (`Today's Batches`, `Active Batch`, `Prior Carryover`, `All Batches` on desktop and mobile) which conflicted with the top header shift date selector.
  - Selecting a date from the top header caused the Batches tab to fail or show contradictory labels.
- **Backend Fix Landed**:
  - `core/views/upload.py` (`api_batches_list`): Fully supports `scope=ALL` (all historical batches) and `scope=SHIFT` / `date=<date>` (Kampala timezone range filtering + `batch_id` date matching). Removed restrictive operator filtering so batches are never hidden between facility operators.
- **Target Files**:
  - `core/templates/core/tabs/tab_batches.html`
  - `core/static/core/js/tab_batches.js`
- **Frontend Action Items Completed**:
  1. In `core/templates/core/tabs/tab_batches.html`:
     - Replaced the 4 scope buttons on both desktop and mobile with a consolidated 2-way toggle:
       - `[📅 Shift Batches]` (`id="btn-bscope-shift"`, active by default)
       - `[🌐 All Batches]` (`id="btn-bscope-all"`)
     - Updated `#batches-scope-summary-tag` to show clear status: `Shift: <label> • X batch(es)` or `All Batches • Y total`.
  2. In `core/static/core/js/tab_batches.js`:
     - Implemented `setBatchesScopeMode(mode)` ("SHIFT" vs "ALL").
     - In `fetchBatchesList(shiftDate)`:
       - Queries `/api/batches/?scope=ALL` when in "ALL" mode.
       - Queries `/api/batches/?scope=SHIFT&date=${encodeURIComponent(currentShiftDate)}` when in "SHIFT" mode.
     - Listens for `shiftDateChanged` events from the global header date selector and automatically re-fetches batches in shift mode.

### 28.3 Fast Photo-to-Order Linking ("pictures i need quickly and any order to close") [COMPLETED]
- **Backend Fix Landed**:
  - Created `POST /api/pairs/create/` (accepts `image_id`, creates a new `VehicleInstallationPair` on the fly, and returns `pair_id`).
- **Target Files**:
  - `core/static/core/js/tab_batches.js`
- **Frontend Action Items Completed**:
  1. In `renderFullScaleBatchPhotos(batchId, images)`:
     - For paired photos: Added direct `⚡ Quick Link Order` action button invoking `quickLinkFromBatch(img.pair.pair_id, img.pair.detected_plate)`.
     - For unassigned photos: Added direct `⚡ Pair & Link Order` action button invoking `quickPairAndLinkUnassignedPhoto(img.id, img.orientation, img.detected_plate)`.
  2. Implemented `quickPairAndLinkUnassignedPhoto(imageId, orientation, detectedPlate)`:
     - Posts to `/api/pairs/create/` with `image_id=${imageId}`.
     - Upon receiving `data.pair_id`, automatically invokes `quickLinkFromBatch(data.pair_id, detectedPlate)`.
  3. Implemented `quickLinkFromBatch(pairId, detectedPlate)`:
     - Sets target pair context, opens `modal-edit-plate`, pre-fills plate search, and immediately lists active ITMS orders for 1-click linking.
  4. In `core/templates/core/base.html`:
     - Bumped script query versions: `tab_batches.js?v=28` and `tab_queue.js?v=31`.

---

## 29. Bugfix: Resolve `currentBatchesDateScope` Duplicate Declaration & `initDropzones` ReferenceError [COMPLETED]

### 29.1 Context & Errors Encountered
- Browser console errors reported:
  1. `tab_batches.js?v=28:1 Uncaught SyntaxError: Identifier 'currentBatchesDateScope' has already been declared`
  2. `app.js?v=27:12 Uncaught ReferenceError: initDropzones is not defined at HTMLDocument.<anonymous> (app.js?v=27:12:5)`
- **Root Cause**:
  - `core/static/core/js/state.js` (line 25) declares `var currentBatchesDateScope = "TODAY";` in global scope.
  - `core/static/core/js/tab_batches.js` (line 60) redeclared `let currentBatchesDateScope = "SHIFT";` in the same global scope, causing a fatal `SyntaxError` that aborted parsing of `tab_batches.js` before `initDropzones` could be registered.

### 29.2 Implemented Fixes [COMPLETED]
1. **`core/static/core/js/state.js`**:
   - Updated line 25 to `var currentBatchesDateScope = "SHIFT"; // "SHIFT" | "ALL"`.
2. **`core/static/core/js/tab_batches.js`**:
   - Removed `let` keyword, using `currentBatchesDateScope = "SHIFT";` assignment to reuse the declaration from `state.js`.
   - Explicitly exported `window.initDropzones = initDropzones;` immediately after the `initDropzones` definition.
   - Verified that no other top-level `let`/`const` variables in `tab_batches.js` collide with `state.js`.
3. **`core/templates/core/base.html`**:
   - Bumped cache-busting query versions: `state.js?v=29` and `tab_batches.js?v=29`.

---

## 30. Feature: Scope Review Queue by Selected Shift Date [COMPLETED]

### 30.1 Context & Requirement
- **User Request**: *"we need to improve the queue page to also scope based on seleceted date; to show all pairs worked on that date selected"*
- **Backend Fix Landed**:
  - `core/views/pairs.py` (`api_pairs_list` & `api_batch_submit`):
    - Accepts `?date=<YYYY-MM-DD or DDMMYY or ALL>` and resolves against `date_service.get_active_shift_info()` when omitted.
    - Filters pairs by Kampala timezone day range across `created_at`, `updated_at`, `front_image__captured_at`, `rear_image__captured_at`, `front_image__batch__created_at`, `rear_image__batch__created_at`, `batch_id` date tokens, and `order__order_number__endswith=suffix_str`.
    - Returns `"shift_date"`, `"shift_suffix"`, and `"shift_display"` in `/api/pairs/` JSON response.

### 30.2 Implemented Frontend Changes [COMPLETED]
1. **`core/static/core/js/tab_queue.js`**:
   - Updated `fetchPairs(shiftDate = null)`:
     - Resolves `currentShiftDate` from `shiftDate || (window.activeShiftDateInfo && (window.activeShiftDateInfo.date || window.activeShiftDateInfo.suffix)) || ""`
     - Appends `&date=${encodeURIComponent(currentShiftDate)}` when `activeScope !== "ALL"`.
     - In `#queue-batch-select` dropdown options:
       - Displays `📅 Selected Shift (${escapeHtml(data.shift_display || 'Active Shift')})` on the primary shift option.
     - Updates `#queue-approved-count-text` to display `${pairsData.length} pairs (${data.shift_display || activeScope}) • ${approvedCount} ready`.
     - Exports `window.fetchPairs = fetchPairs`.
   - Added global `shiftDateChanged` event listener:
     - Resets `currentQueueBatchFilter = "ALL"`.
     - Automatically adapts `currentQueueDateScope` between `"ALL"` and `"TODAY"`.
     - Re-fetches pairs for the newly selected shift date: `void fetchPairs(d)`.
   - Wired `approvedBtn.onclick = () => triggerBatchSubmit(activeScope, currentShiftDate);` to pass shift date context.
2. **`core/static/core/js/pipeline.js`**:
   - In `window.setGlobalShiftDate`:
     - Added automatic Queue refresh: `if (typeof fetchPairs === "function") { void fetchPairs(data.active_shift.date || data.active_shift.suffix); }`.
   - In `triggerBatchSubmit(requestedScope, shiftDate = null)`:
     - Passes `&date=${encodeURIComponent(currentShiftDate)}` when loading approved pairs for submission.
3. **`core/templates/core/tabs/tab_queue.html`**:
   - Updated initial `<option value="SCOPE:TODAY" selected>` text to `📅 Selected Shift`.
4. **`core/templates/core/base.html`**:
   - Bumped cache-busting query versions: `tab_queue.js?v=32` and `pipeline.js?v=30`.

---

## 31. Feature: Multi-Layer Queue Filtering Hierarchy (Date -> Batch -> Status) [COMPLETED]

### 31.1 Problem Statement & User Request
- **User Request**:
  *"for the queue tab we need to improve the filturing logic and layers; so first we start with date; these are batches created on that day; two we move the batches; ACTIVE BACTCH; and other batches selected; then once a batch is selected the we move to pairs and show ALL, REVIEW, ISSUEE; DONE; currently i am seeing old batch photos under conflicts"*
- **Defects Identified**:
  - Previously, the queue dropdown mixed scopes (`SCOPE:TODAY`, `SCOPE:ACTIVE_BATCH`, `SCOPE:CARRYOVER`, `SCOPE:ALL`) with individual batch IDs.
  - Selecting `SCOPE:CARRYOVER` or `SCOPE:ALL` (or clicking the empty-state button) dumped all historical conflicts from prior dates (e.g., 207 conflicts from October 25) into the current operator's work queue.
  - When filtering by `ISSUES`, pairs from unrelated old batches were leaking into view because the query was not strictly constrained by the selected batch.

### 31.2 Backend API Capabilities Landed (`core/views/pairs.py`)
1. **Layer 1 (Shift Date)**:
   - `api_pairs_list` accepts `?date=<YYYY-MM-DD | DDMMYY | ALL>`.
   - Strictly scopes to batches created on that target date: `IngestionBatch.objects.filter(Q(created_at__range=(start_dt, end_dt)) | Q(batch_id__icontains=ymd_str) | Q(batch_id__icontains=suffix_str))`.
   - Never leaks old pairs based on transient `updated_at` timestamps.
2. **Layer 2 (Batch Scoping)**:
   - Accepts `?batch=ACTIVE` (or `ACTIVE_BATCH`): strictly filters pairs to the active/latest batch created on that date.
   - Accepts `?batch=ALL` (or `ALL_BATCHES`): filters pairs to all batches created on that date.
   - Accepts specific `?batch=<batch_id>`: strictly filters pairs to that specific batch.
3. **Layer 3 (Status Filtering)**:
   - Accepts `?status=ALL`, `?status=PENDING` / `REVIEW`, `?status=ISSUES` / `CONFLICT`, `?status=APPROVED`, `?status=SUBMITTED`, `?status=DONE`.
   - Returns real-time counts for the selected batch scope in `data.counts`:
     ```json
     "counts": {
       "all": 35,
       "review": 0,
       "approved": 0,
       "submitted": 0,
       "done": 0,
       "issues": 35
     }
     ```
   - Returns `data.active_batch_id`, `data.active_batch_label`, and `data.batches` list for that shift date.

### 31.3 Implemented Frontend Changes [COMPLETED]

#### 1. `core/templates/core/tabs/tab_queue.html`
- Updated `#queue-batch-select` dropdown:
  - Replaced mixed `Date Scope` optgroup with clean batch-focused options (`⚡ Active Batch` as `ACTIVE`, `📁 All Shift Batches` as `ALL`, and dynamic `Individual Batches` optgroup).
- Updated status filter pills:
  - Added count badges: `#qcount-all`, `#qcount-review`, `#qcount-approved`, `#qcount-issues`, `#qcount-done`.
  - Added scoped CSS for `.queue-filter-pill .pill-count`.

#### 2. `core/static/core/js/tab_queue.js`
- Set initial batch state: `let currentQueueBatchFilter = "ACTIVE";`.
- Updated `onQueueScopeSelectChanged(val)`: sets `currentQueueBatchFilter = val || "ACTIVE";` and calls `fetchPairs()`.
- Updated `setWorkQueueFilter(filter)`: activates selected pill and calls `fetchPairs()`.
- Updated `fetchPairs(shiftDate = null)`:
  - Queries `/api/pairs/?batch=${currentQueueBatchFilter}&status=${currentQueueFilter}&date=${currentShiftDate}`.
  - Dynamically populates `#queue-batch-select` with `ACTIVE`, `ALL`, and all shift batches.
  - Dynamically updates pill counters (`#qcount-all`, `#qcount-review`, `#qcount-approved`, `#qcount-issues`, `#qcount-done`) using `data.counts`.
  - Replaced legacy empty-state buttons ("Check Carryover", "View All") with a clean message: *"No pairs in selected batch for this shift date. Upload photos or switch batch."*
- Updated `shiftDateChanged` event listener:
  - Resets `currentQueueBatchFilter = "ACTIVE"`.
  - Re-queries queue pairs for the new shift date: `fetchPairs(d)`.

#### 3. `core/templates/core/base.html`
- Bumped query version to `tab_queue.js?v=33`.







