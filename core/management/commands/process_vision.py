"""
Vision pipeline management command.

Processes all pending evidence images through the full vision pipeline:
preprocessing → YOLO/heuristic plate detection → OCR → positional
normalization → front/rear orientation classification.

Automatic retry handling
------------------------
By default the command picks up images in these statuses:
  - NEW         – freshly ingested, never processed.
  - FAILED      – previously failed; will be retried up to --max-retries.
  - PROCESSING  – orphaned by a prior crash/kill; automatically recovered
                   as long as their retry_count is below the limit.

The ``--include-needs-review`` flag also re-processes images stuck in
NEEDS_REVIEW (e.g. after a pipeline bug-fix such as EXIF rotation
support), so operators don't have to manually reset every image.

Each image tracks its own ``retry_count``.  Once an image reaches the
configured maximum (default 3), it is left in FAILED status with a clear
error message and is never automatically retried again — preventing
infinite retry loops.  An operator can still reset retry_count manually
(via admin or the TUI) to force another attempt.
"""
import os
from pathlib import Path

import cv2
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from core.models import EvidenceImage, IngestionBatch
from core.vision import detector, normalizer, ocr_engine, orientation, preprocess

# Default cap — overridable via VISION_MAX_RETRIES in settings or the CLI flag.
_DEFAULT_MAX_RETRIES = int(getattr(settings, "VISION_MAX_RETRIES", 3))

# How long a PROCESSING image must have been untouched before we consider it
# orphaned.  Prevents stealing work from a *currently running* pipeline
# instance (e.g. on another node or in a parallel terminal).
_STALE_PROCESSING_SECONDS = int(getattr(settings, "VISION_STALE_PROCESSING_SECONDS", 300))


