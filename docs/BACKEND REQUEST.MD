# Backend Requests & Specifications

This document tracks all backend requirements, database changes, and REST API contracts requested by the UI/UX & Frontend Development team.

---

## 1. Feature: Replace Image (Queue & Batch Interfaces) - [COMPLETED]

### 1.1 Endpoint: Replace Image on a Specific Pair - `COMPLETED`
- **URL**: `POST /api/pairs/<int:pair_id>/action/`
- **Body / Form-Data**:
  - `action`: `"replace_image"`
  - `side`: `"front"` | `"rear"`
  - `source`: `"upload"` | `"batch"`
  - `photo`: `File` (required if `source == "upload"`)
  - `image_id`: `UUID` / `int` (required if `source == "batch"`)
  - `purge_old`: `boolean` (optional, default: `true`)
- **Backend Processing Implemented**:
  1. If `source == "upload"`:
     - Ingests via `vault_service.ingest_uploaded_file(photo, batch=target_batch, orientation_override=side.upper())`.
     - Executes whole-image enhancement, fingerprinting, preview/thumbnail generation.
     - Runs vision pipeline on the image (`process_single_image_vision`): YOLO plate detection, OCR, normalization, orientation classification.
  2. If `source == "batch"`:
     - Locates existing `EvidenceImage` by `image_id`.
  3. Reassigns target pair's `front_image` or `rear_image` to the replacement image via `pair.replace_image()`.
  4. Calls `pair.refresh_completeness()`.
  5. Re-runs `DualStreamVisionEngine.process_pair(pair)` to reconcile consensus and plate matching if complete.
  6. If `purge_old == true`:
     - Safely unlinks physical vault files (`vault_file`, `thumbnail_file`, `preview_file`) via `vault_service.purge_evidence_file(old_image)`.
     - Marks old `EvidenceImage` as `is_file_pruned = True, status = "PRUNED"`.
  7. Appends audit log in `SubmissionAuditLog` with `IMAGE_REPLACED`.
- **Response**:
  ```json
  {
    "success": true,
    "message": "Front photo replaced successfully.",
    "pair": {
      "id": 12,
      "registration_number_detected": "UBK123A",
      "verification_status": "APPROVED",
      "is_complete": true,
      "side_replaced": "front",
      "new_image_id": "uuid",
      "new_image_url": "/media/vault/...",
      "new_image_thumb": "/media/vault/.../thumb.webp",
      "detected_plate": "UBK123A",
      "ocr_confidence": 94.2
    }
  }
  ```

### 1.2 Endpoint: Get Batch Candidate Photos (For Batch Picker Modal) - `COMPLETED`
- **URL**: `GET /api/batches/<str:batch_id>/candidates/`
- **Query Params**:
  - `orientation`: `"FRONT"` | `"REAR"` | `"ALL"` (optional, default: `ALL`)
  - `unassigned_only`: `boolean` (optional, default: `false`)
- **Response**:
  ```json
  {
    "success": true,
    "batch_id": "BATCH-2026...",
    "count": 5,
    "candidates": [
      {
        "id": "uuid",
        "url": "/media/vault/...",
        "thumb_url": "/media/vault/.../thumb.webp",
        "preview_url": "/media/vault/.../preview.webp",
        "orientation": "FRONT",
        "detected_plate": "UBK123A",
        "ocr_confidence": 94.2,
        "captured_at": "14:20:15",
        "is_assigned": false,
        "current_pair_id": null
      }
    ]
  }
  ```

### 1.3 Endpoint: Replace Photo Directly in Batch View - `COMPLETED`
- **URL**: `POST /api/images/<str:image_id>/replace/`
- **Body / Form-Data**:
  - `photo`: `File`
  - `purge_old`: `boolean` (default: `true`)
- **Backend Processing Implemented**:
  - Ingests replacement photo into the same batch.
  - Runs vision/OCR pipeline via `vault_service.process_single_image_vision`.
  - Rebinds any active pairs referencing this image (`pair.front_image` or `pair.rear_image`) and refreshes pair status.
  - Safely purges old image binary file if requested.
  - Returns updated photo metadata and affected pair IDs.

---

## 2. Feature: Delete Pair (Queue Interface) - `COMPLETED`

### 2.1 Endpoint: Safe Soft-Delete & Immediate Vault Purge - `COMPLETED`
- **URL**: `POST /api/pairs/<int:pair_id>/action/`
- **Body**:
  - `action`: `"delete_pair"`
  - `purge_files`: `boolean` (default: `true`)
