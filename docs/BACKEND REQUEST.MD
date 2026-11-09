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


### Backend & UI/UX Update: Centralized Live ITMS Sync, One-Tap Refresh & Mobile Connect Responsiveness (08 November 2026)
- **Status:** COMPLETED, TESTED & DEPLOYED (Commit `fe5bd7c`, Deployed Live on EC2 Production `16.170.39.187`)
- **Key Enhancements Implemented:**
  1. **Purged Local DB Toggle (Web UI Exclusively Focused on Live ITMS):**
     - Removed the confusing `⚡ Live ITMS` vs `💾 Local DB` toggle switch from `#itms-subview-controls`.
     - Locked `itmsExplorerSource = "live"` permanently in frontend JavaScript, ensuring all registry lookups hit the live ITMS feed with zero user confusion.
  2. **Single-Tap Consolidated ITMS Synchronization (`syncItmsAll` & `POST /api/itms/orders/sync/now/?tab=all`):**
     - Removed the multi-button sync drawer (`#itms-filter-drawer`, `Sync Shift Orders`, `Auto-Sync Kits`, `Sync Page`).
     - Enhanced `core/views/orders.py:api_itms_sync_now` with `tab in ("all", "everything")` to trigger unified shift-scoped sync (active + completed archive) AND installation kits crawl in a single execution.
     - Provided a single, prominent `⚡ Sync ITMS` button in `#itms-subview-controls` with live spinner and feedback.
  3. **Centralized One-Tap Refresh (`refreshItmsTab`):**
     - Added unified `🔄 Refresh` button that simultaneously checks and reloads ITMS credentials session status, active bonded warehouse, current subview records (Orders / Archive / Kits), and selected order telematics detail card.
  4. **Full Mobile Responsiveness for ITMS Connect View (`#itms-view-connect`):**
     - Fixed clipping and scroll lock in `.itms-connect-view` by adding `min-height: 0`, `-webkit-overflow-scrolling: touch`, and `100dvh` viewport adaptation.
     - Added `.itms-connect-bond-row` with vertical stacking on screens $\le 768\text{px}$ to prevent badge and label collisions.
     - Added `.itms-connect-btn-group` that stacks action buttons vertically with 44px minimum touch targets on mobile.
     - Enforced `font-size: 16px` on mobile inputs to eliminate iOS Safari auto-zoom issues.

### Backend & UI/UX Update: Queue Order Linking, High-Precision Search & Centralized Filters (09 November 2026)
- **Status:** COMPLETED & TESTED (36/36 Unit Tests Passing)
- **Key Enhancements Implemented:**
  1. **Fix Order Linking & Conflict Approval Failures:**
     - Fixed `core/views/pairs.py:api_pair_action`:
       - Removed hard exclusion of `"Ready for approve"` and excessive status gates in manual order linking (`action == "link_order"`). Allowed linking of valid open orders regardless of transient status strings.
       - Enhanced `action == "approve"`: when an operator approves a complete pair in `CONFLICT` or without an order, automatically attempts auto-linking against local open orders or via live ITMS fetch, promoting complete pairs directly to `APPROVED`.
       - Added automatic background rematching in `core/services/order_sync.py:sync_shift_scoped`: newly synced orders automatically rematch and promote unlinked `CONFLICT` pairs to `APPROVED` without requiring manual operator intervention.
       - Preserved 1:1 order assignment integrity: prevent hijacking of orders already claimed by other active or submitted pairs.
  2. **Refined Order Search Precision & Prevent False Information:**
     - In `core/services/scoped_order_service.py` and `core/views/orders.py`:
       - Numeric search queries (`s.isdigit() and len(s) < 6`, e.g. `600`) now filter strictly against `registration_number` and `order_number`. Substring searches inside 17-character VINs or 9-digit plate serials are prevented from returning false positive matches.
       - Implemented relevance ranking: exact match (score 0), prefix match (score 1), and contains match (score 2).
       - Live search verification: live ITMS fallback records are verified to actually match the query before being added to results.
       - Clean empty state: when no order exists, the endpoint returns `success: true, orders: []`, and the UI displays a clear `⚠️ Order not available` state with actionable tips instead of false results.
  3. **Centralized Queue Controls Toolbar & Unambiguous Pills:**
     - Replaced the fragmented collapsible filter drawer, redundant scope pills (`Today`, `Active`, `Carryover`, `All`), and separate batch dropdown with a clean, centralized 3-tier toolbar:
       - **Tier 1 (Status Navigation)**: Clean, unambiguous text pills without confusing counts: `All`, `Review`, `Approved`, `⚠️ Issues`, and `Done`.
       - **Tier 2 (Action Toolbar)**: Search input with clear `✕` button (`#queue-search-clear`), unified Scope & Ingestion Batch selector (`#queue-batch-select`), refresh (`🔄`), and CSV export (`📥`).
       - **Tier 3 (Batch Action Bar)**: Live vision pipeline indicator, approved count summary, and quick submit button (`🚀 Submit Approved (N) [B]`).
     - Added CSS rules in `core/static/core/css/tab_queue.css` for responsive stacking and touch targets. Bumped cache-busting version to `v=29` in `base.html`.
  4. **Linear Top-to-Bottom Auto-Progression Upon Pair Approval:**
     - In `core/views/pairs.py:api_pairs_list`: Enforced deterministic chronological ordering `qs.order_by("created_at", "id")`, preventing pairs from randomly reordering whenever `updated_at` was bumped.
     - In `core/static/core/js/tab_queue.js`: Updated auto-advance upon approval to strictly move downwards (`currentIdx + 1` to end of list) prioritizing the next pending pair, with automatic scroll-into-view.
  5. **Submission Modal Batch Scoping & Infinite Note Fix:**
     - In `core/static/core/js/pipeline.js:triggerBatchSubmit`: Strictly constrained pairs and conflict counts to the active batch filter (`currentQueueBatchFilter`). Eliminated false discrepancy warnings originating from other batches or historical records.
     - In `core/matcher/order_matcher.py`: Fixed the repetitive string concatenation bug (`f"{existing_note} | {conflict_msg}"`) that was appending duplicate messages on every shift sync cycle. Appropriately formatted placeholder plate IDs (`PAIR...` / `MISSING...`). Sanitized bloated existing notes in the database.

