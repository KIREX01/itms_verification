"""
Management command to clean up intermediate plate crops and debug images.

Usage:
  python manage.py clean_crops
  python manage.py clean_crops --dry-run
  python manage.py clean_crops --older-than-days 7
"""
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Cleans up intermediate localized plate crops and temporary vision debug images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show files that would be deleted without actually deleting them.",
        )
        parser.add_argument(
            "--older-than-days", type=int, default=0,
            help="Only delete crop files older than N days (default: 0 = delete all temporary crops).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        older_days = options["older_than_days"]
        cutoff_time = time.time() - (older_days * 86400) if older_days > 0 else float("inf")

        deleted_count = 0
        deleted_bytes = 0

        # 1. Clean media/crops directory
        crops_root = Path(getattr(settings, "CROPS_ROOT", settings.MEDIA_ROOT / "crops"))
        if crops_root.is_dir():
            for file_path in crops_root.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                    mtime = file_path.stat().st_mtime
                    if mtime <= cutoff_time or older_days == 0:
                        size = file_path.stat().st_size
                        if dry_run:
                            try:
                                rel_display = file_path.relative_to(settings.BASE_DIR)
                            except ValueError:
                                rel_display = file_path
                            self.stdout.write(f"[DRY-RUN] Would delete: {rel_display}")
                        else:
                            try:
                                file_path.unlink()
                            except Exception as exc:
                                self.stderr.write(f"Could not delete {file_path}: {exc}")
                        deleted_count += 1
                        deleted_bytes += size

            # Clean empty subdirectories in media/crops
            if not dry_run:
                for dirpath, dirnames, filenames in os.walk(crops_root, topdown=False):
                    if not filenames and not dirnames and dirpath != str(crops_root):
                        try:
                            os.rmdir(dirpath)
                        except Exception:
                            pass

        # 2. Sweep project root for any stray debug/test images (e.g. debug_*.jpg, test_*.jpg, front_*.jpg)
        root_dir = Path(settings.BASE_DIR)
        debug_prefixes = ("debug_", "test_", "scratch_", "front_", "rear_")
        for f in root_dir.glob("*"):
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                if any(f.name.startswith(pfx) for pfx in debug_prefixes):
                    size = f.stat().st_size
                    if dry_run:
                        self.stdout.write(f"[DRY-RUN] Would delete stray root image: {f.name}")
                    else:
                        try:
                            f.unlink()
                        except Exception as exc:
                            self.stderr.write(f"Could not delete {f.name}: {exc}")
                    deleted_count += 1
                    deleted_bytes += size

        mb_freed = round(deleted_bytes / (1024 * 1024), 2)
        if dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY RUN] Found {deleted_count} crop/debug file(s) ({mb_freed} MB) to clean."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Cleaned up {deleted_count} temporary crop/debug file(s) ({mb_freed} MB freed)."))