- **Backend Processing Implemented**:
  1. **Zero Database Crash / Soft-Delete**:
     - Added `is_deleted = models.BooleanField(default=False, db_index=True)` and `deleted_at = models.DateTimeField(null=True, blank=True)` to `VehicleInstallationPair`.
     - Indexed `[is_deleted, verification_status]`.
     - Pair marked `is_deleted = True, deleted_at = timezone.now()`.
  2. **Release Installation Order**:
     - Linked `InstallationOrder` status reset to `PENDING` (if previously `MATCHED` or `FAILED`) and decoupled from the pair.
  3. **Immediate Purge Process (At that Moment in Time)**:
     - Unlinks physical disk files (`vault_file`, `thumbnail_file`, `preview_file`) if unreferenced by other pairs.
     - Sets `EvidenceImage.is_file_pruned = True, status = "PRUNED", pruned_at = timezone.now()`.
  4. **Audit Trail**:
     - Writes `SubmissionAuditLog` with action `PAIR_DELETED`.
  5. **Query Scoping**:
     - `api_pairs_list`, `api_pair_detail` (404), and `api_stats` filter out `is_deleted=True`.
- **Response**:
  ```json
  {
    "success": true,
    "message": "Pair UBK123A deleted and files purged.",
    "purged_files": ["<image_uuid_1>", "<image_uuid_2>"]
  }
  ```

---

## Automated Verification
All endpoints and lifecycle actions verified with 100% pass rate in `core/tests/test_image_replace_and_pair_deletion.py`:
- `test_soft_delete_pair_purges_files_and_preserves_db`
- `test_replace_image_via_upload`
- `test_replace_image_via_batch_picker`
- `test_api_batch_candidates`
- `test_replace_batch_image`


## 3. Off-Conveyor (U-Turn Walk) Trajectory Alignment Fix - [COMPLETED]
- **Commit Reference**: `f589e2d`
- **Issue Resolved**:
  - In a 4-photo U-turn batch of 2 motorcycles (Bike A: Rear 1, Front 4; Bike B: Rear 2, Front 3):
    - Mobile Companion generated filenames: `uturn_rear_1.jpg`, `uturn_rear_2.jpg`, `uturn_front_1.jpg`, `uturn_front_2.jpg`.
    - Tier 1B camera shutter sequence index matching prematurely matched `uturn_front_1.jpg` (sequence 1) with `uturn_rear_1.jpg` (sequence 1), mistakenly pairing Front 3 with Rear 1 and Front 4 with Rear 2.
    - In addition, incremental upload association during active mobile photo uploads caused premature pairing before the full return walk was completed.
