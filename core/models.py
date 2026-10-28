"""
Core data models for the ITMS installation-photo verification system.

Design notes
------------
- EvidenceImage.status models the full lifecycle of a single photo as it
  moves through the vault -> vision pipeline -> association -> submission.
- VehicleInstallationPair is the unit of work an operator actually approves:
  a (front, rear) pair matched (or not) against an InstallationOrder.
- SubmissionAuditLog is intentionally append-only: rows are never edited,
  only inserted, so it can serve as a forensic trail.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


class InstallationOrder(models.Model):
    """A single expected plate installation, as loaded from the ITMS registry."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Installation Evidence"
        MATCHED = "MATCHED", "Matched to Evidence"
        SUBMITTED = "SUBMITTED", "Submitted to ITMS"
        INSTALLED = "INSTALLED", "Installed (Verified in Archive)"
        FAILED = "FAILED", "Submission Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    registration_number = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Canonical registration number, e.g. UMA123AA",
    )
    plate_serial = models.CharField(max_length=64, blank=True)
    tracker_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)

    # ITMS WebApp Registry & Archive Metadata
    sales_order = models.CharField(max_length=64, blank=True, default="")
    service_type = models.CharField(max_length=64, blank=True, default="")
    vin = models.CharField(max_length=64, blank=True, default="", db_index=True)
    old_registration_number = models.CharField(max_length=32, blank=True, default="")
    warehouse_name = models.CharField(max_length=128, blank=True, default="", db_index=True)
    warehouse_id = models.CharField(max_length=64, blank=True, default="")
    order_status = models.CharField(max_length=64, blank=True, default="", help_text="e.g. Under installation, Ready for approve, Installed")
    registration_status = models.CharField(max_length=64, blank=True, default="", help_text="e.g. Active")
    installation_officer = models.CharField(max_length=128, blank=True, default="")
    installation_date = models.CharField(max_length=64, blank=True, default="")
    account_email = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="ITMS account email that fetched/owns this order.",
    )
    account_uuid = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="ITMS account UUID that fetched/owns this order.",
    )
    itms_order_uuid = models.CharField(max_length=64, blank=True, default="", db_index=True)
    itms_action_url = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False, db_index=True, help_text="True if order is in /installation-orders/archive")
    is_active_on_itms = models.BooleanField(default=True, db_index=True, help_text="True if order is currently active on ITMS /installation-orders/index")
    last_synced_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Timestamp when order was last verified or synced with ITMS")
    itms_stage = models.CharField(max_length=32, blank=True, default="", db_index=True, help_text="Current stage on ITMS (STAGE_1_INSTALLATION, STAGE_2_APPROVE, STAGE_3_CONFIRMATION, ARCHIVED)")
    has_front_photo = models.BooleanField(default=False, help_text="True if front photo is confirmed present on ITMS")
    has_rear_photo = models.BooleanField(default=False, help_text="True if rear photo is confirmed present on ITMS")

    # ITMS Detailed Order & Hardware Inventory
    front_plate_serial = models.CharField(max_length=64, blank=True, default="")
    rear_plate_serial = models.CharField(max_length=64, blank=True, default="")
    front_plate_type = models.CharField(max_length=64, blank=True, default="")
    rear_plate_type = models.CharField(max_length=64, blank=True, default="")
    gps_tracker_id = models.CharField(max_length=64, blank=True, default="")
    front_beacon_id = models.CharField(max_length=64, blank=True, default="")
    rear_beacon_id = models.CharField(max_length=64, blank=True, default="")
    front_photo_url = models.CharField(max_length=255, blank=True, default="")
    rear_photo_url = models.CharField(max_length=255, blank=True, default="")
    details_json = models.JSONField(default=dict, blank=True)
    photos_json = models.JSONField(default=list, blank=True)
    info_fetched_at = models.DateTimeField(null=True, blank=True)
    facility_code = models.CharField(
        max_length=32,
        db_index=True,
        default="AGM",
        help_text="Operating warehouse/bond facility code, e.g. AGM, SPIRO",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["registration_number", "status"]),
            models.Index(fields=["is_active_on_itms", "is_archived"]),
            models.Index(fields=["facility_code", "status"]),
            models.Index(fields=["facility_code", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.order_number} ({self.registration_number})"


class IngestionBatch(models.Model):
    """Tracks a distinct photo upload / ingestion session."""

    class SourceType(models.TextChoices):
        CLI = "CLI", "Command Line Ingestion"
        WEB = "WEB", "Web Upload"
        API = "API", "REST API"
        MOBILE = "MOBILE", "Mobile Direct Capture"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Human-readable batch identifier, e.g. BATCH-20260907-142030-ab12",
    )
    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        default=SourceType.CLI,
    )
    source_label = models.CharField(
        max_length=255,
        blank=True,
        help_text="Folder name, operator name, or upload note for this batch.",
    )
    account_email = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Active ITMS account email at time of ingestion.",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingestion_batches",
        help_text="Local operator account that captured or ingested this batch.",
    )
    facility_code = models.CharField(
        max_length=32,
        db_index=True,
        default="AGM",
        help_text="Operating warehouse/bond facility code.",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    total_files = models.PositiveIntegerField(default=0)
    ingested_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Ingestion batches"
        indexes = [
            models.Index(fields=["operator", "-created_at"]),
            models.Index(fields=["facility_code", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.batch_id} ({self.source_type}) - {self.ingested_count} ingested"


class EvidenceImage(models.Model):
    """A single ingested photo, permanently copied into the evidence vault."""

    class Orientation(models.TextChoices):
        FRONT = "FRONT", "Front"
        REAR = "REAR", "Rear"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Status(models.TextChoices):
        NEW = "NEW", "New / Not Processed"
        PROCESSING = "PROCESSING", "Processing"
        PLATE_DETECTED = "PLATE_DETECTED", "Plate Detected"
        MATCHED = "MATCHED", "Matched to Order"
        INCOMPLETE = "INCOMPLETE", "Incomplete Pair"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Operator Review"
        READY = "READY", "Ready for Submission"
        SUBMITTED = "SUBMITTED", "Submitted"
        PRUNED = "PRUNED", "Pruned (Retention Policy)"
        FAILED = "FAILED", "Failed"
        DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED", "Duplicate Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    batch = models.ForeignKey(
        IngestionBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="images",
        help_text="The ingestion batch this image was uploaded in.",
    )
    file_hash = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="SHA-256 hex digest of the file contents, used for deduplication.",
    )
    original_source_path = models.CharField(max_length=1024)
    vault_file = models.CharField(
        max_length=1024,
        help_text="Path (relative to MEDIA_ROOT) of the immutable vault copy.",
    )
    thumbnail_file = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        help_text="Path (relative to MEDIA_ROOT) of 160x120 WebP thumbnail.",
    )
    preview_file = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        help_text="Path (relative to MEDIA_ROOT) of 640x480 WebP preview.",
    )
    file_size_bytes = models.BigIntegerField(default=0)

    detected_plate = models.CharField(max_length=32, blank=True, db_index=True)
    raw_ocr_text = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Raw unnormalized OCR character string from engine before normalizer or order guidance.",
    )
    order_guided_plate = models.CharField(
        max_length=32, blank=True, default="",
        help_text="Plate candidate resolved via active ITMS order prior matching.",
    )
    ocr_confidence = models.FloatField(null=True, blank=True)
    detector_confidence = models.FloatField(null=True, blank=True)
    bbox = models.JSONField(
        null=True, blank=True,
        help_text="Plate bounding box as [x1, y1, x2, y2] in pixel coordinates.",
    )

    orientation = models.CharField(
        max_length=16, choices=Orientation.choices, default=Orientation.UNKNOWN, db_index=True
    )
    orientation_confidence = models.FloatField(null=True, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NEW)
    error_message = models.TextField(blank=True)

    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times vision processing has been attempted on this image.",
    )

    captured_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="EXIF capture timestamp (DateTimeOriginal).",
    )
    folder_orientation = models.CharField(
        max_length=16,
        blank=True,
        help_text="Orientation inferred from folder structure (FRONT or REAR).",
    )
    ingested_at = models.DateTimeField(default=timezone.now, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp when this evidence was successfully submitted to ITMS.",
    )
    is_file_pruned = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if the heavy vault file has been pruned per the 7-day retention policy.",
    )
    pruned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ingested_at"]
        indexes = [
            models.Index(fields=["detected_plate", "orientation"]),
            models.Index(fields=["status"]),
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["batch", "orientation"]),
            models.Index(fields=["batch", "status"]),
        ]

    def __str__(self):
        return f"{self.id} [{self.orientation}] {self.detected_plate or '???'}"

    @property
    def url(self) -> str:
        return f"/media/{self.vault_file}" if self.vault_file else ""

    @property
    def absolute_path(self) -> str:
        from core.services import vault_service
        return str(vault_service.resolve_vault_path(self.vault_file))


