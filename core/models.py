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
import uuid

from django.db import models
from django.utils import timezone


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
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    # ITMS WebApp Registry & Archive Metadata
    sales_order = models.CharField(max_length=64, blank=True, default="")
    service_type = models.CharField(max_length=64, blank=True, default="")
    vin = models.CharField(max_length=64, blank=True, default="", db_index=True)
    old_registration_number = models.CharField(max_length=32, blank=True, default="")
    warehouse_name = models.CharField(max_length=128, blank=True, default="")
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} ({self.registration_number})"


class IngestionBatch(models.Model):
    """Tracks a distinct photo upload / ingestion session."""

    class SourceType(models.TextChoices):
        CLI = "CLI", "Command Line Ingestion"
        WEB = "WEB", "Web Upload"
        API = "API", "REST API"

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
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    total_files = models.PositiveIntegerField(default=0)
    ingested_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Ingestion batches"

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
        max_length=16, choices=Orientation.choices, default=Orientation.UNKNOWN
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
    ingested_at = models.DateTimeField(default=timezone.now)
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
        ]

    def __str__(self):
        return f"{self.id} [{self.orientation}] {self.detected_plate or '???'}"


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
    )
    match_type = models.CharField(max_length=8, choices=MatchType.choices, default=MatchType.NONE)
    match_score = models.FloatField(null=True, blank=True)
    class MatchedVia(models.TextChoices):
        VISION = "VISION", "Vision OCR"
        ORDER_PRIOR = "ORDER_PRIOR", "Order Prior Guided"
        MANUAL = "MANUAL", "Manual Operator Entry"
        REPAIR = "REPAIR", "Manual Re-Pair"

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
    is_complete = models.BooleanField(default=False)

    operator_note = models.TextField(blank=True)

    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp when this pair was successfully submitted to ITMS.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Pair<{self.registration_number_detected}> {self.verification_status}"

    def refresh_completeness(self):
        self.is_complete = bool(self.front_image_id and self.rear_image_id)
        return self.is_complete


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

    class ResultStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"
        INFO = "INFO", "Info"

    pair = models.ForeignKey(
        VehicleInstallationPair, on_delete=models.CASCADE, related_name="audit_logs"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    result = models.CharField(max_length=8, choices=ResultStatus.choices)
    message = models.TextField(blank=True)
    simulated_token = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {self.action} -> {self.result}"