### Backend & UI/UX Update: Stock Tab Architectural Separation, Distinct Services, Centralized Refresh, Ledger Deduplication & Context-Aware Excel Paste (09 November 2026)
- **Status:** COMPLETED & TESTED (13/13 Tests Passing in `test_stock_services.py`, `test_stock_counting_and_queue.py`, and `test_stock_shift_details_and_tui.py`)
- **Key Enhancements Implemented:**
  1. **Architectural Separation into Two Distinct Domain Services:**
     - **Service 1: Stock Checking & Daily Ledger Service (`core/services/stock_checking_service.py`)**:
       - Encapsulates warehouse inventory validation (`validate_plates_stock_availability`).
       - Calculates 9-row daily shift ledger balances (`calculate_shift_balances`): Opening Balance + Receipts + Transfer In - Transfer Out - Installed = Closing Balance, Physical Audit Count, and Variance.
       - Manages ledger manual entry and override persistence (`update_ledger_entry`).
       - Implements automated shift balance carryover (`auto_carry_previous_closing`).
     - **Service 2: Kit Unallocation & Misallocation Monitoring Service (`core/services/kit_unallocation_service.py`)**:
       - Enforces **Physical Dispatches as Ground Truth**: Treats safe-room physically scanned/pasted kits as the source of truth for shift floor issuance.
       - Detects **Unallocated Floor Kits (`get_unallocated_kits`)**: Isolates kits physically dispatched that were never returned to safe room and never appeared in ITMS orders or archives for that shift, differentiating between kits with local photo evidence (Pending System Sync) and unresolved floor discrepancies.
       - Detects **Misallocated Kits by MVR (`get_misallocated_kits`)**: Detects clerical errors made during ITMS order creation/entry:
         - *Hardware Serial Mismatches*: Front/rear plate serial, GPS tracker, or BLE beacons in `InstallationOrder` differing from registered `InstallationKit` stock.
         - *Category Mismatches*: Dispatched as PSV White but allocated as PMO Yellow (or vice-versa).
         - *Warehouse Mismatches*: Dispatched under AGM Bond but allocated under a foreign facility in ITMS.
         - *Duplicate Allocations*: Same registration plate assigned across multiple active orders.
       - Detects **Spiked / Undispatched Kits (`get_spiked_undispatched_kits`)**: ITMS orders that were fitted on line without safe-room dispatch scans.
       - Safe Auto-Ingestion (`auto_ingest_shift_orders_to_dispatch`): Ingests unscanned orders/archives into `StockDispatchScan` with `AUTO_ORDER_DISPATCH_NOTE` to track fitted daily totals while ensuring zero collision or corruption of unallocated kit monitoring.
       - Generates comprehensive MVR Action Exception Dockets (`generate_mvr_docket`).
  2. **Deduplicate Ledger Editing & Unified 9-Row Modal:**
     - Removed redundant and competing `#stock-target-modal` from `tab_stock.html`.
     - Replaced duplicate buttons (`✏️ Edit Ledger (9-Row Overrides)` and `✎ Opening & Target`) with a single, clear button: `✏️ Edit Ledger (O)`.
     - Built comprehensive `#modal-edit-ledger` supporting Opening Balances, Target Quotas, Auto-Carry Previous Closing, Advance Overrides (Receipts, Installed, Transfers, Closing), Physical Audit Count, and Shift Handover Remarks in a single unified view.
  3. **Centralized Atomic One-Tap Refresh (`refreshStockTab`):**
     - Added `#btn-refresh-stock-tab` in `#pane-stock` header actions next to the shift date.
     - Single tap atomically triggers:
       - Live daily reconciliation recalculation (`/api/stock/reconciliation/?date_suffix=...`)
       - Shift dispatches, movements, and audit log reload (`/api/stock/shift/details/?date_suffix=...`)
       - Blocked / not-on-stock live recheck (`/api/stock/blocked-plates/?date_suffix=...`)
       - Instant UI KPI cards, status badges, and table refresh with rotating icon and toast feedback.
  4. **High-Visibility Display of Kits Not On Stock:**
     - Added in-memory filter pills above the morning dispatches table: `All Dispatched`, `✓ In Stock`, `⚠️ Not On Stock`, and `❌ Unallocated Floor`.
     - Enables instant one-click filtering of not-on-stock kits directly in the table with red badge indicators.
     - Enhanced `#btn-view-blocked-stock` in the universal command bar with high-contrast warning styling.
  5. **Context-Aware "Paste from Excel" Per Active Subtab:**
     - Dynamically configures `#stock-paste-modal` according to `currentStockMode`:
       - **Dispatch Subtab**: Titled "Paste Dispatched Kits from Excel", includes "Check Stock Only" and "Dispatch Plates" actions.
       - **Movements Subtab**: Titled "Paste Stock Movements from Excel", dynamically presents explicit movement operations (Inbound Delivery / Delivered Kits, Line Returns, Bond Transfer In) with partner/reference fields.
       - **Safe Audit Subtab**: Titled "Paste Safe Room Physical Audit from Excel", records direct physical inventory counts.



