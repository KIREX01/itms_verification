"""
Management command to enforce the evidence vault lifecycle retention policy.

Policy:
  - Evidence photos successfully submitted to ITMS (status=SUBMITTED) have a
    1-week (7-day) retention lifecycle by default.
  - After 7 days, their heavy image files on disk are pruned to free storage,
    while database records (license plate numbers, OCR confidence, timestamps,
    audit history) are permanently preserved and marked status=PRUNED.
  - Evidence photos with issues (FAILED, NEEDS_REVIEW, INCOMPLETE, NEW,
    PROCESSING, PLATE_DETECTED, MATCHED) are STRICTLY PROTECTED and RETAINED
    indefinitely until an operator resolves them.

Usage:
  python manage.py prune_vault
  python manage.py prune_vault --dry-run
  python manage.py prune_vault --days 14
  python manage.py prune_vault --batch BATCH-20260907-142030-ab12
"""
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from core.models import EvidenceImage, IngestionBatch


class Command(BaseCommand):
    help = (
        "Prunes heavy evidence image files older than the retention policy (default: 7 days) "
        "that have been successfully submitted to ITMS, while strictly preserving images with issues."
    )

    def add_arguments(self, parser):
        default_days = getattr(settings, "VAULT_RETENTION_DAYS", 7)
        parser.add_argument(
            "--days", type=int, default=default_days,
            help=f"Retention period in days for submitted evidence (default: {default_days}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Simulate pruning without deleting any files or updating the database.",
        )
        parser.add_argument(
            "--batch", type=str, default=None,
            help="Only consider images belonging to a specific IngestionBatch (batch_id).",
        )
        parser.add_argument(
            "--orphans", action="store_true",
            help="Also scan media/vault/ for orphaned files on disk that have no matching EvidenceImage record in the database.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        batch_id = options.get("batch")
        check_orphans = options.get("orphans", False)

        cutoff = timezone.now() - timedelta(days=days)

        # ── 1. Target queryset: SUBMITTED images submitted before cutoff that haven't been pruned ──
        base_qs = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.SUBMITTED,
            submitted_at__isnull=False,
            submitted_at__lte=cutoff,
            is_file_pruned=False,
        )

        if batch_id:
            try:
                batch_obj = IngestionBatch.objects.get(batch_id=batch_id)
                base_qs = base_qs.filter(batch=batch_obj)
            except IngestionBatch.DoesNotExist:
                raise CommandError(f"IngestionBatch with id '{batch_id}' does not exist.")

        candidates = list(base_qs.order_by("submitted_at"))
        total_candidates = len(candidates)

        # ── 2. Count protected images with issues ──
        protected_issues_count = EvidenceImage.objects.filter(
            Q(status__in=[
                EvidenceImage.Status.FAILED,
                EvidenceImage.Status.NEEDS_REVIEW,
                EvidenceImage.Status.INCOMPLETE,
                EvidenceImage.Status.NEW,
                EvidenceImage.Status.PROCESSING,
                EvidenceImage.Status.PLATE_DETECTED,
                EvidenceImage.Status.MATCHED,
            ]) | Q(submitted_at__isnull=True)
        ).count()

        recent_submitted_count = EvidenceImage.objects.filter(
            status=EvidenceImage.Status.SUBMITTED,
            submitted_at__gt=cutoff,
            is_file_pruned=False,
        ).count()

        self.stdout.write(f"=== Evidence Vault Lifecycle Enforcement ({days}-Day Retention) ===")
        self.stdout.write(f"Cutoff timestamp : {cutoff.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        self.stdout.write(f"Eligible to prune: {total_candidates} submitted image(s)")
        self.stdout.write(f"Retained (recent): {recent_submitted_count} submitted image(s) within {days}-day retention window")
        self.stdout.write(f"Retained (issues): {protected_issues_count} image(s) with pending/unresolved status (permanently protected)\n")

        media_root = Path(settings.MEDIA_ROOT)
        pruned_count = 0
        bytes_freed = 0

        if total_candidates == 0:
            self.stdout.write(self.style.SUCCESS("No expired submitted evidence files to prune."))
        else:
            for img in candidates:
                abs_path = media_root / img.vault_file
                file_size = 0
                if abs_path.is_file():
                    file_size = abs_path.stat().st_size

                if dry_run:
                    self.stdout.write(
                        f"[DRY-RUN] Would prune: {img.vault_file} "
                        f"(plate={img.detected_plate or 'N/A'}, size={round(file_size / 1024, 1)} KB, "
                        f"submitted={img.submitted_at.strftime('%Y-%m-%d')})"
                    )
                    pruned_count += 1
                    bytes_freed += file_size
                else:
                    try:
                        if abs_path.is_file():
                            abs_path.unlink()
                        img.is_file_pruned = True
                        img.pruned_at = timezone.now()
                        img.status = EvidenceImage.Status.PRUNED
                        img.save(update_fields=["is_file_pruned", "pruned_at", "status"])
                        pruned_count += 1
                        bytes_freed += file_size
                        self.stdout.write(
                            f"PRUNED   {img.id} -> {img.vault_file} (plate={img.detected_plate}, freed {round(file_size / 1024, 1)} KB)"
                        )
                    except Exception as exc:
                        self.stderr.write(self.style.ERROR(f"ERROR pruning {img.id}: {exc}"))

        # ── 3. Scan and prune orphaned vault files (if requested) ──
        if check_orphans and Path(settings.VAULT_ROOT).is_dir():
            vault_root = Path(settings.VAULT_ROOT)
            known_vault_files = set(
                EvidenceImage.objects.exclude(is_file_pruned=True)
                .values_list("vault_file", flat=True)
            )
            # Normalize to relative path with forward slashes without leading slash
            known_rel_files = {p.replace("\\", "/").lstrip("/") for p in known_vault_files if p}

            VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
            orphaned_files = []
            for disk_file in vault_root.rglob("*"):
                if disk_file.is_file():
                    # Skip hidden config/session files, non-media files, and protected ITMS photo vault
                    if disk_file.name.startswith(".") or disk_file.suffix.lower() not in VALID_EXTENSIONS:
                        continue
                    rel_str = str(disk_file).replace("\\", "/").lower()
                    if "/itms_photos/" in rel_str or "/itms_downloads/" in rel_str or "/itms_vault/" in rel_str:
                        continue
                    try:
                        rel = str(disk_file.relative_to(Path(settings.MEDIA_ROOT))).replace("\\", "/").lstrip("/")
                    except ValueError:
                        rel = disk_file.name
                    if rel not in known_rel_files:
                        orphaned_files.append((disk_file, rel, disk_file.stat().st_size))

            if orphaned_files:
                self.stdout.write(f"\nDiscovered {len(orphaned_files)} orphaned file(s) on disk in vault:")
                for disk_path, rel_path, size in orphaned_files:
                    if dry_run:
                        self.stdout.write(f"[DRY-RUN] Would delete orphan: {rel_path} ({round(size / 1024, 1)} KB)")
                    else:
                        try:
                            disk_path.unlink()
                            self.stdout.write(f"DELETED ORPHAN  {rel_path} ({round(size / 1024, 1)} KB)")
                        except Exception as exc:
                            self.stderr.write(self.style.ERROR(f"Failed to delete {disk_path}: {exc}"))
                    pruned_count += 1
                    bytes_freed += size
            else:
                self.stdout.write("\nNo orphaned files detected in vault storage.")

        # ── 4. Clean empty directories in media/vault ──
        if not dry_run and Path(settings.VAULT_ROOT).is_dir():
            vault_root = Path(settings.VAULT_ROOT)
            for dirpath, dirnames, filenames in os.walk(vault_root, topdown=False):
                if not filenames and not dirnames and dirpath != str(vault_root):
                    try:
                        os.rmdir(dirpath)
                    except Exception:
                        pass

        mb_freed = round(bytes_freed / (1024 * 1024), 2)
        if dry_run:
            self.stdout.write(self.style.WARNING(f"\n[DRY RUN] {pruned_count} file(s) ({mb_freed} MB) would be pruned/cleaned from disk."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nSuccessfully pruned/cleaned {pruned_count} file(s) ({mb_freed} MB freed)."))
        self.stdout.write(self.style.NOTICE(f"Retained {protected_issues_count} unresolved/issue images indefinitely."))