class VehicleInstallationPair(models.Model):
    """Associates a front + rear EvidenceImage pair with an InstallationOrder."""

    class VerificationStatus(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW", "Pending Review"
        APPROVED = "APPROVED", "Approved"
        INCOMPLETE = "INCOMPLETE", "Incomplete"
        CONFLICT = "CONFLICT", "Conflict"
        UNREGISTERED = "UNREGISTERED", "Unregistered Vehicle"
        SUBMITTED = "SUBMITTED", "Submitted"
        FAILED = "FAILED", "Submission Failed"
        OFFLINE_OUTBOX = "OFFLINE_OUTBOX", "Offline Outbox"

    class MatchType(models.TextChoices):
        EXACT = "EXACT", "Exact Match"
        FUZZY = "FUZZY", "Fuzzy Match"
        NONE = "NONE", "No Match"

    registration_number_detected = models.CharField(
        max_length=32, db_index=True,
        help_text="Canonical plate string this pair was grouped under.",
    )
    order = models.ForeignKey(
        InstallationOrder, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="pairs",
    )
    front_image = models.ForeignKey(
        EvidenceImage, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="as_front_of",
    )
    rear_image = models.ForeignKey(
        EvidenceImage, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="as_rear_of",
    )

    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING_REVIEW,
        db_index=True,
    )
    match_type = models.CharField(max_length=8, choices=MatchType.choices, default=MatchType.NONE)
    match_score = models.FloatField(null=True, blank=True)
    class MatchedVia(models.TextChoices):
        VISION = "VISION", "Vision OCR"
        ORDER_PRIOR = "ORDER_PRIOR", "Order Prior Guided"
        MANUAL = "MANUAL", "Manual Operator Entry"
        REPAIR = "REPAIR", "Manual Re-Pair"
        MOBILE_CONVEYOR = "MOBILE_CONVEYOR", "Mobile Conveyor Direct Pair"

    matched_via = models.CharField(
        max_length=16, choices=MatchedVia.choices, default=MatchedVia.VISION,
        help_text="Pipeline stage that determined the pair grouping and plate identity.",
    )
    is_manual_override = models.BooleanField(
        default=False, db_index=True,
        help_text="True if operator manually typed or confirmed the plate / order identity.",
    )
    manual_plate_override = models.CharField(max_length=32, blank=True, default="")
    account_email = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="ITMS account email associated with this pair.",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_pairs",
        help_text="Operator who reviewed, approved, or owns this pair.",
    )
    facility_code = models.CharField(
        max_length=32,
        db_index=True,
        default="AGM",
        help_text="Operating warehouse/bond facility code.",
    )
    is_complete = models.BooleanField(default=False, db_index=True)

    operator_note = models.TextField(blank=True)

    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp when this pair was successfully submitted to ITMS.",
    )
    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if soft-deleted/hidden by operator.",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when pair was soft-deleted.",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["verification_status", "is_complete"]),
            models.Index(fields=["verification_status", "-updated_at"]),
            models.Index(fields=["registration_number_detected", "verification_status"]),
            models.Index(fields=["operator", "verification_status"]),
            models.Index(fields=["facility_code", "verification_status"]),
            models.Index(fields=["is_deleted", "verification_status"]),
            models.Index(fields=["-updated_at"]),
        ]

    def __str__(self):
        return f"Pair<{self.registration_number_detected}> {self.verification_status}"

    def refresh_completeness(self):
        self.is_complete = bool(self.front_image_id and self.rear_image_id)
        return self.is_complete

    def soft_delete(self, operator=None, purge_files: bool = True) -> Dict[str, Any]:
        """
        Soft-deletes the pair, releases any linked order back to PENDING,
        and safely purges unreferenced vault files from disk.
        """
        self.is_deleted = True
        self.deleted_at = timezone.now()

        if self.order:
            linked_order = self.order
            if linked_order.status in (InstallationOrder.Status.MATCHED, InstallationOrder.Status.FAILED):
                linked_order.status = InstallationOrder.Status.PENDING
                linked_order.save(update_fields=["status"])
            self.order = None

        self.save(update_fields=["is_deleted", "deleted_at", "order", "updated_at"])

        purged_ids: List[str] = []
        if purge_files:
            from core.services import vault_service
            for img in [self.front_image, self.rear_image]:
                if img:
                    other_refs = VehicleInstallationPair.objects.filter(
                        is_deleted=False
                    ).exclude(id=self.id).filter(
                        Q(front_image=img) | Q(rear_image=img)
                    ).exists()
                    if not other_refs:
                        vault_service.purge_evidence_file(img)
                        purged_ids.append(str(img.id))

        op_user = operator if (operator and getattr(operator, "is_authenticated", False)) else None
        op_name = (op_user.get_full_name().strip() or op_user.username) if op_user else "System"
        op_email = op_user.email.strip() if (op_user and getattr(op_user, "email", None)) else ""

        SubmissionAuditLog.objects.create(
            pair=self,
            operator=op_user,
            operator_username=op_name,
            operator_email=op_email,
            action=SubmissionAuditLog.Action.PAIR_DELETED,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator soft-deleted pair #{self.id} ({self.registration_number_detected}). Files {'purged: ' + ', '.join(purged_ids) if purged_ids else 'retained'}.",
        )
        return {"success": True, "pair_id": self.id, "purged_files": purged_ids}

    def replace_image(self, side: str, new_image: EvidenceImage, operator=None, purge_old: bool = False) -> Dict[str, Any]:
        """
        Replaces front or rear photo of the pair, refreshes completeness/joint consensus,
        and optionally unlinks/purges the old photo file from disk.
        """
        side_clean = side.lower().strip()
        old_image = None
        if side_clean == "front":
            old_image = self.front_image
            self.front_image = new_image
            new_image.orientation = EvidenceImage.Orientation.FRONT
            new_image.save(update_fields=["orientation"])
        elif side_clean == "rear":
            old_image = self.rear_image
            self.rear_image = new_image
            new_image.orientation = EvidenceImage.Orientation.REAR
            new_image.save(update_fields=["orientation"])
        else:
            raise ValueError(f"Invalid side '{side}'. Must be 'front' or 'rear'.")

        self.refresh_completeness()
        if self.is_complete:
            try:
                from core.vision.joint_pipeline import DualStreamVisionEngine
                engine = DualStreamVisionEngine(save_crops=False)
                res = engine.process_pair(self)
                if res and res.plate_number:
                    self.registration_number_detected = res.plate_number
            except Exception as exc:
                logger.warning("Joint vision processing failed on image replace for pair %s: %s", self.id, exc)

        self.save()

        if purge_old and old_image and old_image.id != new_image.id:
            from core.services import vault_service
            other_refs = VehicleInstallationPair.objects.filter(
                is_deleted=False
            ).exclude(id=self.id).filter(
                Q(front_image=old_image) | Q(rear_image=old_image)
            ).exists()
            if not other_refs:
                vault_service.purge_evidence_file(old_image)

        op_user = operator if (operator and getattr(operator, "is_authenticated", False)) else None
        op_name = (op_user.get_full_name().strip() or op_user.username) if op_user else "System"
        op_email = op_user.email.strip() if (op_user and getattr(op_user, "email", None)) else ""

        SubmissionAuditLog.objects.create(
            pair=self,
            operator=op_user,
            operator_username=op_name,
            operator_email=op_email,
            action=SubmissionAuditLog.Action.IMAGE_REPLACED,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator replaced {side_clean.upper()} photo (new: {new_image.id}, old: {old_image.id if old_image else 'None'}). Purge old: {purge_old}.",
        )
        return {"success": True, "pair_id": self.id, "side": side_clean}


