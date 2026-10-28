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



## 12. Feature: Mobile Companion Off-Conveyor (U-Turn Yard Walk) Session Resilience & Pair Protection - [COMPLETED]
- **Core Requirements Implemented**:
  1. **U-Turn Session Status API (`GET /api/mobile/uturn/session_status/`)**:
     - Accepts `session_id` (`uturn_session_id`).
     - Returns verified photo counts (`rear_count`, `front_count`, `total_photos`), matched pair counts, and batch existence so the mobile companion can reconcile and restore active walks seamlessly upon browser reload.
  2. **U-Turn Walk Discard & Abort API (`POST /api/mobile/uturn/abort_session/`)**:
     - Safely deletes unsubmitted incomplete pairs and unsubmitted evidence images associated with an aborted session.
     - Relabels batch as `[ABORTED]` or cleans up empty batches, guaranteeing zero orphaned or corrupt pairs remain in the database.
  3. **Batch Completeness Validation on `POST /api/mobile/uturn/finish_batch/`**:
     - Prevents sealing an incomplete batch if only REAR photos (0 FRONTS) or only FRONT photos (0 REARS) were uploaded, returning `400 Bad Request` with `MISSING_FRONT_PHOTOS` / `MISSING_REAR_PHOTOS`.
     - Supports explicit `allow_incomplete: true` flag for administrative single-orientation batch sealing.
     - Triggers reverse-trajectory U-turn association and starts the vision pipeline.
  4. **Premature Incomplete Pair Protection in Association Engine (`core/matcher/association.py`)**:
     - In `_associate_batch_sequences`: If a batch is an Off-Conveyor U-Turn walk (`is_uturn_walk`) and has photos from only a single orientation (`total_batch_fronts == 0` or `total_batch_rears == 0`), skips creating incomplete `VehicleInstallationPair` records.
     - Preserves evidence images as unassociated candidates until the companion return walk completes.
  5. **Automated Verification**:
     - Unit test suite in `core/tests/test_mobile_companion.py` updated with tests for status check, session abort, and single-orientation finish blocking with 100% pass rate (19/19 tests passing).


## 13. Feature: Strict Pending Order Linking & Automatic Conflict Flagging - [COMPLETED]
- **Core Requirements Implemented**:
  1. **Strict Pending Order Scoping (`get_pending_orders_queryset`)**:
     - Order matching and linking across the entire backend (`order_matcher.py`, `prior_guided.py`, `association.py`, `views/pairs.py`, `tui/actions.py`) is now strictly restricted to orders awaiting installation evidence.
     - Specifically enforces:
       - `status == InstallationOrder.Status.PENDING`
       - `is_archived == False`
       - `is_active_on_itms == True`
       - `order_status` excluded from `["Installed", "Ready for approve", "Closed", "Archived"]`
       - `itms_stage` excluded from `["STAGE_2_APPROVE", "STAGE_3_CONFIRMATION", "ARCHIVED"]`
       - Excludes orders already fulfilled by an existing submitted pair (`pairs__verification_status=SUBMITTED`).
  2. **Historical Submitted Pair Protection (`core/matcher/association.py`)**:
     - `_resolve_group()` explicitly excludes pairs with `verification_status == SUBMITTED` when looking up or creating pairs for a plate.
     - Freshly uploaded photos for previously submitted vehicles now create brand new `VehicleInstallationPair` records rather than overwriting historical evidence or falsely showing under the "Done" (Submitted) tab.
  3. **Automatic Conflict Flagging (`verification_status = CONFLICT`)**:
     - When a complete pair has no active pending order available in the ITMS registry (e.g. order is already installed/closed, or vehicle plate was never registered on ITMS), the pair is automatically assigned `verification_status = CONFLICT` with `order = None`.
     - Populates explanatory operator notes:
       - If order was previously closed/installed: `Order Conflict: Order #... for plate '...' is already Installed (closed/submitted). No active pending order available.`
       - If order does not exist: `Order Conflict: No pending installation order found for plate '...'. Vehicle plate not in active pending order list.`
  4. **API & Operator Decision Guardrails (`core/views/pairs.py` & `core/tui/actions.py`)**:
     - `POST /api/pairs/<pair_id>/action/` with `action="approve"` validates that `pair.order` is strictly pending. If no pending order exists, rejects with HTTP 400 (`NO_PENDING_ORDER`) and flags the pair as `CONFLICT`.
     - `action="link_order"` / `action="edit_plate"` verifies that the target order is strictly pending, rejecting closed/installed orders with HTTP 400 (`ORDER_NOT_PENDING`).
     - `api_batch_submit()` strictly excludes pairs whose linked order is not in `get_pending_orders_queryset()`.
  5. **Automated Verification**:
     - Regression tests added in `core/tests/test_association.py` (`test_front_and_rear_without_pending_order_marks_conflict`, `test_photos_matching_closed_order_flagged_conflict_and_not_linked`, `test_order_not_available_flagged_conflict`, `test_closed_order_flagged_conflict`) with 100% pass rate (12/12 passing).


