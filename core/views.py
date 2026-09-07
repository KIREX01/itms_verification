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
    Supports both traditional browser form submissions and AJAX requests.
    """
    if request.method == "POST":
        files = request.FILES.getlist("photos")
        batch_label = request.POST.get("batch_label", "").strip() or "Web Upload"

        if not files:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.headers.get("Accept") == "application/json":
                return JsonResponse({"success": False, "error": "No image files selected."}, status=400)
            messages.error(request, "No image files were selected for upload.")
            return render(request, "core/upload.html")

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.WEB,
            source_label=batch_label,
        )

        results = []
        for uploaded_file in files:
            img, status = vault_service.ingest_uploaded_file(uploaded_file, batch=batch)
            results.append({
                "name": uploaded_file.name,
                "status": status,
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        batch.refresh_from_db()

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
                "results": results,
            })

        # Regular HTML response
        context = {
            "batch": batch,
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
      - photos: one or more image files
      - batch_label (optional): human readable string
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    files = request.FILES.getlist("photos")
    if not files:
        return JsonResponse({"error": "No files provided under field 'photos'."}, status=400)

    batch_label = request.POST.get("batch_label", "").strip() or "API Upload"
    batch = vault_service.create_ingestion_batch(
        source_type=IngestionBatch.SourceType.API,
        source_label=batch_label,
    )

    items = []
    for f in files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch)
        items.append({
            "filename": f.name,
            "status": status,
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    batch.refresh_from_db()
    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
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