class SubmissionAuditLog(models.Model):
    """Append-only audit trail of every automated/manual action on a pair."""

    class Action(models.TextChoices):
        ORDER_LOOKUP = "ORDER_LOOKUP", "Order Lookup"
        SERIAL_VERIFY = "SERIAL_VERIFY", "Serial Verification"
        UPLOAD_FRONT = "UPLOAD_FRONT", "Front Photo Upload"
        UPLOAD_REAR = "UPLOAD_REAR", "Rear Photo Upload"
        VALIDATE = "VALIDATE", "Submission Validation"
        SUBMIT = "SUBMIT", "Final Submission"
        OPERATOR_OVERRIDE = "OPERATOR_OVERRIDE", "Operator Override"
        OPERATOR_APPROVE = "OPERATOR_APPROVE", "Operator Approve"
        OPERATOR_SWAP = "OPERATOR_SWAP", "Operator Front/Rear Swap"
        ARCHIVE_VERIFY = "ARCHIVE_VERIFY", "Archive Installation Verification"
        ORDER_INFO_FETCH = "ORDER_INFO_FETCH", "Order Info Inspection"
        PHOTO_DOWNLOAD = "PHOTO_DOWNLOAD", "ITMS Photo Download"
        ORDER_SYNC = "ORDER_SYNC", "Order Registry Sync"
        MANUAL_PLATE_ASSIGN = "MANUAL_PLATE_ASSIGN", "Manual Plate Assignment"
        ORDER_PRIOR_CORRECT = "ORDER_PRIOR_CORRECT", "Order Prior Hypothesis Correction"
        OUTBOX_QUEUE = "OUTBOX_QUEUE", "Offline Outbox Queued"
        OUTBOX_DRAIN = "OUTBOX_DRAIN", "Offline Outbox Auto-Synced"
        FALLBACK = "FALLBACK", "Manual Fallback Triggered"
        PAIR_DELETED = "PAIR_DELETED", "Pair Deleted / Purged"
        IMAGE_REPLACED = "IMAGE_REPLACED", "Evidence Image Replaced"
        STOCK_LEDGER_UPDATED = "STOCK_LEDGER_UPDATED", "Stock Ledger Updated"
        STOCK_REPORT_EMAILED = "STOCK_REPORT_EMAILED", "Stock Report Emailed"

    class ResultStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"
        INFO = "INFO", "Info"

    pair = models.ForeignKey(
        VehicleInstallationPair,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    operator_username = models.CharField(max_length=150, blank=True, default="System")
    operator_email = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    result = models.CharField(max_length=8, choices=ResultStatus.choices, db_index=True)
    message = models.TextField(blank=True)
    simulated_token = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["pair", "action", "-timestamp"]),
            models.Index(fields=["action", "-timestamp"]),
        ]

    def save(self, *args, **kwargs):
        # Automatically resolve operator from pair or batch if omitted
        if not self.operator and self.pair:
            if self.pair.operator:
                self.operator = self.pair.operator
            else:
                batch = (self.pair.front_image.batch if self.pair.front_image and self.pair.front_image.batch else None) or (
                    self.pair.rear_image.batch if self.pair.rear_image and self.pair.rear_image.batch else None
                )
                if batch and batch.operator:
                    self.operator = batch.operator

        if self.operator:
            full_name = self.operator.get_full_name().strip()
            uname = getattr(self.operator, "username", "Operator")
            if not self.operator_username or self.operator_username == "System":
                self.operator_username = full_name or uname
            if not self.operator_email and getattr(self.operator, "email", ""):
                self.operator_email = self.operator.email.strip()

        if not self.operator_email and self.pair:
            if self.pair.account_email:
                self.operator_email = self.pair.account_email.strip()
            else:
                batch = (self.pair.front_image.batch if self.pair.front_image and self.pair.front_image.batch else None) or (
                    self.pair.rear_image.batch if self.pair.rear_image and self.pair.rear_image.batch else None
                )
                if batch and batch.account_email:
                    self.operator_email = batch.account_email.strip()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {self.action} -> {self.result}"


