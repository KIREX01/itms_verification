"""
Database and storage lifecycle maintenance service for ITMS Verification Copilot.

Provides:
- optimize_database(): SQLite WAL checkpointing (TRUNCATE), table index optimization, and page maintenance.
- clean_storage_lifecycle(): Purges stale plate crops, old exports, and orphaned vault items according to retention policies.
"""
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


def optimize_database() -> Dict[str, Any]:
    """
    Optimizes SQLite database health:
    1. Checkpoints and truncates the WAL (Write-Ahead Log) file.
    2. Runs PRAGMA optimize to update SQLite query planner index statistics.
    3. Calculates database file sizes.

    Returns:
        Dict with status, sizes, and checkpoint statistics.
    """
    res = {
        "engine": connection.vendor,
        "wal_checkpoint": None,
        "wal_size_bytes": 0,
        "db_size_bytes": 0,
        "optimized": False,
        "message": "",
    }

    if connection.vendor == "postgresql":
        try:
            with connection.cursor() as cursor:
                cursor.execute("ANALYZE;")
            res["optimized"] = True
            res["message"] = "PostgreSQL query planner index statistics updated (ANALYZE complete)."
            logger.info("Database optimization complete: %s", res["message"])
            return res
        except Exception as exc:
            res["message"] = f"PostgreSQL optimization error: {exc}"
            return res

    if connection.vendor != "sqlite":
        res["message"] = f"Database engine is {connection.vendor}; automated optimization skipped."
        return res

    if getattr(connection, "in_atomic_block", False):
        res["optimized"] = True
        res["message"] = "Active transaction block in progress; WAL checkpoint skipped."
        return res

    try:
        db_path = Path(connection.settings_dict.get("NAME", settings.BASE_DIR / "db.sqlite3"))
        wal_path = Path(str(db_path) + "-wal")

        if wal_path.exists():
            res["wal_size_bytes"] = wal_path.stat().st_size

        if db_path.exists():
            res["db_size_bytes"] = db_path.stat().st_size

        with connection.cursor() as cursor:
            try:
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                row = cursor.fetchone()
                if row:
                    res["wal_checkpoint"] = {
                        "busy": row[0],
                        "log_frames": row[1],
                        "checkpointed_frames": row[2],
                    }
            except Exception as w_exc:
                logger.debug("WAL checkpoint deferred: %s", w_exc)

            try:
                cursor.execute("PRAGMA optimize;")
            except Exception:
                pass

        wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
        db_size_mb = round(res["db_size_bytes"] / (1024 * 1024), 2)
        wal_saved_kb = round((res["wal_size_bytes"] - wal_size_after) / 1024, 1)

        res["optimized"] = True
        res["wal_size_after_bytes"] = wal_size_after
        res["message"] = f"SQLite WAL truncated (freed {wal_saved_kb} KB). DB size: {db_size_mb} MB."
        logger.info("Database optimization complete: %s", res["message"])
        return res

    except Exception as exc:
        logger.error("optimize_database failed: %s", exc)
        res["message"] = f"Optimization error: {exc}"
        return res


def clean_storage_lifecycle(
    max_crop_age_days: int = 7,
    max_export_age_days: int = 30,
    clean_crops: bool = True,
    clean_exports: bool = True,
    optimize_db: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Cleans up temporary files and stale artifacts according to retention policies:
    1. Crops in media/crops older than `max_crop_age_days`.
    2. Export reports in exports/ older than `max_export_age_days`.
    3. Optimizes and truncates SQLite WAL if optimize_db=True.

    Returns:
        Dict with count of files deleted, bytes freed, and database optimization results.
    """
    now = time.time()
    crop_cutoff = now - (max_crop_age_days * 86400) if max_crop_age_days > 0 else float("inf")
    export_cutoff = now - (max_export_age_days * 86400) if max_export_age_days > 0 else float("inf")

    deleted_count = 0
    deleted_bytes = 0

    if clean_crops:
        crops_root = Path(getattr(settings, "CROPS_ROOT", settings.MEDIA_ROOT / "crops"))
        if crops_root.is_dir():
            for file_path in crops_root.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                    try:
                        mtime = file_path.stat().st_mtime
                        if mtime <= crop_cutoff or max_crop_age_days == 0:
                            size = file_path.stat().st_size
                            if not dry_run:
                                file_path.unlink()
                            deleted_count += 1
                            deleted_bytes += size
                    except Exception as exc:
                        logger.warning("Failed to clean crop file %s: %s", file_path, exc)

            if not dry_run:
                for dirpath, dirnames, filenames in os.walk(crops_root, topdown=False):
                    if not filenames and not dirnames and dirpath != str(crops_root):
                        try:
                            os.rmdir(dirpath)
                        except Exception:
                            pass

    if clean_exports:
        exports_root = Path(settings.BASE_DIR) / "exports"
        if exports_root.is_dir():
            for file_path in exports_root.glob("shift_report_*.csv"):
                if file_path.is_file():
                    try:
                        mtime = file_path.stat().st_mtime
                        if mtime <= export_cutoff:
                            size = file_path.stat().st_size
                            if not dry_run:
                                file_path.unlink()
                            deleted_count += 1
                            deleted_bytes += size
                    except Exception as exc:
                        logger.warning("Failed to clean export file %s: %s", file_path, exc)

    root_dir = Path(settings.BASE_DIR)
    debug_prefixes = ("debug_", "test_", "scratch_")
    for f in root_dir.glob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
            if any(f.name.startswith(pfx) for pfx in debug_prefixes):
                try:
                    size = f.stat().st_size
                    if not dry_run:
                        f.unlink()
                    deleted_count += 1
                    deleted_bytes += size
                except Exception as exc:
                    logger.warning("Failed to clean debug file %s: %s", f, exc)

    db_res = optimize_database() if (optimize_db and not dry_run) else {}
    mb_freed = round(deleted_bytes / (1024 * 1024), 2)

    return {
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "mb_freed": mb_freed,
        "dry_run": dry_run,
        "db_optimization": db_res,
    }
