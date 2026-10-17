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