### Backend Update: Web UI Direct Upload Vision Pipeline, TUI Order Sync Cadence & Supervisor Documentation (27 October 2026)
- **Status:** COMPLETED & TESTED (44/44 Unit Tests Passing)
- **Implemented Changes:**
  1. **Web UI Direct Photo Upload ALPR Processing (`core/views/upload.py`):**
     - Resolved root cause where directly uploaded photos in Web UI / API lacked front/rear matching because vision/ALPR processing was previously bypassed.
     - In both `upload_photos_view` and `api_upload_photos`, all newly ingested `EvidenceImage` objects now immediately execute `vault_service.process_single_image_vision(img)`.
     - YOLO plate detection, OCR extraction, and Ugandan plate normalization populate `detected_plate` before pairwise association executes.
     - Following association, `order_matcher.match_all_pending_pairs()` is executed to immediately link complete pairs to pending fitment orders.
  2. **TUI & Configuration Order Sync Cadence Control (`core/services/config_service.py`, `core/tui/settings_pane.py`):**
     - Registered `sync.order_sync_interval_seconds` in `DEFAULT_CONFIG["sync"]` (default 600s / 10m) and `USER_SETTINGS_SCHEMA`.
     - In TUI Settings Pane (`core/tui/settings_pane.py`), added interactive input `#input-order-sync-interval` in Card 1B (Shift Synchronization & Reconciliation).
     - Saving settings immediately invokes `ShiftOrderSyncDaemon.get_instance(interval_seconds=...)` to update the live running background daemon without restarting.
  3. **Supervisor Documentation (`docs/APP_OVERVIEW.md`):**
     - Completely overhauled `docs/APP_OVERVIEW.md` to reflect current project status, live AWS EC2 production deployment, direct ALPR vision engine, 4-tier association, 9-row ledger arithmetic, mobile companion, and SOP workflows.
  4. **Regression Tests (`core/tests/test_web_upload_and_settings.py`):**
     - Added test suite verifying vision invocation on upload, symmetric complete pair creation, automatic order linking, and schema validation.


### Backend Update: Warehouse Model, Facility Sync Metadata & ITMS Account Linkage (28 October 2026)
- **Status:** COMPLETED & TESTED (4/4 Warehouse Sync Tests Passing, 0 System Check Issues)
- **Root Cause Identified & Fixed:**
  - The warehouse table was missing from the local database schema, relying only on transient JSON values in `config.json`.
  - The crawler previously queried `/installation-orders/index` for `<select id="warehousesearch-id">`, which was populated client-side via Select2 AJAX (`/warehouses/ajax-search?status[0]=10&status[1]=9`). Static HTML scraping returned zero elements, silently falling back to dummy IDs (`"24"`, `"22"`).
