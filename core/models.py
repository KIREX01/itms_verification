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
        FALLBACK = "FALLBACK", "Manual Fallback Triggered"

    class ResultStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"
        INFO = "INFO", "Info"

    pair = models.ForeignKey(
        VehicleInstallationPair, on_delete=models.CASCADE, related_name="audit_logs"
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    result = models.CharField(max_length=8, choices=ResultStatus.choices)
    message = models.TextField(blank=True)
    simulated_token = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {self.action} -> {self.result}"