### Backend & UI/UX Update: Reports Tab Operational Stage Metrics, Scanned vs Skipped Discrepancy Tracking, and Centralized Clean CSV Exports (09 November 2026)
- **Status:** COMPLETED & TESTED (7/7 Tests Passing in `test_report_exports.py`, 22/22 in `test_stock_services.py` & `test_reports_and_lifecycle.py`)
- **Key Enhancements Implemented:**
  1. **Dynamic Order Stages Breakdown Across All Given Stages:**
     - In `core/services/report_service.py`: Implemented exact SQL aggregations across all orders (`all_stages: Dict[str, int]`) and pending orders (`pending_stages: Dict[str, int]`) grouping by `order_status` and `itms_stage`.
     - Eliminated arbitrary 100-item query limits, providing 100% accurate counts of orders across all lifecycle stages (`Ready for installation`, `Under installation`, `Ready for approve`, `Installed`, `Archived`).
     - Added dynamic stage pill badges in `tab_reports.html` (`#report-cat2-stages-pills`) rendered in real time.
  2. **Scanned vs Skipped Problem Identifier & Discrepancy Tracking:**
     - In `core/services/report_service.py`: Implemented two-way operational discrepancy monitoring:
       - **Total Scanned**: Quantifies unique canonical plates captured via camera evidence (`VehicleInstallationPair` / `EvidenceImage`) and physical safe-room dispatch scans (`StockDispatchScan`).
       - **Total Skipped / Problems**: Isolates critical floor discrepancies:
         - *Skipped Safe-Room Scan (Spiked on Line)*: Orders installed or in active queue that bypassed safe-room barcode scanning.
         - *Skipped Camera Evidence*: ITMS orders marked installed or archived that lack front/rear camera verification photos.
     - Created Category 4 KPI Card in `tab_reports.html` and a dedicated **Scanned vs Skipped Problem Identifier Audit Table** (`#report-skipped-tbody`) with real-time text filter and clipboard copy (`copySkippedRawPlates`).
  3. **Centralized & Clean CSV Export Hub (`/api/export/report/`):**
     - Enhanced `core/views/system.py:api_export_report` with `type` routing:
       - `type=master`: Streaming CSV of all verification pairs with OCR scores, match confidence, VIN, and photo paths.
       - `type=stages`: Streaming CSV of all installation orders across all stages with officer, date, photo flags, and sync timestamps.
       - `type=scanned_vs_skipped`: Dedicated discrepancy CSV clearly stating problem classification, stage, safe-room scan status, photo evidence status, and recommended actions.
       - `type=unallocated`: Detailed floor unallocated kit list.
       - `type=archived`: Completed archive orders.
       - `type=pending`: Active pending orders.
     - Guaranteed Excel compatibility: UTF-8 BOM (`\xef\xbb\xbf`), standard CRLF line terminators, zero duplicate blank rows, chunked streaming for 10,000+ records.
  4. **Strict Isolation of Stock Tab Exports:**
     - Cleaned `#stock-export-dropdown` in `tab_stock.html` to strictly export warehouse inventory data:
       - Daily Reconciliation Ledger CSV (`/api/stock/export/`)
       - Safe Room Dispatches CSV (`/api/stock/export/category/dispatched/`)
       - Inbound Deliveries & Movements CSV (`/api/stock/export/category/movements/`)
       - Safe Room Physical Count Audit CSV (`/api/stock/export/category/audit/`)
       - Safe Room Unregistered Plates CSV (`/api/stock/export/unregistered/`)
       - Blocked / Not On Stock CSV (`/api/stock/export/category/blocked/`)
     - Removed all order stage and verification exports from the Stock Tab, redirecting operational reporting strictly to the Reports Tab.


