"""
Evidence Vault ingestion and storage service.

Handles:
- Ingestion batch creation with human-readable identifiers
- Structured directory layout: media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/<uuid>.<ext>
- SHA-256 content hashing and cryptographic deduplication
- Tracking batch-level metrics (total, ingested, duplicate, failed)
"""
import hashlib
import os
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import BinaryIO, Optional, Tuple, Union

from django.conf import settings
from django.utils import timezone

from core.models import EvidenceImage, IngestionBatch

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
HASH_CHUNK_SIZE = 1024 * 1024


def create_ingestion_batch(
    source_type: str = IngestionBatch.SourceType.CLI,
    source_label: str = "",
) -> IngestionBatch:
    """Creates a new IngestionBatch with a human-readable identifier."""
    now = timezone.now()
    batch_prefix = now.strftime("BATCH-%Y%m%d-%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    batch_id = f"{batch_prefix}-{short_uuid}"

    batch = IngestionBatch.objects.create(
        batch_id=batch_id,
        source_type=source_type,
        source_label=source_label or batch_id,
        created_at=now,
    )
    return batch


def get_batch_vault_dir(batch: Optional[IngestionBatch] = None) -> Path:
    """
    Returns the absolute directory path for storing files in the vault.
    Layout: media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/
    """
    vault_root = Path(settings.VAULT_ROOT)
    today_str = date.today().isoformat()

    if batch:
        # Extract time and short id from batch_id (e.g. BATCH-20260907-142030-ab12 -> batch_142030_ab12)
        parts = batch.batch_id.split("-")
        if len(parts) >= 4:
            subfolder_name = f"batch_{parts[2]}_{parts[3]}"
        else:
            subfolder_name = f"batch_{batch.id.hex[:8]}"
        target_dir = vault_root / today_str / subfolder_name
    else:
        target_dir = vault_root / today_str

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def hash_file_path(path: Union[str, Path]) -> str:
    """Computes SHA-256 hash of a local file path."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_stream(stream: BinaryIO) -> str:
    """Computes SHA-256 hash of an open binary stream, resetting seek pointer."""
    digest = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(HASH_CHUNK_SIZE), b""):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def ingest_from_disk(
    path: Union[str, Path],
    batch: Optional[IngestionBatch] = None,
) -> Tuple[Optional[EvidenceImage], str]:
    """
    Ingests a photo from local disk into the vault.
    Returns: (EvidenceImage or None, status_code: "INGESTED" | "DUPLICATE_SKIPPED" | "INVALID_EXT" | "READ_ERROR")
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in VALID_EXTENSIONS:
        if batch:
            batch.total_files += 1
            batch.failed_count += 1
            batch.save(update_fields=["total_files", "failed_count"])
        return None, "INVALID_EXT"

    try:
        file_hash = hash_file_path(path)
    except OSError:
        if batch:
            batch.total_files += 1
            batch.failed_count += 1
            batch.save(update_fields=["total_files", "failed_count"])
        return None, "READ_ERROR"

    # Deduplication check
    existing = EvidenceImage.objects.filter(file_hash=file_hash).first()
    if existing:
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        return existing, "DUPLICATE_SKIPPED"

    target_dir = get_batch_vault_dir(batch)
    vault_filename = f"{uuid.uuid4()}{suffix}"
    vault_abs_path = target_dir / vault_filename
    shutil.copy2(path, vault_abs_path)

    vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")

    image = EvidenceImage.objects.create(
        batch=batch,
        file_hash=file_hash,
        original_source_path=str(path),
        vault_file=vault_relative,
        file_size_bytes=path.stat().st_size,
        status=EvidenceImage.Status.NEW,
    )

    if batch:
        batch.total_files += 1
        batch.ingested_count += 1
        batch.save(update_fields=["total_files", "ingested_count"])

    return image, "INGESTED"


def ingest_uploaded_file(
    uploaded_file,
    batch: Optional[IngestionBatch] = None,
) -> Tuple[Optional[EvidenceImage], str]:
    """
    Ingests an in-memory or temporary UploadedFile (from Django request.FILES).
    Returns: (EvidenceImage or None, status_code: "INGESTED" | "DUPLICATE_SKIPPED" | "INVALID_EXT" | "READ_ERROR")
    """
    name = getattr(uploaded_file, "name", "upload.jpg")
    suffix = Path(name).suffix.lower()
    if suffix not in VALID_EXTENSIONS:
        if batch:
            batch.total_files += 1
            batch.failed_count += 1
            batch.save(update_fields=["total_files", "failed_count"])
        return None, "INVALID_EXT"

    try:
        file_hash = hash_stream(uploaded_file)
    except Exception:
        if batch:
            batch.total_files += 1
            batch.failed_count += 1
            batch.save(update_fields=["total_files", "failed_count"])
        return None, "READ_ERROR"

    existing = EvidenceImage.objects.filter(file_hash=file_hash).first()
    if existing:
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        return existing, "DUPLICATE_SKIPPED"

    target_dir = get_batch_vault_dir(batch)
    vault_filename = f"{uuid.uuid4()}{suffix}"
    vault_abs_path = target_dir / vault_filename

    uploaded_file.seek(0)
    with vault_abs_path.open("wb") as dest:
        for chunk in uploaded_file.chunks():
            dest.write(chunk)

    vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")

    image = EvidenceImage.objects.create(
        batch=batch,
        file_hash=file_hash,
        original_source_path=name,
        vault_file=vault_relative,
        file_size_bytes=vault_abs_path.stat().st_size,
        status=EvidenceImage.Status.NEW,
    )

    if batch:
        batch.total_files += 1
        batch.ingested_count += 1
        batch.save(update_fields=["total_files", "ingested_count"])

    return image, "INGESTED"
