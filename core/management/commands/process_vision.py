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
        parser.add_argument(
            "--joint-pairs", action="store_true",
            help="Process candidate pairs using the Dual-Stream Joint Vision Pipeline (cross-validation, PSV/PMO color check, syntax resolution).",
        )
        parser.add_argument(
            "--single-only", action="store_true",
            help="Bypass pair-first processing and only run isolated single-image vision.",
        )
        parser.add_argument(
            "--no-auto-pair", action="store_true",
            help="Skip pre-vision physical pair association (U-Turn / Filename matching).",
        )

    def handle(self, *args, **options):
        batch_arg = options.get("batch")
        batch_obj = None
        if batch_arg:
            try:
                batch_obj = IngestionBatch.objects.get(batch_id=batch_arg)
                self.stdout.write(f"Scoped to IngestionBatch: {batch_obj.batch_id}")
            except IngestionBatch.DoesNotExist:
                raise CommandError(f"IngestionBatch with id '{batch_arg}' does not exist.")

        # ── Phase 1: Physical Pre-Pairing (U-Turn Walk & Filename Sequence) ──
        if not options.get("single_only") and not options.get("no_auto_pair"):
            from core.matcher import association
            self.stdout.write("Phase 1: Establishing physical pairs via U-Turn trajectory & Filename stems...")
            assoc_summary = association.run_association(batch_id=batch_arg)
            if assoc_summary.complete_pairs > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {assoc_summary.complete_pairs} physical pair(s) aligned (U-Turn / Filenames).\n"
                    )
                )

        # ── Phase 2: Dual-Stream Joint Vision on Formed Pairs ─────────────
        if not options.get("single_only"):
            from core.vision.joint_pipeline import DualStreamVisionEngine
            from core.models import VehicleInstallationPair

            pair_qs = VehicleInstallationPair.objects.filter(
                front_image__isnull=False, rear_image__isnull=False
            ).exclude(
                verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
            )
            if batch_arg:
                pair_qs = pair_qs.filter(
                    Q(front_image__batch__batch_id=batch_arg) |
                    Q(rear_image__batch__batch_id=batch_arg)
                )

            # Process pairs that need vision (e.g. placeholder PAIR-, unanalyzed, or force reprocess)
            if not options.get("reprocess_all") and not options.get("joint_pairs"):
                pair_qs = pair_qs.filter(
                    Q(registration_number_detected__startswith="PAIR-") |
                    Q(front_image__status__in=[EvidenceImage.Status.NEW, EvidenceImage.Status.PROCESSING, EvidenceImage.Status.MATCHED]) |
                    Q(rear_image__status__in=[EvidenceImage.Status.NEW, EvidenceImage.Status.PROCESSING, EvidenceImage.Status.MATCHED]) |
                    Q(verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE) |
                    Q(verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT)
                )

            total_pairs = pair_qs.count()
            if total_pairs > 0:
                self.stdout.write(f"Phase 2: Processing {total_pairs} pair(s) with Dual-Stream Joint Vision (YOLOv8 + Consensus)...")
                engine = DualStreamVisionEngine(save_crops=options.get("save_crops", False))
                reconciled, conflicts, errors = 0, 0, 0

                for pair in pair_qs:
                    try:
                        res = engine.process_pair(pair)
                        if res.success:
                            reconciled += 1
                            status_style = self.style.SUCCESS
                        elif res.reconciliation_status in ("COLOR_CONFLICT", "PLATE_MISMATCH"):
                            conflicts += 1
                            status_style = self.style.ERROR
                        else:
                            errors += 1
                            status_style = self.style.WARNING

                        self.stdout.write(
                            status_style(
                                f"  Pair #{pair.id:<4} [{res.reconciliation_status:<18}] Plate={res.plate_number or '???'} "
                                f"Cat={res.vehicle_category} Conf={res.consensus_conf:.2f} "
                                f"(Front={res.front_plate_raw or '—'} [{res.front_color}] ↔ Rear={res.rear_plate_raw or '—'} [{res.rear_color}])"
                            )
                        )
                    except Exception as exc:
                        errors += 1
                        self.stderr.write(self.style.ERROR(f"  Pair #{pair.id:<4} [ERROR] Joint vision failed: {exc}"))

                self.stdout.write(
                    self.style.SUCCESS(
                        f"\nDual-Stream Vision Complete: {reconciled} reconciled & verified, "
                        f"{conflicts} conflicts flagged, {errors} issues.\n"
                    )
                )

            if options.get("joint_pairs"):
                return

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

        # ── 1b. Recover images that failed solely due to 'processed' variable error ──
        unbound_err_qs = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.FAILED,
            error_message__icontains="cannot access local variable 'processed'",
        )
        for img in unbound_err_qs:
            if img.detected_plate:
                img.status = EvidenceImage.Status.PLATE_DETECTED
                img.error_message = ""
                img.save(update_fields=["status", "error_message"])
            else:
                img.status = EvidenceImage.Status.NEW
                img.error_message = ""
                img.retry_count = 0
                img.save(update_fields=["status", "error_message", "retry_count"])

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
                Q(status=EvidenceImage.Status.INCOMPLETE, detected_plate="") |
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

        processed = 0
        plate_found = 0
        failed = 0
        skipped = 0

        image_stream = qs.iterator(chunk_size=50) if hasattr(qs, "iterator") else qs
        for image in image_stream:
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
                image.processed_at = timezone.now()
                if image.retry_count >= max_retries:
                    image.error_message += f" [max retries ({max_retries}) exhausted]"
                image.save(update_fields=["status", "error_message", "processed_at"])
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

        # ── Phase 4: Order Matching & Registry Reconciliation ───────────
        try:
            from core.matcher import order_matcher
            matched_orders = order_matcher.match_all_pending_pairs()
            if matched_orders > 0:
                self.stdout.write(
                    self.style.SUCCESS(f"Phase 4: Reconciled {matched_orders} pair(s) against active ITMS installation orders.")
                )
        except Exception as err:
            self.stderr.write(f"Warning: Order matching reconciliation error: {err}")

        if options.get("cleanup_crops"):
            self.stdout.write("\nCleaning up temporary crops as requested...")
            call_command("clean_crops", stdout=self.stdout, stderr=self.stderr)

    def _process_one(self, image: EvidenceImage, abs_path: str, save_crops: bool = False):
        raw = preprocess.load_image(abs_path)
        pre = preprocess.preprocess_pipeline(raw)

        # Orientation: preserve folder ground-truth if known, otherwise classify
        if image.folder_orientation:
            image.orientation = image.folder_orientation
            image.orientation_confidence = 1.0
            orient_result = orientation.classify_orientation(pre)  # still compute for forensic scoring
        else:
            orient_result = orientation.classify_orientation(pre)
            image.orientation = orient_result.orientation
            image.orientation_confidence = orient_result.confidence

        detection = detector.detect_plate(pre)
        if detection is None:
            image.status = EvidenceImage.Status.NEEDS_REVIEW
            image.error_message = "No plate candidate detected."
            image.detected_plate = ""
            image.ocr_confidence = None
            image.detector_confidence = None
            image.bbox = None
            image.processed_at = timezone.now()
            image.save(update_fields=[
                "status", "error_message", "detected_plate", "ocr_confidence",
                "detector_confidence", "bbox", "orientation", "orientation_confidence", "processed_at"
            ])
            self.stdout.write(
                f"NO_PLATE    {image.id}  orient={orient_result.orientation}({orient_result.confidence})  (attempt {image.retry_count})"
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

        plate_text, ocr_conf, is_valid = "", None, False
        if ocr_result is not None:
            norm = normalizer.normalize_plate(ocr_result.text)
            plate_text = norm["canonical"]
            is_valid = norm["is_valid"]
            ocr_conf = ocr_result.confidence

        image.detected_plate = (plate_text or "")[:32]
        image.ocr_confidence = ocr_conf
        image.detector_confidence = detection.confidence
        image.bbox = detection.bbox
        image.processed_at = timezone.now()

        min_conf = getattr(settings, "OCR_MIN_CONFIDENCE", 0.55)
        if plate_text and is_valid and (ocr_conf is None or ocr_conf >= min_conf):
            image.status = EvidenceImage.Status.PLATE_DETECTED
            image.error_message = ""
            # Propagate newly detected plate to placeholder/incomplete pair if applicable
            from core.models import VehicleInstallationPair
            pair = VehicleInstallationPair.objects.filter(
                Q(front_image=image) | Q(rear_image=image),
                registration_number_detected__startswith="UNPAIRED-",
            ).first()
            if pair:
                pair.registration_number_detected = plate_text
                pair.save(update_fields=["registration_number_detected"])
        else:
            image.status = EvidenceImage.Status.NEEDS_REVIEW
            if not ocr_result:
                image.error_message = "Plate localized but OCR extracted no readable text."
            elif not is_valid:
                raw_txt = ocr_result.text if ocr_result else ""
                image.error_message = f"Invalid plate syntax '{plate_text or raw_txt}'"
            elif ocr_conf is not None and ocr_conf < min_conf:
                image.error_message = f"OCR confidence {ocr_conf:.2f} below threshold {min_conf:.2f}"

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