class InstallationKit(models.Model):
    """
    ITMS Installation Kit inventory record representing linked plates and trackers.
    Mirrors https://stock.itms.ug/installation-kits.
    """

    kit_code = models.CharField(max_length=64, unique=True, db_index=True, help_text="e.g. IK-UMA300PW")
    registration_number = models.CharField(max_length=32, db_index=True, help_text="e.g. UMA 300PW")
    front_plate = models.CharField(max_length=64, blank=True, db_index=True)
    rear_plate = models.CharField(max_length=64, blank=True, db_index=True)
    front_tracker = models.CharField(max_length=64, blank=True, db_index=True)
    rear_tracker = models.CharField(max_length=64, blank=True, db_index=True)
    gps_tracker = models.CharField(max_length=64, blank=True, db_index=True)
    warehouse = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=64, blank=True, default="New")
    created_date = models.CharField(max_length=64, blank=True, default="")
    kit_uuid = models.CharField(max_length=64, blank=True, default="", db_index=True)
    detail_url = models.CharField(max_length=255, blank=True, default="")

    # Extended Information from /installation-kit/<uuid>/main/information
    created_by_user = models.CharField(max_length=128, blank=True, default="")
    front_plate_article = models.CharField(max_length=64, blank=True, default="")
    rear_plate_article = models.CharField(max_length=64, blank=True, default="")
    gps_article = models.CharField(max_length=64, blank=True, default="")
    sim_article = models.CharField(max_length=64, blank=True, default="")
    sim_serial = models.CharField(max_length=64, blank=True, default="")
    sim_mac = models.CharField(max_length=64, blank=True, default="")
    front_tracker_article = models.CharField(max_length=64, blank=True, default="")
    rear_tracker_article = models.CharField(max_length=64, blank=True, default="")
    front_tracker_mac = models.CharField(max_length=64, blank=True, default="")
    rear_tracker_mac = models.CharField(max_length=64, blank=True, default="")
    details_json = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    facility_code = models.CharField(
        max_length=32,
        db_index=True,
        default="AGM",
        help_text="Operating warehouse/bond facility code, e.g. AGM, SPIRO",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["facility_code", "status"]),
            models.Index(fields=["facility_code", "registration_number"]),
        ]

    def __str__(self):
        return f"{self.kit_code} ({self.registration_number})"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kit_code": self.kit_code,
            "registration_number": self.registration_number,
            "status": self.status,
            "warehouse": self.warehouse,
            "front_plate": self.front_plate,
            "rear_plate": self.rear_plate,
            "front_tracker": self.front_tracker,
            "rear_tracker": self.rear_tracker,
            "gps_tracker": self.gps_tracker,
            "sim_serial": self.sim_serial,
            "sim_mac": self.sim_mac,
            "created_date": self.created_date,
            "created_by_user": self.created_by_user,
            "facility_code": self.facility_code,
            "kit_uuid": self.kit_uuid,
            "detail_url": self.detail_url,
        }


