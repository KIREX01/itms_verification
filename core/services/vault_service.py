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
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Optional, Tuple, Union

from django.conf import settings
from django.utils import timezone

from core.models import EvidenceImage, IngestionBatch
from core.vision.plate_enhancer import enhance_whole_image

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


def detect_folder_orientation(path: Union[str, Path]) -> str:
    """
    Infers vehicle orientation from directory names or path components.
    Recognizes 'front', 'forward', 'fronts' vs 'rear', 'back', 'rears', 'backs'.
    """
    raw_str = str(path).replace("\\", "/").strip().lower()
    # Check parent directory segments (ignoring the filename itself)
    parts = [seg for seg in raw_str.split("/")[:-1] if seg]
    for part in reversed(parts):
        tokens = [t.strip() for t in part.replace("_", " ").replace("-", " ").split()]
        if any(f in tokens or f == part for f in ("front", "forward", "fronts")):
            return EvidenceImage.Orientation.FRONT
        if any(r in tokens or r == part for r in ("rear", "back", "rears", "backs")):
            return EvidenceImage.Orientation.REAR
        if any(f in part for f in ("front", "forward")):
            return EvidenceImage.Orientation.FRONT
        if any(r in part for r in ("rear", "back")):
            return EvidenceImage.Orientation.REAR

    # Check filename stem fallback
    stem = Path(path).stem.lower()
    stem_tokens = [t.strip() for t in stem.replace("_", " ").replace("-", " ").split()]
    if any(f in stem_tokens for f in ("front", "forward", "fronts")):
        return EvidenceImage.Orientation.FRONT
    if any(r in stem_tokens for r in ("rear", "back", "rears", "backs")):
        return EvidenceImage.Orientation.REAR

    return ""


def extract_exif_timestamp(path: Union[str, Path]) -> Optional[datetime]:
    """Extracts capture timestamp from EXIF DateTimeOriginal with fallback to mtime."""
    from PIL import Image
    from datetime import datetime as _dt
    p = Path(path)
    if not p.exists():
        return None
    try:
        with Image.open(p) as img:
            exif = img.getexif()
            dt_str = exif.get(0x9003) or exif.get(0x0132)
            if not dt_str:
                exif_ifd = exif.get_ifd(0x8769)
                dt_str = exif_ifd.get(0x9003) or exif_ifd.get(0x9004) or exif_ifd.get(0x0132)
            if dt_str:
                dt = _dt.strptime(str(dt_str), "%Y:%m:%d %H:%M:%S")
                return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        pass
    # 2. Check if filename encodes capture timestamp (Samsung, Pixel, Xiaomi, Tecno, WhatsApp)
    try:
        from core.services.camera_naming import parse_camera_filename
        sig = parse_camera_filename(p.name)
        if sig.embedded_datetime:
            return sig.embedded_datetime
    except Exception:
        pass
    # 3. Fallback to file system mtime
    try:
        mtime = p.stat().st_mtime
        dt = _dt.fromtimestamp(mtime)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


def ingest_from_disk(
    path: Union[str, Path],
    batch: Optional[IngestionBatch] = None,
    orientation_override: Optional[str] = None,
) -> Tuple[Optional[EvidenceImage], str]:
    """
    Ingests a photo from local disk into the vault.
    Supports explicit orientation_override ('FRONT' or 'REAR').
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
        if orientation_override and existing.orientation == EvidenceImage.Orientation.UNKNOWN:
            clean_orient = orientation_override.upper()
            if clean_orient in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
                existing.folder_orientation = clean_orient
                existing.orientation = clean_orient
                existing.orientation_confidence = 1.0
                existing.save(update_fields=["folder_orientation", "orientation", "orientation_confidence"])
        return existing, "DUPLICATE_SKIPPED"

    target_dir = get_batch_vault_dir(batch)
    vault_filename = f"{uuid.uuid4()}{suffix}"
    vault_abs_path = target_dir / vault_filename
    shutil.copy2(path, vault_abs_path)

    # Enhance the vault copy in-place: autocontrast, dynamic contrast,
    # saturation boost, sharpness, brightness normalization.
    enhance_whole_image(str(vault_abs_path))

    vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")

    clean_override = orientation_override.upper() if orientation_override else None
    if clean_override in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
        folder_orient = clean_override
        initial_orient = clean_override
        initial_orient_conf = 1.0
    else:
        folder_orient = detect_folder_orientation(path)
        initial_orient = folder_orient if folder_orient else EvidenceImage.Orientation.UNKNOWN
        initial_orient_conf = 1.0 if folder_orient else None

    captured_at = extract_exif_timestamp(path)

    image = EvidenceImage.objects.create(
        batch=batch,
        file_hash=file_hash,
        original_source_path=str(path),
        vault_file=vault_relative,
        file_size_bytes=path.stat().st_size,
        status=EvidenceImage.Status.NEW,
        folder_orientation=folder_orient,
        orientation=initial_orient,
        orientation_confidence=initial_orient_conf,
        captured_at=captured_at,
    )

    if batch:
        batch.total_files += 1
        batch.ingested_count += 1
        batch.save(update_fields=["total_files", "ingested_count"])

    return image, "INGESTED"


def ingest_uploaded_file(
    uploaded_file,
    batch: Optional[IngestionBatch] = None,
    orientation_override: Optional[str] = None,
) -> Tuple[Optional[EvidenceImage], str]:
    """
    Ingests an in-memory or temporary UploadedFile (from Django request.FILES).
    Supports explicit orientation_override ('FRONT' or 'REAR').
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
        if orientation_override and existing.orientation == EvidenceImage.Orientation.UNKNOWN:
            clean_orient = orientation_override.upper()
            if clean_orient in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
                existing.folder_orientation = clean_orient
                existing.orientation = clean_orient
                existing.orientation_confidence = 1.0
                existing.save(update_fields=["folder_orientation", "orientation", "orientation_confidence"])
        return existing, "DUPLICATE_SKIPPED"

    target_dir = get_batch_vault_dir(batch)
    vault_filename = f"{uuid.uuid4()}{suffix}"
    vault_abs_path = target_dir / vault_filename

    uploaded_file.seek(0)
    with vault_abs_path.open("wb") as dest:
        for chunk in uploaded_file.chunks():
            dest.write(chunk)

    # Enhance the vault copy in-place: autocontrast, dynamic contrast,
    # saturation boost, sharpness, brightness normalization.
    enhance_whole_image(str(vault_abs_path))

    vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")

    clean_override = orientation_override.upper() if orientation_override else None
    if clean_override in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
        folder_orient = clean_override
        initial_orient = clean_override
        initial_orient_conf = 1.0
    else:
        folder_orient = detect_folder_orientation(name)
        initial_orient = folder_orient if folder_orient else EvidenceImage.Orientation.UNKNOWN
        initial_orient_conf = 1.0 if folder_orient else None

    # Extract EXIF timestamp from the vaulted file
    captured_at = extract_exif_timestamp(vault_abs_path)

    image = EvidenceImage.objects.create(
        batch=batch,
        file_hash=file_hash,
        original_source_path=name,
        vault_file=vault_relative,
        file_size_bytes=vault_abs_path.stat().st_size,
        status=EvidenceImage.Status.NEW,
        folder_orientation=folder_orient,
        orientation=initial_orient,
        orientation_confidence=initial_orient_conf,
        captured_at=captured_at,
    )

    if batch:
        batch.total_files += 1
        batch.ingested_count += 1
        batch.save(update_fields=["total_files", "ingested_count"])

    return image, "INGESTED"
