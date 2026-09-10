"""
Views for Evidence Photo Upload and Ingestion Batch Management.
"""
from pathlib import Path

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from core.models import EvidenceImage, IngestionBatch
from core.services import vault_service


def upload_photos_view(request: HttpRequest) -> HttpResponse:
    """
    Web Upload Interface for operators to drag-and-drop or select photos.
    Supports separate front and rear photo uploads as well as general batches.
    Supports both traditional browser form submissions and AJAX requests.
    """
    if request.method == "POST":
        front_files = request.FILES.getlist("front_photos")
        rear_files = request.FILES.getlist("rear_photos")
        general_files = request.FILES.getlist("photos")
        batch_label = request.POST.get("batch_label", "").strip() or "Web Upload"

        total_count = len(front_files) + len(rear_files) + len(general_files)
        if total_count == 0:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.headers.get("Accept") == "application/json":
                return JsonResponse({"success": False, "error": "No image files selected."}, status=400)
            messages.error(request, "No image files were selected for upload.")
            return render(request, "core/upload.html")

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.WEB,
            source_label=batch_label,
        )

        results = []

        # Process FRONT photos
        for f in front_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT")
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "FRONT",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process REAR photos
        for f in rear_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR")
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "REAR",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process General photos (detect orientation from filename or path)
        for f in general_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch)
            results.append({
                "name": f.name,
                "status": status,
                "orientation": img.orientation if img else "",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        batch.refresh_from_db()
        front_count = batch.images.filter(orientation="FRONT").count()
        rear_count = batch.images.filter(orientation="REAR").count()
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
            count_warning = f"⚠️ Incomplete upload: {front_count} Front photos uploaded without any Rear photos. Pair matching will remain incomplete."
            messages.warning(request, count_warning)
        elif rear_count > 0 and front_count == 0:
            count_warning = f"⚠️ Incomplete upload: {rear_count} Rear photos uploaded without any Front photos. Pair matching will remain incomplete."
            messages.warning(request, count_warning)

        # AJAX / JSON response
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({
                "success": True,
                "batch_id": batch.batch_id,
                "batch_label": batch.source_label,
                "total_files": batch.total_files,
                "ingested": batch.ingested_count,
                "duplicates": batch.duplicate_count,
                "failed": batch.failed_count,
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

    # GET request - show upload form with recent batches
    recent_batches = IngestionBatch.objects.all().order_by("-created_at")[:10]
    return render(request, "core/upload.html", {"recent_batches": recent_batches})


@csrf_exempt
def api_upload_photos(request: HttpRequest) -> JsonResponse:
    """
    REST API endpoint for programmatic multi-photo upload.
    POST /api/upload/
    Form-Data:
      - front_photos: one or more front image files (optional)
      - rear_photos: one or more rear image files (optional)
      - photos: one or more general image files (optional)
      - batch_label (optional): human readable string
    """
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
    )

    items = []
    for f in front_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT")
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "FRONT",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in rear_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR")
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "REAR",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in general_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch)
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": img.orientation if img else "",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    batch.refresh_from_db()
    front_count = batch.images.filter(orientation="FRONT").count()
    rear_count = batch.images.filter(orientation="REAR").count()
    is_symmetric = (front_count == rear_count and front_count > 0)
    discrepancy = abs(front_count - rear_count)

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "front_count": front_count,
        "rear_count": rear_count,
        "is_symmetric": is_symmetric,
        "discrepancy": discrepancy,
        "items": items,
    }, status=201)


def api_batch_detail(request: HttpRequest, batch_id: str) -> JsonResponse:
    """
    GET /api/batches/<batch_id>/
    Returns batch details, statistics, and list of images.
    """
    batch = get_object_or_404(IngestionBatch, batch_id=batch_id)
    images = [
        {
            "id": str(img.id),
            "original_name": img.original_source_path,
            "vault_file": img.vault_file,
            "status": img.status,
            "detected_plate": img.detected_plate,
            "orientation": img.orientation,
            "ocr_confidence": img.ocr_confidence,
            "is_file_pruned": img.is_file_pruned,
        }
        for img in batch.images.all()
    ]

    return JsonResponse({
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "created_at": batch.created_at.isoformat(),
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "images": images,
    })