# ============================================================================
# Physical Inventory & Stock Monitoring Models
# ============================================================================

class PlateCategory(models.TextChoices):
    PSV = "PSV", "Public White (PSV)"
    PMO = "PMO", "Private Yellow (PMO)"


class StockDelivery(models.Model):
    """
    Inbound shipment / delivery manifest of license plates or installation kits
    received at the warehouse.
    """
    delivery_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Delivery manifest or shipment reference, e.g. DN-20260930-01",
    )
    paper_note_reference = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Printed physical delivery note number from paper slip",
    )
    delivery_note_image = models.ForeignKey(
        "EvidenceImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delivery_notes",
        help_text="Optional photo of paper delivery note in Evidence Vault",
    )
    supplier = models.CharField(
        max_length=128,
        blank=True,
        default="Factory / Central Depot",
        help_text="Origin supplier, factory or dispatching depot",
    )
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
        help_text="PSV (Public White) or PMO (Private Yellow)",
    )
    delivery_date = models.DateField(default=timezone.localdate, db_index=True)
    target_date_suffix = models.CharField(
        max_length=16,
        blank=True,
        db_index=True,
        help_text="6-digit date suffix matching shift orders, e.g. 260926",
    )
    total_plates_count = models.PositiveIntegerField(default=0)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_deliveries",
    )
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-delivery_date", "-created_at"]
        verbose_name_plural = "Stock deliveries"

    def __str__(self):
        return f"{self.delivery_number} ({self.total_plates_count} {self.plate_category} plates on {self.delivery_date})"

    @property
    def image_url(self) -> Optional[str]:
        if self.delivery_note_image:
            return self.delivery_note_image.url
        return None

    @property
    def image_absolute_path(self) -> Optional[str]:
        if self.delivery_note_image:
            return self.delivery_note_image.absolute_path
        return None


class StockDeliveryItem(models.Model):
    """Individual plate scanned or recorded as part of an inbound delivery."""
    delivery = models.ForeignKey(
        StockDelivery,
        on_delete=models.CASCADE,
        related_name="items",
    )
    registration_number = models.CharField(max_length=32, db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
    )
    plate_serial = models.CharField(max_length=64, blank=True, default="")
    kit_code = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["delivery", "registration_number"]),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.plate_category}) [Delivery #{self.delivery.delivery_number}]"


class StockBondTransfer(models.Model):
    """
    Transfers of installation kits/plates between bonds:
    - TRANSFER_IN: kits transferred into our bond from other bonds (+Stock)
    - TRANSFER_OUT: kits transferred from our bond to other bonds (-Stock)
    """
    class TransferType(models.TextChoices):
        TRANSFER_IN = "TRANSFER_IN", "Bond Transfer In (Received from another bond)"
        TRANSFER_OUT = "TRANSFER_OUT", "Bond Transfer Out (Sent to another bond)"

    transfer_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Transfer reference e.g. TRF-IN-20260929-01",
    )
    transfer_type = models.CharField(max_length=16, choices=TransferType.choices, db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
        help_text="PSV (Public White) or PMO (Private Yellow)",
    )
    other_bond_name = models.CharField(
        max_length=128,
        blank=True,
        default="Other Bond",
        help_text="Name of external bond transferred to/from",
    )
    transfer_date = models.DateField(default=timezone.localdate, db_index=True)
    target_date_suffix = models.CharField(max_length=16, blank=True, db_index=True)
    plates_count = models.PositiveIntegerField(default=0)
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-transfer_date", "-created_at"]
        verbose_name_plural = "Stock bond transfers"

    def __str__(self):
        return f"{self.transfer_number} ({self.transfer_type} {self.plates_count} {self.plate_category} plates - {self.other_bond_name})"


class StockDispatchScan(models.Model):
    """
    Log of plates scanned when taken out of warehouse stock and issued to the
    installation/assembly line for a given work date.
    """
    class Status(models.TextChoices):
        RECONCILED_INSTALLED = "RECONCILED_INSTALLED", "Reconciled Installed (Archive)"
        RETURNED_TO_SAFE = "RETURNED_TO_SAFE", "Returned to Safe Room"
        ON_LINE_ACTIVE = "ON_LINE_ACTIVE", "On Line Active (Fitting in Progress)"
        PENDING_SYSTEM_SYNC = "PENDING_SYSTEM_SYNC", "Pending ITMS Sync (Local Queue)"
        UNRESOLVED_DISCREPANCY = "UNRESOLVED_DISCREPANCY", "Unresolved Discrepancy (MVR Review)"
        # Legacy status values preserved for backward compatibility
        DISPATCHED = "DISPATCHED", "Dispatched to Line"
        INSTALLED = "INSTALLED", "Installed (Archived)"
        PENDING_ORDER = "PENDING_ORDER", "Pending in Order"
        RETURNED = "RETURNED", "Returned to Stock"
        UNALLOCATED = "UNALLOCATED", "Unallocated Discrepancy"

    registration_number = models.CharField(max_length=32, db_index=True)
    bond_code = models.CharField(max_length=32, blank=True, default="AGM", db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
    )
    work_date = models.DateField(default=timezone.localdate, db_index=True)
    work_date_suffix = models.CharField(
        max_length=16,
        db_index=True,
        help_text="6-digit date suffix, e.g. 260926",
    )
    dispatched_at = models.DateTimeField(default=timezone.now, db_index=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatch_scans",
    )
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DISPATCHED, db_index=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-dispatched_at"]
        verbose_name_plural = "Stock dispatch scans"
        indexes = [
            models.Index(fields=["work_date", "status"]),
            models.Index(fields=["work_date_suffix", "status"]),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.plate_category}) Dispatched {self.work_date_suffix}"


