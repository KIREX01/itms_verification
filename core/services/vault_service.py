"""
Evidence Vault ingestion and storage service.

Handles:
- Ingestion batch creation with human-readable identifiers
- Structured directory layout: media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/<uuid>.<ext>
- SHA-256 content hashing and cryptographic deduplication
- Tracking batch-level metrics (total, ingested, duplicate, failed)
"""
import hashlib
import logging
import os
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Optional, Tuple, Union

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from core.models import EvidenceImage, IngestionBatch
from core.vision.plate_enhancer import enhance_whole_image

logger = logging.getLogger(__name__)

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


def get_vault_root() -> Path:
    """
    Returns the absolute directory path to the active Evidence Vault.
    Reads storage.vault_path from secure/config.json with fallback to settings.VAULT_ROOT.
    Ensures directory exists and keeps settings.VAULT_ROOT synchronized.
    """
    from core.services import config_service
    configured_path = config_service.get_setting("storage.vault_path", None)
    if configured_path and isinstance(configured_path, (str, Path)) and configured_path not in ("media/vault", "media\\vault"):
        p = Path(configured_path)
        if not p.is_absolute():
            base = getattr(settings, "BASE_DIR", None) or Path(__file__).resolve().parent.parent.parent
            p = (Path(base) / p).resolve()
    else:
        media_dir = Path(getattr(settings, "MEDIA_ROOT", Path(__file__).resolve().parent.parent.parent / "media"))
        vault_subdir = getattr(settings, "VAULT_SUBDIR", "vault")
        p = Path(getattr(settings, "VAULT_ROOT", media_dir / vault_subdir))

    p.mkdir(parents=True, exist_ok=True)
    try:
        settings.VAULT_ROOT = p
    except Exception:
        pass
    return p