- **Backend Fix**:
  1. **Tier 1B Bypass**: Skip shutter sequence index matching whenever a batch or image is identified as U-Turn (`is_uturn_context`).
  2. **Turnaround Trajectory Anchoring**: In Tier 3, turnaround alignment always anchors from the physical turnaround point backwards ($R_{N-1-k} \leftrightarrow F_k$).
  3. **Natural Row Ordering**: Resolved pairs are emitted in natural row order (Row Position #1 = Bike A, Row Position #2 = Bike B).
  4. **Upload Association Throttling**: In `api_mobile_upload`, auto-association only runs when photo counts are balanced (`fronts == rears > 0`), and final sealing runs cleanly in `api_mobile_uturn_finish`.
- **Automated Verification**:
  - `TestMobileCompanion.test_uturn_pairing_aligns_turnaround_without_shutter_index_confusion` in `core/tests/test_mobile_companion.py` (100% pass).
## 4. Feature: Flexible Stock Ledger Entry & Official Report Email Dispatch - [COMPLETED]
- **Requirements Implemented**:
  1. **Count-Only Inbound Delivery Intake**:
     - Endpoint `POST /api/stock/delivery/` and service `record_delivery` now accept `plates_count` / `quantity` directly without requiring individual plate barcode scans.
     - Automatically updates inbound kits received (`kits_received_psv` or `kits_received_pmo`) and reconciles the shift.
  2. **Flexible Manual Ledger Entry & Metric Overrides**:
     - Model fields added: `override_installed`, `override_received`, `override_transfers` on `DailyStockLedger`.
     - Endpoint `POST /api/stock/ledger/entry/` and service `update_ledger_entry`:
       - Allows safe-room teams to record/override any row in the official 9-row reconciliation (Opening Balance, Kits Received, SCHEDULED, Kits Installed, Bond Transfer IN, Bond Transfer OUT, Physical Count, Notes, and Storage Bond Name).
       - Automatically recalculates closing balances using:
         $$\text{Closing Balance} = \text{Opening} + \text{Received} + \text{Transfer In} - \text{Transfer Out} - \text{Installed}$$
       - Correctly formats Daily Performance % ($0\%$, $106\%$, etc.) and Backlog Level ($\max(0, \text{Scheduled} - \text{Installed})$).
       - Generates immutable audit log in `SubmissionAuditLog` (`action=STOCK_LEDGER_UPDATED`).
  3. **Official 9-Row Reconciliation Report Email Dispatch**:
     - Endpoint `POST /api/stock/report/email/`:
       - Dispatches Uganda Flag Ribbon-branded, mobile-responsive HTML report table and ASCII text fallback.
       - Resolves recipients from user email, connected ITMS account email, or explicitly passed list.
       - Dispatches via Resend API with offline graceful fallback and records `STOCK_REPORT_EMAILED` audit log.
- **Automated Verification**:
  - `core.tests.test_stock_monitoring.StockMonitoringTests`:
    - `test_count_only_delivery_intake`: Verifies inbound delivery count intake without plate barcodes.
    - `test_direct_ledger_entry_matches_user_report`: Verifies exact matching of the user's 9-row table (AGM / SPIRO: 90/857 opening, 200 received, 300 scheduled, 317 installed -> 106% perf, 90 transfer out, 0/740 closing).
    - `test_stock_report_email_dispatch`: Verifies email dispatch, recipient resolution, and audit log.
    - Full suite: 62/62 tests passing.

## 5. Feature: Standardized CSV Reports, Spiked Kit Detection & High-Performance Streaming - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Standardized CSV Exports Across Stock & Reports Pages**:
     - Exports have distinct, clearly defined schemas without redundant data repetition:
       - Master Reconciliation (`/api/stock/export/`): Clean operational summary covering 9-row balance table, floor operations, and discrepancy counts without dumping hundreds of redundant row-level dispatch lists.
       - Dispatched Kits Export (`/api/stock/export/category/dispatched/`): Dedicated itemized listing of physically scanned-out kits.
       - Unallocated Discrepancies (`/api/stock/export/category/unallocated/`): Kits scanned out to line without active ITMS order or archive.
       - Spiked / Undispatched Kits (`/api/stock/export/category/spiked/`): Dedicated audit listing of plates appearing in ITMS orders that bypassed safe room dispatch scanning.
  2. **Spiked / Undispatched Kit Identification Logic**:
     - Integrated into `compute_daily_reconciliation` in `core/services/stock_monitoring_service.py` and `get_date_driven_plate_totals` in `core/services/report_service.py`.
     - Cross-references all scoped ITMS orders (`installed` and `pending`) against `dispatched_plates_map`.
     - Distinguishes:
       - `spiked_installed`: Plates verified and archived in ITMS without safe room dispatch scan.
       - `spiked_pending`: Plates active in installation queue without safe room dispatch scan.
     - Exposes `spiked_undispatched` in `floor_operations`, `graduated_scale`, and top-level recon dictionary.
  3. **Zero Empty Rows and CRLF Format Integrity**:
     - Diagnosed and resolved Windows text-mode `\r\r\n` duplication by enforcing direct binary writes via `.write_bytes(content.encode("utf-8-sig"))` or opening files with `newline=""` / binary mode.
     - All `csv.writer` instances explicitly specify `lineterminator="\r\n"`.
     - All exports prepend UTF-8 BOM (`\xef\xbb\xbf`) for 100% native Microsoft Excel rendering.
     - Eliminated all artificial `writer.writerow([])` spacer rows. Every exported row is structured data.
  4. **High-Performance & Scalability for 1,000+ Records**:
     - Refactored `api_export_report` (`/api/export/report/`) to use `StreamingHttpResponse` with chunked generator yielding 200-row byte chunks.
     - Replaced greedy `pairs.all()` with `.only(...)` and `.iterator(chunk_size=1000)`.
     - Replaced loop-based N+1 count queries in `report_service.py` (`get_system_totals`) with single conditional database aggregations (`Count(..., filter=Q(...))`).
- **Automated Verification**:
  - `core.tests.test_report_exports.ReportExportsAndSpikedKitTests`:
    - `test_spiked_undispatched_kit_detection`: 100% pass.
    - `test_spiked_csv_export_content_and_headers`: 100% pass.
    - `test_zero_empty_rows_and_crlf_format`: 100% pass.
    - `test_streaming_export_with_1000_records`: 100% pass.


## 6. Feature: Centralized Shift Date Backend Architecture & "ALL" Mode Support - [COMPLETED]
- **Core Requirements Implemented**:
  1. **"ALL" Mode / Suffix Backend Support in Date Service**:
     - Updated `core/services/date_service.py` (`get_active_shift_info`):
       - Recognizes `mode == "all"` or `manual_date.upper() == "ALL"`.
       - Returns dictionary payload:
         ```python
         {
             "mode": "all",
             "manual_date": "ALL",
             "date": "ALL",
             "suffix": "ALL",
             "formatted_date": "All Dates",
             "display": "All Dates (Aggregate)",
             "is_today": False,
         }
         ```
  2. **Global Shift Selection API Endpoint Update**:
     - Updated `core/views/system.py` (`api_shift_date`):
       - Accepts `POST /api/settings/shift-date/` with `{ "target_date": "ALL" }`.
       - Persists `sync.shift_target_date_mode="all"` and `sync.manual_target_date="ALL"` in `GlobalConfigService`.
       - Emits standardized JSON payload containing `active_shift` and `available_dates`.
  3. **Universal Fallback Across All Operational Endpoints**:
     - `/api/stock/reconcile/`, `/api/reports/totals/`, `/api/export/report/`, `/api/stock/export/` all fall back cleanly to `date_service.get_active_shift_info()` when no `?date=` or `?suffix=` query parameter is supplied by the frontend.
     - Preserves full backward compatibility for explicit parameter calls.
- **Automated Verification**:
  - `core.tests.test_date_service.DateServiceTestCase`:
    - Verified `test_api_shift_date_get_and_post`:
      - `POST {"target_date": "yesterday"}` -> 200 OK, mode="yesterday"
      - `POST {"target_date": "today"}` -> 200 OK, mode="today"
      - `POST {"target_date": "ALL"}` -> 200 OK, mode="all", suffix="ALL", formatted_date="All Dates".
    - Full test suite passed in 1.86s.


## 7. Feature: Comprehensive Email Dispatch & Audit Trail Success/Failure Telemetry - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Explicit Success/Failure Audit Logging for Email Dispatch**:
     - Updated `api_stock_report_email` in `core/views/bond.py`:
       - On success: Creates `SubmissionAuditLog` with `action=STOCK_REPORT_EMAILED`, `result=ResultStatus.SUCCESS`, `simulated_token=work_suffix`, and operator context.
       - On failure / API rejection: Creates `SubmissionAuditLog` with `action=STOCK_REPORT_EMAILED`, `result=ResultStatus.FAILURE`, detailed failure message, and operator context.
       - On exception / timeout: Creates `SubmissionAuditLog` with `action=STOCK_REPORT_EMAILED`, `result=ResultStatus.FAILURE`, exception trace, and operator context.
       - On empty recipient list: Creates `SubmissionAuditLog` with `action=STOCK_REPORT_EMAILED`, `result=ResultStatus.FAILURE`.
  2. **Audit Logging for Stock Ledger Overrides**:
     - Updated `update_ledger_entry` in `core/services/stock_monitoring_service.py` to record `result=ResultStatus.SUCCESS` and `simulated_token=suffix`.
  3. **History API & TUI Formatting Enhancements**:
     - Updated `api_history_list` in `core/views/dashboard.py`:
       - Supports `?filter=EMAIL` to isolate email dispatches.
       - Maps unlinked pair logs (`STOCK_REPORT_EMAILED`, `STOCK_LEDGER_UPDATED`) to informative plate/order tokens (`SHIFT_<suffix>` and `EMAIL_DISPATCH` / `LEDGER_OVERRIDE`) instead of generic `"N/A"`.
       - Formats `result` cleanly so frontend renders green `badge-green` for `SUCCESS` and red `badge-red` for `FAILURE`.
     - Updated `_reload_history_table` in `core/tui/tables.py` to preserve unlinked audit logs when filtering by active account.
- **Automated Verification**:
  - `core.tests.test_stock_monitoring.StockMonitoringTests`:
    - `test_stock_report_email_dispatch`: Verifies `SUCCESS` audit logging.
    - `test_stock_report_email_dispatch_failure_and_history_api`: Verifies `FAILURE` audit logging and History API payload.
    - Full suite passing.


## 8. Feature: Factory Reset & Production Data Cleanup Command (`clear_data --factory-reset`) - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Master `--factory-reset` Atomic Execution**:
     - Deletes all operational and relational records in cascade-safe dependency order:
       - `SubmissionAuditLog`
       - `VehicleInstallationPair`
       - `EvidenceImage`
       - `IngestionBatch`
       - `InstallationOrder`
       - `InstallationKit`
       - Stock ledger models (`StockDelivery`, `StockDispatchScan`, `StockReturnScan`, `DailyStockLedger`, `FacilityMetadata`, `PhysicalStockAudit`)
     - Safely unlinks and purges all disk artifacts in `media/vault/`, `media/crops/`, and exported shift reports in `exports/`.
  2. **Account Retention & Non-Admin Cleansing (`--keep-admin-only`)**:
     - Preserves the primary superuser `admin` account intact.
     - Safely deletes test non-admin user accounts along with associated `OperatorProfile`, `OperatorITMSSession`, and active `Session` records.
  3. **Granular Flags for Targeted Maintenance**:
     - `--include-orders`: Clears installation orders.
     - `--clear-kits`: Clears installation kits.
     - `--clear-stock`: Clears deliveries, dispatches, returns, daily ledgers, and facility metadata.
     - `--clear-exports`: Clears exported CSV files in `exports/`.
     - `--keep-admin-only`: Purges non-admin accounts while keeping superuser `admin`.
  4. **Auto-Sync Daemon Safeguards**:
     - Setting `sync.auto_sync_enabled = False` in `secure/config.json` prevents background `MorningKitSyncDaemon` and `ShiftOrderSyncDaemon` from re-pulling external kits/orders upon service restart, preserving pristine zero-record factory state.
- **Verification & Deployment**:
  - Validated via atomic transaction execution on PostgreSQL production environment (`16.170.39.187`). Verified table counts: Orders: 0, Kits: 0, Stock: 0, Pairs: 0, Batches: 0, Images: 0, Users: `[('admin', True)]`.


## 9. Feature: Corporate Sender Identity & Notification Email Format - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Professional Brand Sender Identity**:
     - Migrated notification sender configuration from generic `auth@info.kirex.online` to `Close Copilot <support@info.kirex.online>` / institutional corporate notification format.
     - Display name formatted cleanly as `Close Copilot` across all automated shift reports, 2FA security codes, and diagnostic dispatches.
  2. **Domain Compliance & Anti-Spam**:
     - Ensures sender headers adhere to SPF/DKIM policies for `info.kirex.online`.


## 10. Bugfix: Frontend History Filter & Script Identifier Declaration - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Missing Filter Handler Resolution**:
     - Implemented `window.setHistoryFilter(filter)` in `tab_history.js` and bound it to history tab pill buttons (`tab_history.html`), resolving browser `ReferenceError: setHistoryFilter is not defined`.
  2. **Duplicate Identifier Declaration Fix**:
     - Fixed duplicate `let currentHistoryFilter` declaration in `tab_history.js` that caused `SyntaxError: Identifier 'currentHistoryFilter' has already been declared`.


## 11. Feature: Two-Way Direct Single-Order Installation & 1-Click ITMS Submission API - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Two-Way Flexible Workflow Support**:
     - Provides operators dealing with single walk-in bikes or individual orders a rapid, direct installation path directly from the Orders tab or Mobile Companion, eliminating the need to wait for bulk photo batching or switch to the external `stock.itms.ug` portal.
  2. **Endpoint Architecture (`/api/itms/orders/<order_ident>/install/`)**:
     - `GET /api/itms/orders/<order_ident>/install/`:
       - Live detection of current order stage on ITMS (`STAGE_1_INSTALLATION`, `STAGE_2_EVIDENCE`, `STAGE_3_CONFIRMATION`, `STAGE_ARCHIVED`).
       - Returns assigned hardware serials (`front_plate_serial`, `rear_plate_serial`, `gps_tracker_id`) and existing attached `VehicleInstallationPair` / evidence photo preview URLs.
     - `POST /api/itms/orders/<order_ident>/install/`:
       - `action="save_step1"`: Submits Step 1 hardware fitment (`front_plate_id`, `rear_plate_id`, `tracker_id`) to ITMS, advancing order stage to `STAGE_2_EVIDENCE`.
       - `action="attach_photos"`: Ingests direct single-order photo uploads (`front_photo`, `rear_photo`), runs vision/OCR parsing, links to `VehicleInstallationPair`.
       - `action="submit"` (default): 1-click or multi-step submission driving the full ITMS workflow (Step 1 -> Step 2 -> Step 3 finalize), marking the pair as `SUBMITTED`, updating local order status, and logging append-only `SubmissionAuditLog`.
  3. **Backward Compatibility & Sandbox Safety**:
     - Supports `dry_run` flag (`true`/`false`) override in POST body or fallback to system configuration `submission.dry_run_mode`.
     - Completely non-disruptive to the existing bulk batch processing pipeline.
  4. **Automated Verification**:
     - Unit test suite implemented in `core/tests/test_direct_order_install.py` with 100% pass rate (6/6 tests passing).