class Command(BaseCommand):
    help = (
        "Vision pipeline: runs preprocessing, YOLO/heuristic plate detection, "
        "OCR, positional normalization, and front/rear orientation classification "
        "over all pending vaulted evidence images.  Automatically retries FAILED "
        "and orphaned-PROCESSING images up to --max-retries times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Process at most N images (useful for testing on a small batch first).",
        )
        parser.add_argument(
            "--reprocess-failed", action="store_true",
            help="Force reprocessing of images currently in FAILED status (resets their retry count).",
        )
        parser.add_argument(
            "--include-needs-review", action="store_true",
            help=(
                "Also reprocess images in NEEDS_REVIEW status (useful after a "
                "pipeline improvement, e.g. EXIF rotation or OCR fix, so they get "
                "another chance without manual resets)."
            ),
        )
        parser.add_argument(
            "--reprocess-all", action="store_true",
            help="Reprocess all unsubmitted images (NEW, FAILED, and NEEDS_REVIEW) and reset retry counts.",
        )
        parser.add_argument(
            "--max-retries", type=int, default=_DEFAULT_MAX_RETRIES,
            help=(
                f"Maximum number of processing attempts per image before giving "
                f"up (default: {_DEFAULT_MAX_RETRIES}). Images at or above this "
                f"count are skipped."
            ),
        )
        parser.add_argument(
            "--save-crops", action="store_true",
            help="Save localized plate crops to media/crops/ for visual inspection and audit.",
        )
        parser.add_argument(
            "--cleanup-crops", action="store_true",
            help="Clean up temporary crop files in media/crops/ after processing completes.",
        )
        parser.add_argument(
            "--batch", type=str, default=None,
            help="Process only images belonging to a specific IngestionBatch (batch_id).",
        )

    def handle(self, *args, **options):
        max_retries = options["max_retries"]

        # If user passed --reprocess-failed, reset retry_count on all FAILED images
        if options["reprocess_failed"]:
            failed_reset = EvidenceImage.objects.filter(status=EvidenceImage.Status.FAILED).update(retry_count=0)
            if failed_reset:
                self.stdout.write(f"Reset retry budget for {failed_reset} FAILED image(s).")

        # If user passed --reprocess-all, reset retry_count on all unsubmitted images
        if options.get("reprocess_all"):
            all_reset = EvidenceImage.objects.exclude(
                status=EvidenceImage.Status.SUBMITTED
            ).update(retry_count=0)
            if all_reset:
                self.stdout.write(f"Reset retry budget for {all_reset} image(s).")

        # ── 1. Recover orphaned PROCESSING images ──────────────────────
        stale_cutoff = timezone.now() - timezone.timedelta(seconds=_STALE_PROCESSING_SECONDS)
        stale_qs = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.PROCESSING,
            processed_at__isnull=True,           # never finished
            ingested_at__lt=stale_cutoff,         # ingested long enough ago
        ) | EvidenceImage.objects.filter(
            status=EvidenceImage.Status.PROCESSING,
            processed_at__lt=stale_cutoff,        # or finished long ago (stale)
        )
        stale_count = stale_qs.count()
        if stale_count:
            stale_qs.update(status=EvidenceImage.Status.FAILED, error_message="Recovered from orphaned PROCESSING state.")
            self.stdout.write(
                self.style.WARNING(f"Recovered {stale_count} orphaned PROCESSING image(s).")
            )

        # ── 2. Build the processable queryset ──────────────────────────
        # Pending images:
        # 1. NEW (freshly ingested)
        # 2. FAILED (retry_count < max_retries, or forced via --reprocess-failed)
        # 3. NEEDS_REVIEW with no detected plate (retry_count < max_retries) -- left out from vision
        # 4. If --include-needs-review: all NEEDS_REVIEW
        # 5. If --reprocess-all: all unsubmitted images
        if options.get("reprocess_all"):
            base_filter = ~Q(status=EvidenceImage.Status.SUBMITTED)
        else:
            base_filter = (
                Q(status=EvidenceImage.Status.NEW) |
                Q(status=EvidenceImage.Status.FAILED) |
                Q(status=EvidenceImage.Status.NEEDS_REVIEW, detected_plate="")
            )
            if options["include_needs_review"]:
                base_filter = base_filter | Q(status=EvidenceImage.Status.NEEDS_REVIEW)

        batch_arg = options.get("batch")
        if batch_arg:
            try:
                batch_obj = IngestionBatch.objects.get(batch_id=batch_arg)
                base_filter = base_filter & Q(batch=batch_obj)
                self.stdout.write(f"Filtering to IngestionBatch: {batch_obj.batch_id}")
            except IngestionBatch.DoesNotExist:
                raise CommandError(f"IngestionBatch with id '{batch_arg}' does not exist.")

        if options.get("reprocess_all"):
            qs = EvidenceImage.objects.filter(base_filter).order_by("ingested_at")
        elif options.get("reprocess_failed"):
            # Include images matching base_filter where FAILED are force-retried
            qs = EvidenceImage.objects.filter(
                base_filter & (Q(status=EvidenceImage.Status.FAILED) | Q(retry_count__lt=max_retries))
            ).order_by("ingested_at")
        else:
            qs = EvidenceImage.objects.filter(base_filter, retry_count__lt=max_retries).order_by("ingested_at")

        if options["limit"]:
            qs = qs[: options["limit"]]

        total = qs.count() if hasattr(qs, "count") else len(qs)
        if total == 0:
            self.stdout.write(self.style.WARNING("No pending images to process."))
            self._print_stuck_summary(max_retries)
            return

        self.stdout.write(f"Processing {total} image(s) (max_retries={max_retries})...\n")

        processed, plate_found, failed, skipped = 0, 0, 0, 0

        for image in qs:
            # ── Guard: double-check retry budget (race-safe) ───────────
            if image.retry_count >= max_retries:
                skipped += 1
                continue

            image.status = EvidenceImage.Status.PROCESSING
            image.retry_count += 1
            image.error_message = ""
            image.save(update_fields=["status", "retry_count", "error_message"])

            abs_path = os.path.join(settings.MEDIA_ROOT, image.vault_file)
            try:
                self._process_one(image, abs_path, save_crops=options.get("save_crops", False))
                processed += 1
                if image.detected_plate:
                    plate_found += 1
            except Exception as exc:  # noqa: BLE001 - want to log any vision failure without killing the batch
                image.status = EvidenceImage.Status.FAILED
                image.error_message = str(exc)
                if image.retry_count >= max_retries:
                    image.error_message += f" [max retries ({max_retries}) exhausted]"
                image.save(update_fields=["status", "error_message"])
                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED  {image.id} (attempt {image.retry_count}/{max_retries}): {exc}"
                    )
                )
                failed += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Vision pipeline complete: {processed} processed, "
                f"{plate_found} plates found, {failed} failed, {skipped} skipped (exhausted retries)."
            )
        )
        self._print_stuck_summary(max_retries)

        if options.get("cleanup_crops"):
            self.stdout.write("\nCleaning up temporary crops as requested...")
            call_command("clean_crops", stdout=self.stdout, stderr=self.stderr)

    def _process_one(self, image: EvidenceImage, abs_path: str, save_crops: bool = False):
        raw = preprocess.load_image(abs_path)
        pre = preprocess.preprocess_pipeline(raw)

        detection = detector.detect_plate(pre)
        if detection is None:
            image.status = EvidenceImage.Status.NEEDS_REVIEW
            image.error_message = "No plate candidate detected."
            image.processed_at = timezone.now()
            image.save(update_fields=["status", "error_message", "processed_at"])
            self.stdout.write(
                f"NO_PLATE    {image.id}  (attempt {image.retry_count})"
            )
            return

        crop = detector.crop_detection(pre, detection)
        if save_crops and crop is not None:
            crops_root = Path(getattr(settings, "CROPS_ROOT", settings.MEDIA_ROOT / "crops"))
            date_dir = crops_root / timezone.now().strftime("%Y-%m-%d")
            date_dir.mkdir(parents=True, exist_ok=True)
            crop_path = date_dir / f"{image.id}_plate.jpg"
            cv2.imwrite(str(crop_path), crop)

        ocr_result = ocr_engine.read_plate_text(crop)

        plate_text, ocr_conf = "", None
        if ocr_result is not None:
            norm = normalizer.normalize_plate(ocr_result.text)
            plate_text = norm["canonical"]
            ocr_conf = ocr_result.confidence

        orient_result = orientation.classify_orientation(pre)

        image.detected_plate = plate_text
        image.ocr_confidence = ocr_conf
        image.detector_confidence = detection.confidence
        image.bbox = detection.bbox
        image.orientation = orient_result.orientation
        image.orientation_confidence = orient_result.confidence
        image.processed_at = timezone.now()

        min_conf = getattr(settings, "OCR_MIN_CONFIDENCE", 0.55)
        if plate_text and (ocr_conf is None or ocr_conf >= min_conf):
            image.status = EvidenceImage.Status.PLATE_DETECTED
        else:
            image.status = EvidenceImage.Status.NEEDS_REVIEW

        image.save()
        self.stdout.write(
            f"{image.status:<14} {image.id}  plate={plate_text or '???'}  "
            f"orient={orient_result.orientation}({orient_result.confidence})  "
            f"attempt={image.retry_count}"
        )

    def _print_stuck_summary(self, max_retries: int):
        """Print a summary of images that are stuck and won't be retried automatically."""
        exhausted = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.FAILED,
            retry_count__gte=max_retries,
        ).count()
        needs_review = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.NEEDS_REVIEW,
        ).count()
        stale_processing = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.PROCESSING,
        ).count()

        if exhausted or needs_review or stale_processing:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("-- Stuck images summary --"))
            if exhausted:
                self.stdout.write(
                    self.style.ERROR(
                        f"  {exhausted} image(s) exhausted all {max_retries} retries (FAILED, won't auto-retry)."
                    )
                )
            if needs_review:
                self.stdout.write(
                    f"  {needs_review} image(s) in NEEDS_REVIEW (use --include-needs-review to reprocess)."
                )
            if stale_processing:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {stale_processing} image(s) still PROCESSING (may be in-flight or orphaned; "
                        f"will be auto-recovered on next run after {_STALE_PROCESSING_SECONDS}s)."
                    )
                )
