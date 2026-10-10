"""
Evidence photo upload, batch ingestion, and batch management.
"""
import logging
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from core.models import (
    IngestionBatch,
    VehicleInstallationPair,
)
from core.services import vault_service

logger = logging.getLogger(__name__)


@login_required(login_url="/login/")
def upload_photos_view(request: HttpRequest) -> HttpResponse:
    """
    Web Upload Interface for operators to drag-and-drop or select photos.
    Supports separate front and rear photo uploads as well as general batches.
    """
    if request.method == "POST":
        front_files = request.FILES.getlist("front_photos")
        rear_files = request.FILES.getlist("rear_photos")
        general_files = request.FILES.getlist("photos")
        batch_label = request.POST.get("batch_label", "").strip() or "Web Upload"

        total_count = len(front_files) + len(rear_files) + len(general_files)
        if total_count == 0:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return JsonResponse({"success": False, "error": "No image files selected."}, status=400)
            messages.error(request, "No image files were selected for upload.")
            return render(request, "core/upload.html")

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.WEB,
            source_label=batch_label,
            user=request.user,
        )

        results = []

        # Process FRONT photos
        for f in front_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT", update_batch=False)
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "FRONT",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process REAR photos
        for f in rear_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR", update_batch=False)
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "REAR",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process General photos (detect orientation from filename or path)
        for f in general_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, update_batch=False)
            results.append({
                "name": f.name,
                "status": status,
                "orientation": img.orientation if img else "",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Aggregate batch counts in a single update instead of per-image saves
        batch.total_files = len(results)
        batch.ingested_count = sum(1 for r in results if r["status"] == "INGESTED")
        batch.duplicate_count = sum(1 for r in results if r["status"] == "DUPLICATE_SKIPPED")
        batch.failed_count = sum(1 for r in results if r["status"] in ("INVALID_EXT", "READ_ERROR"))
        batch.save(update_fields=["total_files", "ingested_count", "duplicate_count", "failed_count"])

        counts = batch.images.aggregate(
            front=Count("id", filter=Q(orientation="FRONT")),
            rear=Count("id", filter=Q(orientation="REAR")),
        )
        front_count = counts["front"] or 0
        rear_count = counts["rear"] or 0
        is_symmetric = (front_count == rear_count and front_count > 0)
        discrepancy = abs(front_count - rear_count)

        count_warning = ""
        if front_count > 0 and rear_count > 0 and not is_symmetric:
            missing_side = "REAR" if front_count > rear_count else "FRONT"
            count_warning = f"⚠️ Photo count mismatch: {front_count} Front vs {rear_count} Rear ({discrepancy} missing from {missing_side}). Pairs will be incomplete."
            messages.warning(request, count_warning)
        elif front_count > 0 and rear_count > 0 and is_symmetric:
            messages.success(request, f"✓ Symmetric batch validated: {front_count} Front and {rear_count} Rear photos uploaded into {batch.batch_id} (1:1 ratio).")
        elif front_count > 0 and rear_count == 0:
            count_warning = f"⚠️ Incomplete upload: {front_count} Front photos uploaded without any Rear photos."
            messages.warning(request, count_warning)
        elif rear_count > 0 and front_count == 0:
            count_warning = f"⚠️ Incomplete upload: {rear_count} Rear photos uploaded without any Front photos."
            messages.warning(request, count_warning)

        # Automatically execute initial association on newly uploaded batch
        try:
            from core.matcher import association
            association.run_association(batch_id=batch.batch_id)
        except Exception as assoc_err:
            logger.warning("Auto-association warning on upload %s: %s", batch.batch_id, assoc_err)

        # AJAX / JSON response
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({
                "success": True,
                "batch_id": batch.batch_id,
                "batch_label": batch.source_label,
                "total_files": batch.total_files,
                "ingested": batch.ingested_count,
                "ingested_count": batch.ingested_count,
                "duplicates": batch.duplicate_count,
                "duplicate_count": batch.duplicate_count,
                "failed": batch.failed_count,
                "failed_count": batch.failed_count,
                "front_count": front_count,
                "rear_count": rear_count,
                "is_symmetric": is_symmetric,
                "discrepancy": discrepancy,
                "count_warning": count_warning,
                "results": results,
            })

        # Regular HTML response
        context = {
            "batch": batch,
            "front_count": front_count,
            "rear_count": rear_count,
            "is_symmetric": is_symmetric,
            "discrepancy": discrepancy,
            "count_warning": count_warning,
            "results": results,
            "success": True,
        }
        return render(request, "core/upload.html", context)

    recent_batches = IngestionBatch.objects.all().order_by("-created_at")[:10]
    return render(request, "core/upload.html", {"recent_batches": recent_batches})


@csrf_exempt
def api_upload_photos(request: HttpRequest) -> JsonResponse:
    """REST API endpoint for multi-photo upload from curl or third-party tools."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    front_files = request.FILES.getlist("front_photos")
    rear_files = request.FILES.getlist("rear_photos")
    general_files = request.FILES.getlist("photos")

    if not (front_files or rear_files or general_files):
        return JsonResponse({"error": "No files provided under 'front_photos', 'rear_photos', or 'photos'."}, status=400)

    batch_label = request.POST.get("batch_label", "").strip() or "API Upload"
    batch = vault_service.create_ingestion_batch(
        source_type=IngestionBatch.SourceType.API,
        source_label=batch_label,
        user=request.user,
    )

    items = []
    for f in front_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT", update_batch=False)
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "FRONT",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in rear_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR", update_batch=False)
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "REAR",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in general_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, update_batch=False)
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": img.orientation if img else "",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    # Aggregate batch counts in a single update
    batch.total_files = len(items)
    batch.ingested_count = sum(1 for item in items if item["status"] == "INGESTED")
    batch.duplicate_count = sum(1 for item in items if item["status"] == "DUPLICATE_SKIPPED")
    batch.failed_count = sum(1 for item in items if item["status"] in ("INVALID_EXT", "READ_ERROR"))
    batch.save(update_fields=["total_files", "ingested_count", "duplicate_count", "failed_count"])

    counts = batch.images.aggregate(
        front=Count("id", filter=Q(orientation="FRONT")),
        rear=Count("id", filter=Q(orientation="REAR")),
    )
    front_count = counts["front"] or 0
    rear_count = counts["rear"] or 0
    is_symmetric = (front_count == rear_count and front_count > 0)
    discrepancy = abs(front_count - rear_count)

    # Automatic initial association
    try:
        from core.matcher import association
        association.run_association(batch_id=batch.batch_id)
    except Exception as assoc_err:
        logger.warning("Auto-association warning on API upload %s: %s", batch.batch_id, assoc_err)

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "ingested": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "duplicates": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "failed": batch.failed_count,
        "front_count": front_count,
        "rear_count": rear_count,
        "is_symmetric": is_symmetric,
        "discrepancy": discrepancy,
        "items": items,
    }, status=201)


def api_batch_detail(request: HttpRequest, batch_id: str) -> JsonResponse:
    """Returns batch metadata and list of associated images with pair assignments."""
    batch = IngestionBatch.objects.filter(batch_id=batch_id).first()
    if not batch:
        return JsonResponse({"success": False, "error": f"Batch '{batch_id}' not found."}, status=404)
    batch_images = list(batch.images.all())
    image_ids = [img.id for img in batch_images]

    # Find pairs associated with any image in this batch
    pairs = VehicleInstallationPair.objects.filter(
        Q(front_image_id__in=image_ids) | Q(rear_image_id__in=image_ids)
    ).select_related("order")

    pair_map = {}
    for p in pairs:
        if p.front_image_id:
            pair_map[p.front_image_id] = (p, "FRONT")
        if p.rear_image_id:
            pair_map[p.rear_image_id] = (p, "REAR")

    images = []
    for img in batch_images:
        p_info = None
        if img.id in pair_map:
            p, role = pair_map[img.id]
            p_info = {
                "pair_id": p.id,
                "detected_plate": p.registration_number_detected,
                "verification_status": p.verification_status,
                "role": role,
                "order_number": p.order.order_number if p.order else None,
                "order_plate": p.order.registration_number if p.order else None,
            }

        vault_path = str(img.vault_file).replace("\\", "/")
        images.append({
            "id": str(img.id),
            "file_name": Path(img.original_source_path or img.vault_file).name,
            "url": f"/media/{vault_path}",
            "vault_file": vault_path,
            "status": img.status,
            "orientation": img.orientation,
            "detected_plate": img.detected_plate,
            "ocr_confidence": round(img.ocr_confidence * 100, 1) if img.ocr_confidence is not None else None,
            "captured_at": img.captured_at.strftime("%Y-%m-%d %H:%M:%S") if img.captured_at else None,
            "file_size_kb": round(img.file_size_bytes / 1024, 1) if img.file_size_bytes else 0,
            "pair": p_info,
        })

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "created_at": batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else "",
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "images": images,
    })


@require_GET
def api_batches_list(request: HttpRequest) -> JsonResponse:
    """Returns list of photo ingestion batches with shift scope filtering."""
    today = timezone.localdate()
    latest_batch = IngestionBatch.objects.order_by("-created_at").first()
    scope = request.GET.get("scope", "TODAY").upper().strip()

    qs = IngestionBatch.objects.all().order_by("-created_at")
    total_batches = qs.count()
    today_batches_cnt = qs.filter(created_at__date=today).count()
    carryover_batches_cnt = qs.exclude(created_at__date=today).count()

    if scope == "TODAY":
        qs = qs.filter(created_at__date=today)
    elif scope == "ACTIVE_BATCH" and latest_batch:
        qs = qs.filter(id=latest_batch.id)
    elif scope == "CARRYOVER":
        qs = qs.exclude(created_at__date=today)

    batches = [
        {
            "id": str(b.id),
            "batch_id": b.batch_id,
            "source_type": b.source_type,
            "source_label": b.source_label,
            "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "created_date": b.created_at.strftime("%Y-%m-%d"),
            "is_today": bool(b.created_at and b.created_at.date() == today),
            "is_latest": bool(latest_batch and b.id == latest_batch.id),
            "total_files": b.total_files,
            "ingested_count": b.ingested_count,
            "duplicate_count": b.duplicate_count,
            "failed_count": b.failed_count,
        }
        for b in qs[:60]
    ]
    return JsonResponse({
        "success": True,
        "batches": batches,
        "scope": scope,
        "latest_batch_id": latest_batch.batch_id if latest_batch else None,
        "total_batches": total_batches,
        "today_batches": today_batches_cnt,
        "carryover_batches": carryover_batches_cnt,
    })