class StockReturnScan(models.Model):
    """
    Log of plates returned to stock uninstalled (motorcycle no-show, defect, cancellation).
    """
    class Reason(models.TextChoices):
        BIKE_NO_SHOW = "BIKE_NO_SHOW", "Motorcycle No-Show"
        DEFECTIVE_PLATE = "DEFECTIVE_PLATE", "Defective Plate / Hardware"
        CANCELLED_ORDER = "CANCELLED_ORDER", "Cancelled Order"
        LINE_ROLLOVER = "LINE_ROLLOVER", "End of Shift Line Return"
        OTHER = "OTHER", "Other"

    registration_number = models.CharField(max_length=32, db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
    )
    work_date = models.DateField(default=timezone.localdate, db_index=True)
    work_date_suffix = models.CharField(
        max_length=16,
        db_index=True,
        help_text="6-digit date suffix, e.g. 260926",
    )
    returned_at = models.DateTimeField(default=timezone.now, db_index=True)
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="return_scans",
    )
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    reason = models.CharField(max_length=64, choices=Reason.choices, default=Reason.BIKE_NO_SHOW)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-returned_at"]
        verbose_name_plural = "Stock return scans"
        indexes = [
            models.Index(fields=["work_date", "reason"]),
            models.Index(fields=["work_date_suffix", "reason"]),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.plate_category}) Returned: {self.reason}"


class SafeAuditScan(models.Model):
    """
    Physical plate scanned or entered during safe room physical stock-taking / audit for a shift.
    Represents actual physical presence in the safe room / storage box at this bond facility.
    """
    registration_number = models.CharField(max_length=32, db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
    )
    work_date = models.DateField(default=timezone.localdate, db_index=True)
    work_date_suffix = models.CharField(
        max_length=16,
        db_index=True,
        help_text="6-digit date suffix, e.g. 260926",
    )
    bond_code = models.CharField(max_length=32, blank=True, default="AGM", db_index=True)
    scanned_at = models.DateTimeField(default=timezone.now, db_index=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="safe_audit_scans",
    )
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scanned_at"]
        verbose_name_plural = "Safe audit scans"
        indexes = [
            models.Index(fields=["work_date_suffix", "registration_number"]),
            models.Index(fields=["work_date", "plate_category"]),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.plate_category}) Safe Audit {self.work_date_suffix}"


class StockBlockedScan(models.Model):
    """
    Persistent database log of plates scanned at dispatch or inspection that were
    NOT on stock (or already installed) and set aside for the shift.
    Ensures all logged-in devices (Mobile Companion, Desktop Web UI, TUI) share
    a unified, durable 'Not on Stock / Set Aside' list across workers.
    """
    registration_number = models.CharField(max_length=32, db_index=True)
    plate_category = models.CharField(
        max_length=8,
        choices=PlateCategory.choices,
        default=PlateCategory.PSV,
        db_index=True,
    )
    work_date = models.DateField(default=timezone.localdate, db_index=True)
    work_date_suffix = models.CharField(
        max_length=16,
        db_index=True,
        help_text="6-digit date suffix, e.g. 260926",
    )
    bond_code = models.CharField(max_length=32, blank=True, default="AGM", db_index=True)
    reason = models.CharField(max_length=255, blank=True, default="NOT ON ITMS STOCK")
    scanned_at = models.DateTimeField(default=timezone.now, db_index=True)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blocked_scans",
    )
    operator_name = models.CharField(max_length=150, blank=True, default="Operator")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scanned_at", "id"]
        verbose_name_plural = "Stock blocked scans"
        constraints = [
            models.UniqueConstraint(
                fields=["work_date_suffix", "registration_number"],
                name="unique_blocked_scan_per_shift",
            )
        ]
        indexes = [
            models.Index(fields=["work_date_suffix", "registration_number"]),
            models.Index(fields=["work_date", "plate_category"]),
        ]

    def __str__(self):
        return f"{self.registration_number} ({self.plate_category}) Blocked {self.work_date_suffix}: {self.reason}"


