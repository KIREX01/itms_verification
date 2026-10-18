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


## 8. Brand Transition to "CLOSE Copilot" (Phase 1: Surface & Frontend Rebranding) - [PENDING FRONTEND ACTION]

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