def set_vault_root(new_path: Union[str, Path]) -> Path:
    """
    Updates and persists the Evidence Vault storage location in secure/config.json.
    """
    from core.services import config_service
    p = Path(new_path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    config_service.set_setting("storage.vault_path", str(p))
    try:
        settings.VAULT_ROOT = p
    except Exception:
        pass
    return p


def resolve_vault_path(rel_or_abs: Union[str, Path]) -> Path:
    """
    Safely resolves an EvidenceImage vault_file path to an absolute path on disk,
    checking active vault_root, MEDIA_ROOT, and absolute paths.
    """
    if not rel_or_abs:
        return get_vault_root()

    p = Path(rel_or_abs)
    if p.is_absolute() and p.exists():
        return p

    vault_root = get_vault_root()
    clean_str = str(rel_or_abs).replace("\\", "/").lstrip("/")

    # Check 1: direct in active vault root
    direct_vault = vault_root / clean_str
    if direct_vault.exists():
        return direct_vault

    # Check 2: strip 'vault/' prefix if path has it
    if clean_str.startswith("vault/"):
        sub = clean_str[6:]
        sub_vault = vault_root / sub
        if sub_vault.exists():
            return sub_vault

    # Check 3: fallback to Django settings.MEDIA_ROOT
    media_path = Path(settings.MEDIA_ROOT) / clean_str
    if media_path.exists():
        return media_path

    return direct_vault


def get_batch_vault_dir(batch: Optional[IngestionBatch] = None) -> Path:
    """
    Returns the absolute directory path for storing files in the vault.
    Layout: media/vault/YYYY-MM-DD/batch_HHMMSS_<id>/
    """
    vault_root = get_vault_root()
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


def generate_thumbnails(vault_abs_path: Union[str, Path]) -> Tuple[str, str]:
    """Generates small (160x120) and medium (640x480) WebP thumbnails for an image in the vault.
    Returns: (thumb_relative_path, preview_relative_path)
    """
    from PIL import Image
    src_path = Path(vault_abs_path)
    if not src_path.is_file():
        return "", ""

    target_dir = src_path.parent
    stem = src_path.stem
    thumb_path = target_dir / f"{stem}_sm.webp"
    preview_path = target_dir / f"{stem}_md.webp"

    try:
        with Image.open(src_path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                rgb_img = img.convert("RGB")
            else:
                rgb_img = img

            # 1. Medium Preview (640x480 max bounds)
            if not preview_path.exists():
                preview = rgb_img.copy()
                preview.thumbnail((640, 480), Image.Resampling.LANCZOS)
                preview.save(preview_path, format="WEBP", quality=80, method=4)

            # 2. Small Thumbnail (160x120 max bounds)
            if not thumb_path.exists():
                thumb = rgb_img.copy()
                thumb.thumbnail((160, 120), Image.Resampling.BILINEAR)
                thumb.save(thumb_path, format="WEBP", quality=75, method=2)

        try:
            thumb_rel = str(thumb_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")
        except ValueError:
            thumb_rel = str(thumb_path.resolve()).replace("\\", "/")

        try:
            preview_rel = str(preview_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")
        except ValueError:
            preview_rel = str(preview_path.resolve()).replace("\\", "/")

        return thumb_rel, preview_rel
    except Exception as exc:
        logger.warning("Failed to generate thumbnails for %s: %s", src_path, exc)
        return "", ""


def ensure_image_thumbnails(image: EvidenceImage) -> Tuple[str, str]:
    """Ensures thumbnail_file and preview_file exist for the given EvidenceImage. Generates if missing."""
    if image.thumbnail_file and image.preview_file:
        return image.thumbnail_file, image.preview_file
    abs_path = resolve_vault_path(image.vault_file)
    if not abs_path.is_file():
        return "", ""
    thumb_rel, preview_rel = generate_thumbnails(abs_path)
    update_fields = []
    if thumb_rel and not image.thumbnail_file:
        image.thumbnail_file = thumb_rel
        update_fields.append("thumbnail_file")
    if preview_rel and not image.preview_file:
        image.preview_file = preview_rel
        update_fields.append("preview_file")
    if update_fields:
        image.save(update_fields=update_fields)
    return image.thumbnail_file, image.preview_file


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

    # Compute final SHA-256 cryptographic hash and file size of the immutable vault file
    final_file_hash = hash_file_path(vault_abs_path)
    final_size_bytes = vault_abs_path.stat().st_size

    # Deduplication guard: Check if the enhanced image matches an existing vault record
    existing_final = EvidenceImage.objects.filter(file_hash=final_file_hash).first()
    if existing_final:
        vault_abs_path.unlink(missing_ok=True)
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        if orientation_override and existing_final.orientation == EvidenceImage.Orientation.UNKNOWN:
            clean_orient = orientation_override.upper()
            if clean_orient in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
                existing_final.folder_orientation = clean_orient
                existing_final.orientation = clean_orient
                existing_final.orientation_confidence = 1.0
                existing_final.save(update_fields=["folder_orientation", "orientation", "orientation_confidence"])
        return existing_final, "DUPLICATE_SKIPPED"

    try:
        vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")
    except ValueError:
        vault_relative = str(vault_abs_path.resolve()).replace("\\", "/")

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
    thumb_rel, preview_rel = generate_thumbnails(vault_abs_path)

    try:
        image = EvidenceImage.objects.create(
            batch=batch,
            file_hash=final_file_hash,
            original_source_path=str(path),
            vault_file=vault_relative,
            thumbnail_file=thumb_rel,
            preview_file=preview_rel,
            file_size_bytes=final_size_bytes,
            status=EvidenceImage.Status.NEW,
            folder_orientation=folder_orient,
            orientation=initial_orient,
            orientation_confidence=initial_orient_conf,
            captured_at=captured_at,
        )
    except IntegrityError:
        vault_abs_path.unlink(missing_ok=True)
        if thumb_rel:
            try:
                resolve_vault_path(thumb_rel).unlink(missing_ok=True)
            except Exception:
                pass
        if preview_rel:
            try:
                resolve_vault_path(preview_rel).unlink(missing_ok=True)
            except Exception:
                pass
        existing_dup = EvidenceImage.objects.filter(file_hash=final_file_hash).first()
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        return existing_dup, "DUPLICATE_SKIPPED"

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

    final_file_hash = hash_file_path(vault_abs_path)
    final_size_bytes = vault_abs_path.stat().st_size

    # Deduplication guard: Check if the enhanced image matches an existing vault record
    existing_final = EvidenceImage.objects.filter(file_hash=final_file_hash).first()
    if existing_final:
        vault_abs_path.unlink(missing_ok=True)
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        if orientation_override and existing_final.orientation == EvidenceImage.Orientation.UNKNOWN:
            clean_orient = orientation_override.upper()
            if clean_orient in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
                existing_final.folder_orientation = clean_orient
                existing_final.orientation = clean_orient
                existing_final.orientation_confidence = 1.0
                existing_final.save(update_fields=["folder_orientation", "orientation", "orientation_confidence"])
        return existing_final, "DUPLICATE_SKIPPED"

    try:
        vault_relative = str(vault_abs_path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")
    except ValueError:
        vault_relative = str(vault_abs_path.resolve()).replace("\\", "/")

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
    thumb_rel, preview_rel = generate_thumbnails(vault_abs_path)

    try:
        image = EvidenceImage.objects.create(
            batch=batch,
            file_hash=final_file_hash,
            original_source_path=name,
            vault_file=vault_relative,
            thumbnail_file=thumb_rel,
            preview_file=preview_rel,
            file_size_bytes=final_size_bytes,
            status=EvidenceImage.Status.NEW,
            folder_orientation=folder_orient,
            orientation=initial_orient,
            orientation_confidence=initial_orient_conf,
            captured_at=captured_at,
        )
    except IntegrityError:
        vault_abs_path.unlink(missing_ok=True)
        if thumb_rel:
            try:
                resolve_vault_path(thumb_rel).unlink(missing_ok=True)
            except Exception:
                pass
        if preview_rel:
            try:
                resolve_vault_path(preview_rel).unlink(missing_ok=True)
            except Exception:
                pass
        existing_dup = EvidenceImage.objects.filter(file_hash=final_file_hash).first()
        if batch:
            batch.total_files += 1
            batch.duplicate_count += 1
            batch.save(update_fields=["total_files", "duplicate_count"])
        return existing_dup, "DUPLICATE_SKIPPED"

    if batch:
        batch.total_files += 1
        batch.ingested_count += 1
        batch.save(update_fields=["total_files", "ingested_count"])

    return image, "INGESTED"


def get_or_create_mobile_batch(
    source_label: str = "",
    bike_client_id: str = "",
    force_new: bool = False,
    max_photos: Optional[int] = None,
) -> IngestionBatch:
    """
    Finds or creates today's active MOBILE batch with a 200-photo limit (100 pairs: 100 Front + 100 Rear).
    
    Guarantees:
    1. Pair Affinity: If bike_client_id is provided and an existing partner image (Front or Rear)
       was already ingested into a batch today, the complementary photo is assigned to that EXACT same batch
       so pairs are NEVER split across batches.
    2. Capacity Rollover: When an active batch reaches max_photos (default 200 photos), the system
       automatically creates a new sequential batch (e.g. '#2', '#3') to keep memory and OCR inference fast.
    3. Standard Naming & Status: Batches preserve standard BATCH-YYYYMMDD-HHMMSS-xxxxxx IDs and MOBILE source type.
    """
    import re
    from core.models import VehicleInstallationPair

    today = timezone.localdate()
    limit = max_photos or getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)

    # 1. Pair Affinity Guard: If this bike already has an image uploaded today, keep both in the SAME batch!
    clean_bike_id = str(bike_client_id).strip()
    if clean_bike_id and not force_new:
        existing_pair = VehicleInstallationPair.objects.filter(
            operator_note__contains=f"bike_id:{clean_bike_id}",
            created_at__date=today,
        ).first()
        if existing_pair:
            linked_img = existing_pair.front_image or existing_pair.rear_image
            if linked_img and linked_img.batch and linked_img.batch.source_type == IngestionBatch.SourceType.MOBILE:
                logger.info(
                    "Pair affinity: Binding bike '%s' to existing batch %s (%d photos)",
                    clean_bike_id, linked_img.batch.batch_id, linked_img.batch.ingested_count
                )
                return linked_img.batch

    # Query all mobile batches created today, ordered by creation time
    qs = IngestionBatch.objects.filter(
        created_at__date=today,
        source_type=IngestionBatch.SourceType.MOBILE,
    ).order_by("created_at")

    # If force_new is not requested, look for the latest batch that still has capacity (< limit)
    if not force_new:
        latest = qs.last()
        if latest and latest.ingested_count < limit:
            return latest

    # If all existing batches today have reached capacity (>= limit), or force_new is True, or none exist:
    num_existing = qs.count()
    batch_index = num_existing + 1

    if source_label:
        clean_label = source_label.strip()
        clean_label = re.sub(r"\s*#\d+$", "", clean_label)
        clean_label = re.sub(r"\s*\(Batch\s*#?\d+\)$", "", clean_label, flags=re.IGNORECASE)
        label = f"{clean_label} #{batch_index}"
    else:
        label = f"Mobile Camera Session {today.strftime('%Y-%m-%d')} #{batch_index}"

    new_batch = create_ingestion_batch(
        source_type=IngestionBatch.SourceType.MOBILE,
        source_label=label,
    )
    logger.info("Created sequential mobile batch: %s (%s) [Limit: %d photos / %d pairs]", new_batch.batch_id, label, limit, limit // 2)
    return new_batch


def link_or_create_conveyor_pair(
    bike_client_id: str,
    image: EvidenceImage,
    orientation: str,
    sequence_num: int = 1,
) -> Tuple["VehicleInstallationPair", bool]:
    """
    Directly associates Front and Rear photos belonging to the same motorcycle on the conveyor.
    Guarantees pairwise symmetry (1 Front + 1 Rear) and preserves the source of truth.
    Returns: (pair, is_now_complete)
    """
    from core.models import VehicleInstallationPair
    from core.services.itms_web_client import get_current_itms_account

    clean_id = str(bike_client_id).strip()
    placeholder_plate = f"BIKE-{clean_id[-8:].upper()}" if len(clean_id) >= 4 else f"CONVEYOR-#{sequence_num:03d}"
    curr_acc = get_current_itms_account()

    # Search for an existing pair created under this bike_client_id or image reference
    pair = None
    if image.orientation == EvidenceImage.Orientation.FRONT:
        pair = VehicleInstallationPair.objects.filter(front_image=image).first()
    elif image.orientation == EvidenceImage.Orientation.REAR:
        pair = VehicleInstallationPair.objects.filter(rear_image=image).first()

    if not pair:
        # Search for pair with this operator note or placeholder
        pair = VehicleInstallationPair.objects.filter(
            operator_note__contains=f"bike_id:{clean_id}"
        ).first()

    if not pair:
        # If no pair exists yet for this bike, create one
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected=image.detected_plate or placeholder_plate,
            verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            matched_via=VehicleInstallationPair.MatchedVia.MOBILE_CONVEYOR,
            account_email=curr_acc,
            operator_note=f"Mobile On-Conveyor (Bike #{sequence_num} | bike_id:{clean_id})",
        )

    # Attach the image to the appropriate orientation slot
    is_front = (orientation.upper() == EvidenceImage.Orientation.FRONT)
    if is_front:
        pair.front_image = image
    else:
        pair.rear_image = image

    # Update canonical detected plate if image has a detected plate
    if image.detected_plate and (pair.registration_number_detected.startswith("BIKE-") or pair.registration_number_detected.startswith("CONVEYOR-")):
        pair.registration_number_detected = image.detected_plate

    pair.refresh_completeness()
    if pair.is_complete:
        pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        pair.operator_note = f"Auto-paired via Mobile On-Conveyor Direct Capture (Bike #{sequence_num} | 1F + 1R Complete | bike_id:{clean_id})"
        image.status = EvidenceImage.Status.MATCHED
        image.save(update_fields=["status"])
        if is_front and pair.rear_image:
            pair.rear_image.status = EvidenceImage.Status.MATCHED
            pair.rear_image.save(update_fields=["status"])
        elif not is_front and pair.front_image:
            pair.front_image.status = EvidenceImage.Status.MATCHED
            pair.front_image.save(update_fields=["status"])
    else:
        missing_side = "REAR" if is_front else "FRONT"
        pair.operator_note = f"Mobile On-Conveyor: Bike #{sequence_num} awaiting {missing_side} photo (bike_id:{clean_id})"
        image.status = EvidenceImage.Status.INCOMPLETE
        image.save(update_fields=["status"])

    pair.save()
    return pair, pair.is_complete