class DailyStockLedger(models.Model):
    """
    Daily stock reconciliation snapshot and accounting ledger per work date.
    Differentiates between Public White (PSV) and Private Yellow (PMO):
    Opening Balance + Kits Received + Bond Transfer In - Bond Transfer Out - Kits Installed = Closing Balance
    """
    work_date = models.DateField(unique=True, db_index=True)
    work_date_suffix = models.CharField(max_length=16, db_index=True)
    warehouse_name = models.CharField(max_length=128, blank=True, default="Warehouse Stock / Bond")

    # 1. Public White (PSV) Category
    opening_balance_psv = models.IntegerField(default=0, help_text="PSV stock at start of shift")
    kits_received_psv = models.IntegerField(default=0, help_text="PSV kits received from supplier/central depot")
    bond_transfer_in_psv = models.IntegerField(default=0, help_text="PSV kits transferred into our bond from other bonds")
    bond_transfer_out_psv = models.IntegerField(default=0, help_text="PSV kits transferred out from our bond to other bonds")
    scheduled_psv = models.IntegerField(default=0, help_text="Target PSV plates scheduled for installation under the bond (manually entered)")
    kits_installed_psv = models.IntegerField(default=0, help_text="PSV kits installed in orders & archive for that day")
    closing_balance_psv = models.IntegerField(default=0, help_text="PSV calculated closing stock balance")

    # 2. Private Yellow (PMO) Category
    opening_balance_pmo = models.IntegerField(default=0, help_text="PMO stock at start of shift")
    kits_received_pmo = models.IntegerField(default=0, help_text="PMO kits received from supplier/central depot")
    bond_transfer_in_pmo = models.IntegerField(default=0, help_text="PMO kits transferred into our bond from other bonds")
    bond_transfer_out_pmo = models.IntegerField(default=0, help_text="PMO kits transferred out from our bond to other bonds")
    scheduled_pmo = models.IntegerField(default=0, help_text="Target PMO plates scheduled for installation under the bond (manually entered)")
    kits_installed_pmo = models.IntegerField(default=0, help_text="PMO kits installed in orders & archive for that day")
    closing_balance_pmo = models.IntegerField(default=0, help_text="PMO calculated closing stock balance")

    # 3. Overall Totals
    opening_stock = models.IntegerField(default=0, help_text="Combined opening stock")
    delivered_count = models.IntegerField(default=0, help_text="Combined kits received")
    bond_transfer_in_total = models.IntegerField(default=0, help_text="Combined bond transfer in")
    bond_transfer_out_total = models.IntegerField(default=0, help_text="Combined bond transfer out")
    scheduled_total = models.IntegerField(default=0, help_text="Combined scheduled installation target")
    installed_count = models.IntegerField(default=0, help_text="Combined kits installed")
    closing_stock = models.IntegerField(default=0, help_text="Combined calculated closing stock balance")

    # Floor operations tracking
    dispatched_count = models.IntegerField(default=0, help_text="Total plates taken out to line")
    pending_count = models.IntegerField(default=0, help_text="Plates active in orders")
    returned_count = models.IntegerField(default=0, help_text="Plates returned uninstalled to stock")
    unallocated_count = models.IntegerField(default=0, help_text="Discrepancy: Dispatched - Returned - Installed - Pending")

    physical_count = models.IntegerField(null=True, blank=True, help_text="Optional manual physical count audit")
    variance = models.IntegerField(default=0, help_text="Variance between physical count and closing stock")
    override_installed = models.BooleanField(
        default=False,
        help_text="True if installed count was manually entered/overridden rather than auto-counted from orders",
    )
    override_received = models.BooleanField(
        default=False,
        help_text="True if kits received count was manually entered rather than counted from scanned delivery items",
    )
    override_transfers = models.BooleanField(
        default=False,
        help_text="True if bond transfers were manually entered rather than counted from scan transfers",
    )
    is_closed = models.BooleanField(default=False, help_text="True if shift stock has been locked/closed")
    notes = models.TextField(blank=True, default="")
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date"]
        verbose_name_plural = "Daily stock ledgers"

    def __str__(self):
        return (
            f"Stock Ledger {self.work_date} ({self.work_date_suffix}): "
            f"PSV [Open {self.opening_balance_psv} -> Close {self.closing_balance_psv}], "
            f"PMO [Open {self.opening_balance_pmo} -> Close {self.closing_balance_pmo}]"
        )


class OperatorITMSSession(models.Model):
    """
    Persisted ITMS WebApp session credentials for an individual operator.
    Stores cookies, CSRF tokens, and facility scoping per user account,
    enabling multi-user concurrent operation on VPS servers without filesystem JSON contention.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="itms_session",
    )
    base_url = models.CharField(max_length=255, default="https://stock.itms.ug")
    itms_email = models.CharField(max_length=255, blank=True, default="")
    itms_user_uuid = models.CharField(max_length=128, blank=True, default="")
    itms_display_name = models.CharField(max_length=255, blank=True, default="")
    cookies = models.JSONField(default=dict, blank=True)
    csrf_token = models.TextField(blank=True, default="")
    is_authenticated = models.BooleanField(default=False)
    saved_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_status_message = models.CharField(max_length=500, blank=True, default="")
    active_bond_code = models.CharField(max_length=64, default="AGM")
    active_bond_name = models.CharField(max_length=255, default="AGM Bonded Warehouse")

    class Meta:
        verbose_name = "Operator ITMS Session"
        verbose_name_plural = "Operator ITMS Sessions"

    def __str__(self):
        status_str = f"Authenticated as {self.itms_email}" if self.is_authenticated else "Disconnected"
        return f"ITMS Session for {self.user.username} ({status_str})"

    def is_cookie_valid(self, buffer_seconds: int = 120) -> bool:
        """Return True if session cookies exist and have not expired."""
        if not self.is_authenticated or not self.cookies:
            return False
        if not self.cookies.get("_identity-frontend"):
            return False
        if not self.expires_at:
            return True
        from django.utils import timezone
        import datetime
        return timezone.now() < (self.expires_at - datetime.timedelta(seconds=buffer_seconds))


class OperatorProfile(models.Model):
    """
    Profile settings and facility scoping for an individual operator.
    Enforces logical multi-tenant isolation and transfer workflows.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    active_facility = models.CharField(max_length=32, default="AGM", db_index=True)
    allowed_facilities = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Operator Profile"
        verbose_name_plural = "Operator Profiles"

    def __str__(self):
        return f"Profile for {self.user.username} ({self.active_facility})"