### Backend & UI/UX Update: History Tab Distinct Audit Filters, Evidence Photo Thumbnails & Lightbox Integration (09 November 2026)
- **Status:** COMPLETED & TESTED (5/5 Dedicated Tests Passing in `test_history_tab.py`)
- **Key Enhancements Implemented:**
  1. **Distinct, Comprehensive Audit Trail Filters:**
     - Replaced confusing, duplicate filters (`Failed` vs `Issues / Conflicts`, `Stock & Ledger` vs `Emails`, and missing `Approvals` / `Overrides`) with 6 distinct audit event categories:
       - **All Events (`ALL`)**: Complete append-only audit trail and installation lifecycle events.
       - **Submissions (`SUBMISSIONS`)**: Transmissions to ITMS (`action in ["SUBMIT", "ITMS_SUBMISSION"]` or `verification_status == "SUBMITTED"`).
       - **Approvals (`APPROVALS`)**: Verified and approved pairs (`action in ["PAIR_APPROVAL", "OPERATOR_APPROVE", "APPROVE"]` or `verification_status == "APPROVED"`).
       - **Overrides (`OVERRIDES`)**: Manual plate overrides, swaps, replacements, and hypothesis corrections (`action in ["OPERATOR_OVERRIDE", "MANUAL_PLATE_ASSIGN", "OPERATOR_SWAP", "ORDER_PRIOR_CORRECT", "IMAGE_REPLACED"]` or `is_manual_override == True`).
       - **Issues & Fails (`ISSUES`)**: Submission errors, OCR conflicts, validation rejections, and incomplete pairs (`result == "FAILURE"` or `verification_status in ["CONFLICT", "FAILED", "INCOMPLETE", "UNREGISTERED"]`).
       - **Stock & Audits (`STOCK`)**: Safe-room physical counts, shift ledger updates, and email reports (`action in ["STOCK_LEDGER_UPDATED", "STOCK_REPORT_EMAILED"]` or plate starting with `SHIFT_`).
     - Aligned backend query routing in `core/views/dashboard.py:api_history_list` and in-memory filtering in `core/static/core/js/tab_history.js`.
  2. **Evidence Photos in History Table & Mobile Cards:**
     - Added dedicated **Evidence Photos** column to desktop/tablet history grid table (`itms-grid-table`).
     - Compact dual thumbnails (Front & Rear, 44x32px) with hover zoom and single-click full-screen lightbox preview (`openLightbox`).
     - Added responsive evidence photo strips to mobile cards (`history-card-item`) with `FRONT` and `REAR` badge tags and tap-to-zoom.
     - Graceful fallbacks (`--` or auto-hidden container) for stock ledger, email, or unphotographed audit events.
  3. **Evidence Photo Previews in Audit Detail Modal (`modal-history-detail`):**
     - Fixed `openHistoryModal` in `tab_history.js` to dynamically show/hide front and rear photo cards independently.
     - Full-resolution image URL binding on click (`this.dataset.fullUrl || this.src`) preventing blurry thumbnail zoom in lightbox.
  4. **Clean Media URL Normalization:**
     - Implemented `_clean_media_url` in `core/views/dashboard.py`: safely resolves vault files, thumbnails, and previews to clean `/media/` paths, eliminating duplicate prefixes, double slashes, and Windows backslashes.
     - Increased returned items limit to 300 to ensure rich representations of both direct audit logs and pair records.