- **Implemented Architecture Changes:**
  1. **New Database Model `Warehouse` (`core/models.py` & Migration `0028`):**
     - Stores `warehouse_uuid` (unique, indexed), normalized `code` (e.g. `AGM`, `AGM_STORAGE`), full facility `name`, `address`, `city`, `zip_code`, `company` (`VGS LTD`), `designation_number`, `status` (`Active`), `group_name` (`MC DEALER`), `itms_url`, `created_date_itms`, and `is_active_bond`.
     - Registered with full list displays, filtering, and search in Django Admin (`core/admin.py`).
  2. **Facility Sync Metadata & Session Linkage (`core/models.py`, `core/services/scoped_order_service.py`):**
     - Enhanced `FacilitySyncMetadata` with `warehouse_uuid`, `warehouse_name`, and foreign key `warehouse = ForeignKey(Warehouse)`.
     - Enhanced `OperatorITMSSession` with `active_warehouse_uuid` and `active_warehouse = ForeignKey(Warehouse)`.
     - Auto-links `FacilitySyncMetadata` and `OperatorITMSSession` so the system accurately reflects which physical warehouse the ITMS account and facility sync caches are attached to.
  3. **Direct ITMS Web Scraper & API Crawler (`core/services/itms_web_client.py`):**
     - Implemented `parse_warehouses_html(html)` targeting `https://stock.itms.ug/warehouses` and parsing `#w4` grid view table rows (`data-key` UUID, transaction URLs, and columns 0 through 9).
     - Added Select2 AJAX endpoint querying (`/warehouses/ajax-search?status[0]=10&status[1]=9`) with `X-Requested-With: XMLHttpRequest` headers to enrich facility discovery.
     - Automatically calls `fetch_warehouses_from_itms()` on successful login (`client.login()`), persisting facilities directly to the database without manual user intervention.
  4. **Bond Service Source of Truth (`core/services/bond_service.py` & `core/views/orders.py`):**
     - `get_all_discovered_warehouses()` now queries `Warehouse.objects.all()` as the primary database source of truth.
     - `POST /api/itms/warehouses/` atomically updates `Warehouse.is_active_bond`, syncs `FacilitySyncMetadata`, and binds the active warehouse to the user's `OperatorITMSSession`.
  5. **Regression Verification (`core/tests/test_warehouse_sync.py`):**
     - Comprehensive unit test suite added covering HTML parsing, database upserts, foreign key linkage, `bond_service` priority, and REST API switching.



### Backend Update: Dynamic TUI Kit Bounds & Quick Install Submission Optimization (28 October 2026)
- **Status:** COMPLETED, TESTED & DEPLOYED (13/13 Quick Install & Sync Tests Passing, Verified Live on EC2 Production)
- **Bottlenecks & Failures Resolved:**
  1. **TUI Kit Crawl Bound Clamping (Clamped to 100 instead of Configured 200 Pages):**
     - *Root Cause:* In `core/services/order_sync.py`, `safe_max_pages = max(1, min(int(max_pages or 50), 100))` enforced a hardcoded ceiling of 100 pages, ignoring user settings above 100. Furthermore, background shift loops and TUI buttons in `itms_pane.py` passed hardcoded values (35 or 100).
     - *Resolution:* Removed the 100-page clamp and raised the ceiling to 1000 pages across `order_sync.py`, `kit_provisioning_service.py`, and `itms_web_client.py`. All callers now default to `max_pages=None`, dynamically loading `config_service.get_setting("crawl.max_kit_pages", 100)`.
  2. **TUI Sync Intervals Control & Daemon Dynamic Cadence:**
     - *Root Cause:* `ShiftOrderSyncDaemon` only initialized intervals at boot and had a static 180s tick loop, ignoring changes made to order sync intervals in the TUI.
     - *Resolution:* Added interactive control for `sync.kit_sync_interval_seconds` in TUI Settings Pane (`core/tui/settings_pane.py`), alongside `sync.order_sync_interval_seconds`. Daemon loops now dynamically reload interval settings from `config_service` on every heartbeat and adapt their sleep ticks dynamically (`min(ACTIVE_TICK, orders_interval)`).
  3. **Quick Install Submission Freeze on Photo Submit:**
     - *Root Cause:* Direct order installation was running full YOLOv8 plate detection and multi-pass OCR synchronously on CPU for both photos inside the HTTP POST thread (15–20s delay), followed by concurrency lock blocking (`blocking=False`), and an unyielding Step 4 archive verification loop (6–10s delay). If archive indexing on ITMS took slightly longer than 6 seconds, the backend reported failure even after Step 3 had successfully finalized on ITMS.
     - *Resolution:*
       - Fast-tracked vision for known direct install orders: sets plate to `order.registration_number` with `Status.PLATE_DETECTED` and verified orientation without running heavy CPU OCR.
       - Disabled redundant final-stage compression during upload since incoming mobile companion and web UI direct uploads are already compressed.
       - Auto-fills hardware serials from matching `InstallationKit` in `POST` if omitted in the request.
       - Set `blocking_lock=True, lock_timeout=15.0` to eliminate lock contention failures.
       - If Step 3 confirmation succeeded on ITMS, the workflow returns success (`pending_archive_confirmation=True`), transitioning the order and pair to `SUBMITTED` while background daemons verify final archive indexing.