class FacilitySyncMetadata(models.Model):
    """
    Tracks facility-level synchronization status and timestamps for cache deduplication.
    Prevents redundant network strain when multiple operators are stationed at the same bond.
    """
    facility_code = models.CharField(max_length=32, primary_key=True)
    last_kits_synced_at = models.DateTimeField(null=True, blank=True)
    last_orders_synced_at = models.DateTimeField(null=True, blank=True)
    total_kits = models.IntegerField(default=0)
    total_orders = models.IntegerField(default=0)
    synced_by_operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="facility_syncs",
    )

    class Meta:
        verbose_name = "Facility Sync Metadata"
        verbose_name_plural = "Facility Sync Metadata"

    def is_kits_fresh(self, ttl_minutes: int = 180) -> bool:
        if not self.last_kits_synced_at:
            return False
        from django.utils import timezone
        import datetime
        return timezone.now() < (self.last_kits_synced_at + datetime.timedelta(minutes=ttl_minutes))

    def is_orders_fresh(self, ttl_minutes: int = 60) -> bool:
        if not self.last_orders_synced_at:
            return False
        from django.utils import timezone
        import datetime
        return timezone.now() < (self.last_orders_synced_at + datetime.timedelta(minutes=ttl_minutes))

    def __str__(self):
        return f"Sync Metadata for {self.facility_code} ({self.total_kits} kits, {self.total_orders} orders)"


class AuthorizedDevice(models.Model):
    """
    Registry of known, approved, pending, or blocked client devices and network rules.
    Supports multi-device account sharing (e.g., Team Leader Desktop + Fitter Mobile Companion
    paired via QR code) while allowing granular blocking by Device ID, IP/CIDR, or MAC address.
    """

    class Status(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        PENDING = "PENDING", "Pending Approval"
        BLOCKED = "BLOCKED", "Blocked"
        REVOKED = "REVOKED", "Revoked"

    class DeviceType(models.TextChoices):
        DESKTOP = "DESKTOP", "Desktop / Laptop Console"
        MOBILE = "MOBILE", "Mobile Companion"
        TUI = "TUI", "Terminal UI (TUI)"
        NETWORK_RULE = "NETWORK_RULE", "IP / MAC Firewall Rule"

    class LinkedVia(models.TextChoices):
        EXISTING_TRUSTED = "EXISTING_TRUSTED", "Existing Linked Account"
        DIRECT_LOGIN = "DIRECT_LOGIN", "Authenticated Operator Login"
        QR_PAIRING = "QR_PAIRING", "Mobile QR Code Scan"
        EMAIL_ACTIVATION = "EMAIL_ACTIVATION", "Email Activation Request"
        ADMIN_RULE = "ADMIN_RULE", "Administrator Manual Rule"

    LinkMethod = LinkedVia

    device_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Deterministic hardware/browser device token or firewall rule ID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="authorized_devices",
        help_text="Operator account using this device (multiple devices can share one account)",
    )
    authorized_email = models.CharField(max_length=255, blank=True, default="", db_index=True)
    device_name = models.CharField(max_length=150, default="Operator Station")
    device_type = models.CharField(
        max_length=24,
        choices=DeviceType.choices,
        default=DeviceType.DESKTOP,
        db_index=True,
    )
    linked_via = models.CharField(
        max_length=24,
        choices=LinkedVia.choices,
        default=LinkedVia.DIRECT_LOGIN,
    )
    ip_address = models.CharField(max_length=64, blank=True, default="", db_index=True)
    pinned_ip_cidr = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional pinned IP or CIDR subnet for strict restriction or block rule",
    )
    mac_address = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        help_text="Normalized hardware MAC address (AA:BB:CC:DD:EE:FF) when available via LAN ARP or TUI",
    )
    fingerprint_hash = models.CharField(max_length=128, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.APPROVED,
        db_index=True,
    )
    block_reason = models.CharField(max_length=255, blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_device_records",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "Authorized Device"
        verbose_name_plural = "Authorized Devices"
        indexes = [
            models.Index(fields=["status", "ip_address"]),
            models.Index(fields=["status", "mac_address"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        owner = self.user.username if self.user else (self.authorized_email or "Unassigned")
        return f"{self.device_name} [{self.ip_address or 'No IP'} | {self.mac_address or self.device_id[:12]}] ({self.status} - {owner})"


class EmailAccessRequest(models.Model):
    """
    Tracks pre-approved operator emails and self-service signup / device activation requests.
    Allows new or unrecognized devices/emails to request activation and unlock via a 6-digit code
    or 1-click administrator approval.
    """

    class Status(models.TextChoices):
        PRE_APPROVED = "PRE_APPROVED", "Pre-Approved (Ready for Signup)"
        PENDING = "PENDING", "Pending Administrator Approval"
        APPROVED = "APPROVED", "Approved & Active"
        REJECTED = "REJECTED", "Rejected"
        BLOCKED = "BLOCKED", "Blocked"

    email = models.CharField(max_length=255, db_index=True)
    username = models.CharField(max_length=150, blank=True, default="")
    full_name = models.CharField(max_length=150, blank=True, default="")
    facility_code = models.CharField(max_length=32, default="AGM")
    requested_role = models.CharField(max_length=32, default="OPERATOR")
    reason = models.TextField(blank=True, default="")
    device_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    mac_address = models.CharField(max_length=32, blank=True, default="", db_index=True)
    client_ip = models.CharField(max_length=64, blank=True, default="", db_index=True)
    device_name = models.CharField(max_length=150, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    email_verified = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    activation_code = models.CharField(max_length=16, blank=True, default="", db_index=True)
    activation_token = models.CharField(max_length=128, unique=True, db_index=True)
    max_devices_allowed = models.IntegerField(
        default=4,
        help_text="Maximum concurrent devices allowed to share this account (e.g. Team Leader Desktop + Companion Phones)",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_access_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email Access Request"
        verbose_name_plural = "Email Access Requests"

    def __str__(self):
        return f"Access Request: {self.email} ({self.status}) [{self.client_ip}]"

    @property
    def ip_address(self) -> str:
        return self.client_ip



