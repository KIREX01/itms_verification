# Backend Requests & Specifications

This document tracks all backend requirements, database changes, and REST API contracts requested by the UI/UX & Frontend Development team.

---

## 1. Feature: Replace Image (Queue & Batch Interfaces)

### 1.1 Endpoint: Replace Image on a Specific Pair
- **URL**: `POST /api/pairs/<int:pair_id>/action/`
- **Body / Form-Data**:
  - `action`: `"replace_image"`
  - `side`: `"front"` | `"rear"`
  - `source`: `"upload"` | `"batch"`
  - `photo`: `File` (required if `source == "upload"`)
  - `image_id`: `UUID` / `int` (required if `source == "batch"`)
  - `purge_old`: `boolean` (optional, default: `true`)
- **Required Backend Processing**:
  1. If `source == "upload"`:
     - Ingest via `vault_service.ingest_uploaded_file()` into the pair's current batch.
     - Execute whole-image enhancement, fingerprinting, preview/thumbnail generation.
     - Run vision pipeline on the image (`process_single_image_vision`): YOLO plate detection, OCR, normalization, orientation classification.
  2. If `source == "batch"`:
     - Locate existing `EvidenceImage` by `image_id`.
  3. Reassign target pair's `front_image` or `rear_image` to the replacement image.
  4. Call `pair.refresh_completeness()`.
  5. Re-run `DualStreamVisionEngine.process_pair(pair)` to reconcile consensus and plate matching if complete.
  6. If `purge_old == true`:
     - Safely delete/unlink old image files from disk (`vault_file`, `thumbnail_file`, `preview_file`).
     - Mark old `EvidenceImage` as `is_file_pruned = True, status = "PRUNED"`.
  7. Append audit log in `SubmissionAuditLog` with `IMAGE_REPLACED`.
- **Expected JSON Response**:
  ```json
  {
    "success": true,
    "message": "Front photo replaced successfully.",
    "pair": { ... updated pair serialization ... }
  }
  ```

### 1.2 Endpoint: Get Batch Candidate Photos (For Batch Picker Modal)
- **URL**: `GET /api/batches/<str:batch_id>/candidates/`
- **Query Params**:
  - `orientation`: `"FRONT"` | `"REAR"` | `"ALL"` (optional)
  - `unassigned_only`: `boolean` (optional, default: `false`)
- **Expected JSON Response**:
  ```json
  {
    "success": true,
    "batch_id": "BATCH-2026...",
    "candidates": [
      {
        "id": "uuid",
        "url": "/media/vault/...",
        "thumb_url": "/media/vault/.../thumb.webp",
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

### 1.3 Endpoint: Replace Photo Directly in Batch View
- **URL**: `POST /api/images/<str:image_id>/replace/`
- **Body / Form-Data**:
  - `photo`: `File`
  - `purge_old`: `boolean` (default: `true`)
- **Required Backend Processing**:
  - Ingest new photo into the same batch.
  - Run vision/OCR pipeline on new photo.
  - If old photo was part of any active `VehicleInstallationPair`, update foreign key to point to the new image.
  - Safely prune old image binary file if requested.
  - Return updated photo metadata and affected pair ID.

---

## 2. Feature: Delete Pair (Queue Interface)

### 2.1 Endpoint: Safe Soft-Delete & Immediate Vault Purge
- **URL**: `POST /api/pairs/<int:pair_id>/action/`
- **Body**:
  - `action`: `"delete_pair"`
  - `purge_files`: `boolean` (default: `true`)
- **Required Backend Processing**:
  1. **Zero Database Crash / Soft-Delete**:
     - Add `is_deleted = models.BooleanField(default=False, db_index=True)` and `deleted_at = models.DateTimeField(null=True, blank=True)` to `VehicleInstallationPair`.
     - Set `pair.is_deleted = True, pair.deleted_at = timezone.now()`.
  2. **Release Installation Order**:
     - If pair was linked to `InstallationOrder`, decouple and reset order status so future captures can match it.
  3. **Immediate Purge Process (At that Moment in Time)**:
     - If `purge_files == true`:
       - For `front_image` and `rear_image`: if not referenced by any other active pair, unlink physical files on disk (`vault_file`, `thumbnail_file`, `preview_file`).
       - Mark `EvidenceImage.is_file_pruned = True, status = "PRUNED", pruned_at = timezone.now()`.
  4. **Audit Trail**:
     - Create `SubmissionAuditLog` with action `PAIR_DELETED` / `OPERATOR_OVERRIDE` documenting operator, timestamp, and purged file details.
  5. **Query Scoping**:
     - Ensure `api_pairs_list`, `api_pair_detail`, and `api_stats` filter out `is_deleted=True`.
- **Expected JSON Response**:
  ```json
  {
    "success": true,
    "message": "Pair UBK123A deleted and evidence files purged."
  }
  ```
