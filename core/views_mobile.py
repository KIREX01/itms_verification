"""
Mobile Web Companion views and REST APIs for ITMS Verification Copilot.

Provides:
- Mobile Web Companion interface (touch-optimized for phone viewports)
- Low-latency heartbeat / ping endpoint for proximity & connection sensing
- Network discovery API for QR code and local IP address sharing
- Mobile photo ingestion endpoint supporting both On-Conveyor and Off-Conveyor workflows
"""
import json
import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core.models import EvidenceImage, IngestionBatch, VehicleInstallationPair
from core.services import device_service, network_service, vault_service
from core.services.device_session_service import session_manager
from core.services.photo_quality_service import assess_photo_quality
from core.version import __version__

logger = logging.getLogger(__name__)


def _get_client_ip(request: HttpRequest) -> str:
    """Helper to extract real client IP address."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


@require_GET
@login_required(login_url="/login/?next=/mobile/")
def mobile_companion_view(request: HttpRequest) -> HttpResponse:
    """
    Renders the Mobile Web Companion touch UI for smartphones.
    Zero-install PWA/web interface accessed via Cloud VPS, local Wi-Fi, or Hotspot.
    Supports station switching back to supervisor console.
    """
    view_override = request.GET.get("view", "").lower().strip()
    if view_override == "desktop":
        request.session["preferred_view"] = "desktop"
        return redirect("core:dashboard")
    elif view_override == "mobile":
        request.session["preferred_view"] = "mobile"

    batch = vault_service.get_or_create_mobile_batch()
    is_mobile = device_service.is_mobile_device(request)
    device_type = device_service.get_client_device_type(request)

    conn_info = network_service.get_mobile_connection_info(request=request)
    is_cloud = conn_info.get("is_cloud", False)

    from core.services import date_service
    active_shift_d = date_service.get_active_shift_date()
    shift_suffix = date_service.format_date_suffix(active_shift_d)
    shift_date_str = date_service.format_iso_date(active_shift_d)

    return render(request, "core/mobile_companion.html", {
        "version": __version__,
        "active_batch_id": batch.batch_id,
        "active_batch_label": batch.source_label,
        "server_time": timezone.now().isoformat(),
        "active_shift_date": shift_date_str,
        "active_shift_suffix": shift_suffix,
        "max_devices": session_manager.get_max_allowed(),
        "is_mobile_device": is_mobile,
        "device_type": device_type,
        "is_cloud_sync": is_cloud,
        "server_host": request.get_host(),
    })


@require_GET
def api_mobile_ping(request: HttpRequest) -> JsonResponse:
    """
    Ultra-low latency heartbeat endpoint.
    Used by mobile clients to gauge Wi-Fi proximity, round-trip latency,
    and enforce laptop/server connection limits to avoid overload.
    """
    device_id = request.GET.get("device_id", "").strip()
    device_name = request.GET.get("device_name", "").strip()
    mode = request.GET.get("mode", "conveyor").strip()
    client_ip = _get_client_ip(request)

    allowed = True
    session_data = {}
    if device_id:
        allowed, session_data = session_manager.record_heartbeat(
            device_id=device_id,
            device_name=device_name or "Smartphone",
            client_ip=client_ip,
            mode=mode,
        )
        if not allowed:
            return JsonResponse({
                "pong": False,
                "allowed": False,
                "error": "DEVICE_LIMIT_EXCEEDED",
                "message": session_data.get("message", "Device limit reached on this station."),
                "active_count": session_data.get("active_count", 0),
                "max_allowed": session_data.get("max_allowed", 2),
                "active_devices": session_data.get("active_devices", []),
                "server_time": timezone.now().isoformat(),
            }, status=429)

    batch = vault_service.get_or_create_mobile_batch()
    max_photos = getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)

    return JsonResponse({
        "pong": True,
        "allowed": True,
        "device_id": device_id,
        "active_count": session_data.get("active_count", 1),
        "max_allowed": session_data.get("max_allowed", session_manager.get_max_allowed()),
        "batch_id": batch.batch_id,
        "batch_label": batch.source_label,
        "batch_photos": batch.ingested_count,
        "batch_limit": max_photos,
        "batch_remaining": max(0, max_photos - batch.ingested_count),
        "batch_is_full": batch.ingested_count >= max_photos,
        "server_time": timezone.now().isoformat(),
        "status": "online",
    })


@require_POST
def api_mobile_disconnect(request: HttpRequest) -> JsonResponse:
    """Explicitly releases an active smartphone companion session."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    device_id = request.POST.get("device_id") or request.GET.get("device_id", "")
    if device_id:
        session_manager.disconnect_device(device_id.strip())
    return JsonResponse({"success": True})


@require_GET
def api_network_info(request: HttpRequest) -> JsonResponse:
    """
    Returns discovered local network interfaces, candidate URLs,
    and Windows Mobile Hotspot detection status.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    port = request.get_port() or 8000
    try:
        port = int(port)
    except (ValueError, TypeError):
        port = 8000

    if port == 8443:
        port = 443

    # Default to HTTPS unless explicitly disabled via ?ssl=0
    use_https_arg = request.GET.get("https") or request.GET.get("ssl")
    if use_https_arg is not None:
        use_https = use_https_arg.lower() not in ("0", "false", "no")
    else:
        use_https = True

    info = network_service.get_mobile_connection_info(port=port, use_https=use_https, request=request)
    try:
        from core.services import device_access_service
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            pair_token = device_access_service.generate_qr_pairing_token(user=user)
            if pair_token:
                info["pair_token"] = pair_token
                sep = "&" if "?" in info.get("primary_url", "") else "?"
                info["primary_url"] = f"{info['primary_url']}{sep}pair_token={pair_token}"
                info["https_primary_url"] = f"{info['https_primary_url']}{sep}pair_token={pair_token}"
                info["http_primary_url"] = f"{info['http_primary_url']}{sep}pair_token={pair_token}"
                for c in info.get("candidate_urls", []):
                    c_sep = "&" if "?" in c.get("url", "") else "?"
                    c["url"] = f"{c['url']}{c_sep}pair_token={pair_token}"
                    c["https_url"] = f"{c['https_url']}{c_sep}pair_token={pair_token}"
                    c["http_url"] = f"{c['http_url']}{c_sep}pair_token={pair_token}"
    except Exception as exc:
        logger.debug("QR pair_token generation notice: %s", exc)
    return JsonResponse(info)


@require_GET
def api_mobile_status(request: HttpRequest) -> JsonResponse:
    """Returns current active mobile session statistics and connected devices."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    batch = vault_service.get_or_create_mobile_batch()
    front_count = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
    rear_count = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()

    today = timezone.localdate()
    today_pairs = VehicleInstallationPair.objects.filter(
        created_at__date=today,
        matched_via=VehicleInstallationPair.MatchedVia.MOBILE_CONVEYOR,
    ).count()

    sessions = session_manager.get_active_sessions()
    max_photos = getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "batch_label": batch.source_label,
        "total_photos": batch.ingested_count,
        "batch_max_photos": max_photos,
        "batch_max_pairs": max_photos // 2,
        "batch_remaining_photos": max(0, max_photos - batch.ingested_count),
        "batch_is_full": batch.ingested_count >= max_photos,
        "front_photos": front_count,
        "rear_photos": rear_count,
        "conveyor_pairs_today": today_pairs,
        "active_devices": sessions.get("devices", []),
        "active_devices_count": sessions.get("active_count", 0),
        "max_devices_allowed": sessions.get("max_allowed", 2),
        "version": __version__,
    })


@csrf_exempt
@require_POST
def api_mobile_upload(request: HttpRequest) -> JsonResponse:
    """
    Direct ingestion endpoint for photos captured from the Mobile Web Companion.
    
    Supports:
    - Quality and defocus validation (Laplacian variance + exposure).
    - Device load protection limits.
    - On-Conveyor Mode: Direct 1-by-1 bike pairing preserving source of truth.
    - Off-Conveyor Mode: Sequential U-Turn walk batching.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    photo_file = request.FILES.get("photo") or request.FILES.get("file")
    if not photo_file:
        return JsonResponse({"success": False, "error": "No photo file provided."}, status=400)

    mode = request.POST.get("mode", "conveyor").lower().strip()
    orientation = request.POST.get("orientation", "").upper().strip()
    bike_client_id = request.POST.get("bike_client_id", "").strip()
    sequence_num_raw = request.POST.get("sequence_number", "1")
    batch_label = request.POST.get("batch_label", "").strip()
    client_timestamp = request.POST.get("captured_at", "").strip()
    device_id = request.POST.get("device_id", "").strip()
    device_name = request.POST.get("device_name", "").strip()

    # Enforce device limits on upload
    if device_id:
        allowed, session_data = session_manager.record_heartbeat(
            device_id=device_id,
            device_name=device_name or "Smartphone",
            client_ip=_get_client_ip(request),
            mode=mode,
        )
        if not allowed:
            return JsonResponse({
                "success": False,
                "error": "DEVICE_LIMIT_EXCEEDED",
                "message": session_data.get("message", "Device limit reached."),
            }, status=429)

    try:
        sequence_num = int(sequence_num_raw)
    except ValueError:
        sequence_num = 1

    if orientation not in (EvidenceImage.Orientation.FRONT, EvidenceImage.Orientation.REAR):
        orientation = EvidenceImage.Orientation.FRONT if "front" in photo_file.name.lower() else EvidenceImage.Orientation.REAR

    # Read photo bytes to assess photo focus, blur, and exposure
    photo_bytes = photo_file.read()
    photo_file.seek(0)
    quality_report = assess_photo_quality(photo_bytes)

    # Mode-aware batching:
    # Mode A (Off-Conveyor U-Turn): Each walk session gets its own dedicated IngestionBatch (not bounded by conveyor 200 limit).
    # Mode B (On-Conveyor): Sequential rolling 200-photo batch preserving pair affinity.
    uturn_session_id = request.POST.get("uturn_session_id", "").strip()
    uturn_batch_index = request.POST.get("uturn_batch_index", "").strip()

    if mode == "uturn":
        batch = vault_service.get_or_create_uturn_batch(
            uturn_session_id=uturn_session_id,
            batch_index=int(uturn_batch_index) if uturn_batch_index.isdigit() else None,
            source_label=batch_label or f"U-Turn Walk Batch #{uturn_batch_index or '1'}",
        )
    else:
        batch = vault_service.get_or_create_mobile_batch(
            source_label=batch_label,
            bike_client_id=bike_client_id,
        )

    # Ingest the uploaded photo into the Evidence Vault
    image, status = vault_service.ingest_uploaded_file(
        photo_file,
        batch=batch,
        orientation_override=orientation,
    )

    if not image:
        return JsonResponse({
            "success": False,
            "error": f"Failed to ingest photo (status: {status}).",
            "status": status,
            "quality_report": quality_report,
        }, status=400)

    # Update client capture timestamp if provided
    if client_timestamp:
        try:
            parsed_dt = datetime.fromisoformat(client_timestamp.replace("Z", "+00:00"))
            image.captured_at = timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
            image.save(update_fields=["captured_at"])
        except Exception:
            pass

    pair_id = None
    is_paired = False
    pairing_note = ""

    # Mode A: On-Conveyor (Assembly Line) - Immediate Pairwise Association
    if mode == "conveyor" and bike_client_id:
        pair, is_paired = vault_service.link_or_create_conveyor_pair(
            bike_client_id=bike_client_id,
            image=image,
            orientation=orientation,
            sequence_num=sequence_num,
        )
        pair_id = pair.id
        pairing_note = pair.operator_note

        # If photo is blurry or soft, annotate operator note for quality review
        if quality_report.get("is_blurry") and pair:
            warn_msg = f"[Quality Warning: Blurry photo, sharpness={quality_report['sharpness_score']}]"
            pair.operator_note = f"{pair.operator_note} {warn_msg}".strip()
            pair.save(update_fields=["operator_note"])

        # If pair is complete (1 Front + 1 Rear), trigger vision pipeline in background
        if is_paired:
            try:
                from core.services.pipeline_runner import runner as pipeline_runner
                pipeline_runner.start_pipeline("vision", batch_id=batch.batch_id)
            except Exception as pipeline_err:
                logger.debug("Automatic background vision trigger notice: %s", pipeline_err)

    # Mode B: Off-Conveyor (U-Turn Walk) - Ingested into batch and trajectory aligned
    elif mode == "uturn":
        try:
            f_count = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
            r_count = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()
            if f_count > 0 and f_count == r_count:
                from core.matcher import association
                association.run_association(batch_id=batch.batch_id)
        except Exception as assoc_err:
            logger.debug("Auto-association notice on U-turn upload: %s", assoc_err)

    max_photos = getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)

    return JsonResponse({
        "success": True,
        "status": status,
        "image_id": str(image.id),
        "orientation": orientation,
        "mode": mode,
        "bike_client_id": bike_client_id,
        "sequence_number": sequence_num,
        "is_paired": is_paired,
        "pair_id": pair_id,
        "pairing_note": pairing_note,
        "batch_id": batch.batch_id,
        "batch_label": batch.source_label,
        "batch_photos": batch.ingested_count,
        "batch_limit": max_photos,
        "batch_remaining": max(0, max_photos - batch.ingested_count),
        "batch_is_full": batch.ingested_count >= max_photos,
        "vault_file": image.vault_file,
        "quality_report": quality_report,
    })


@require_POST
def api_mobile_new_batch(request: HttpRequest) -> JsonResponse:
    """
    Explicitly seals the active batch and opens a new sequential mobile batch.
    Allows operators to segment inspection runs (e.g. by shift or parking bay).
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    source_label = request.POST.get("source_label", "").strip()
    new_batch = vault_service.get_or_create_mobile_batch(source_label=source_label, force_new=True)
    max_photos = getattr(settings, "MAX_MOBILE_BATCH_PHOTOS", 200)

    return JsonResponse({
        "success": True,
        "batch_id": new_batch.batch_id,
        "batch_label": new_batch.source_label,
        "total_photos": new_batch.ingested_count,
        "batch_max_photos": max_photos,
        "batch_remaining_photos": max_photos,
        "batch_is_full": False,
    })


@require_http_methods(["GET", "POST"])
def api_mobile_uturn_status(request: HttpRequest) -> JsonResponse:
    """
    Returns current sync status and photo counts for a specific U-Turn walk session.
    Allows the mobile client to restore, verify, and reconcile state after browser reload.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    uturn_session_id = request.GET.get("session_id", "").strip() or request.GET.get("uturn_session_id", "").strip()
    if not uturn_session_id:
        return JsonResponse({"success": False, "error": "session_id parameter required.", "code": "PARAM_REQUIRED"}, status=400)

    batch = IngestionBatch.objects.filter(
        source_type=IngestionBatch.SourceType.MOBILE,
        source_label__contains=uturn_session_id,
    ).first()

    if not batch:
        return JsonResponse({
            "success": True,
            "exists": False,
            "uturn_session_id": uturn_session_id,
            "rear_count": 0,
            "front_count": 0,
            "total_photos": 0,
            "pairs_count": 0,
        })

    rear_count = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()
    front_count = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
    total_photos = batch.images.count()
    pairs_count = VehicleInstallationPair.objects.filter(
        Q(front_image__batch=batch) | Q(rear_image__batch=batch),
        is_complete=True,
    ).distinct().count()

    return JsonResponse({
        "success": True,
        "exists": True,
        "uturn_session_id": uturn_session_id,
        "batch_id": batch.batch_id,
        "batch_label": batch.source_label,
        "rear_count": rear_count,
        "front_count": front_count,
        "total_photos": total_photos,
        "pairs_count": pairs_count,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    })


@require_POST
def api_mobile_uturn_abort(request: HttpRequest) -> JsonResponse:
    """
    Aborts / discards an in-progress U-Turn walk session.
    Removes any unsubmitted incomplete pairs and unsubmitted evidence images associated
    with this session so that aborted sessions do not pollute the database.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    uturn_session_id = ""
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            uturn_session_id = body.get("uturn_session_id", "").strip() or body.get("session_id", "").strip()
        except Exception:
            pass
    if not uturn_session_id:
        uturn_session_id = request.POST.get("uturn_session_id", "").strip() or request.POST.get("session_id", "").strip()

    if not uturn_session_id:
        return JsonResponse({"success": False, "error": "uturn_session_id parameter required.", "code": "PARAM_REQUIRED"}, status=400)

    batch = IngestionBatch.objects.filter(
        source_type=IngestionBatch.SourceType.MOBILE,
        source_label__contains=uturn_session_id,
    ).first()

    if not batch:
        return JsonResponse({"success": True, "message": "No active batch found for session; nothing to discard."})

    # Delete any unsubmitted pairs attached to this batch
    deleted_pairs, _ = VehicleInstallationPair.objects.filter(
        Q(front_image__batch=batch) | Q(rear_image__batch=batch)
    ).exclude(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED).delete()

    # Delete unsubmitted evidence images
    deleted_images, _ = EvidenceImage.objects.filter(
        batch=batch
    ).exclude(status=EvidenceImage.Status.SUBMITTED).delete()

    # Relabel batch or remove if completely empty
    remaining_images = batch.images.count()
    if remaining_images == 0:
        batch.delete()
        logger.info("Discarded empty U-Turn walk batch for session %s", uturn_session_id)
    else:
        batch.source_label = f"[ABORTED] {batch.source_label}"
        batch.save(update_fields=["source_label"])

    return JsonResponse({
        "success": True,
        "message": f"U-Turn walk session {uturn_session_id} aborted successfully.",
        "deleted_pairs": deleted_pairs,
        "deleted_images": deleted_images,
    })


@require_POST
def api_mobile_uturn_finish(request: HttpRequest) -> JsonResponse:
    """
    Completes / seals an Off-Conveyor U-Turn batch and triggers final association & vision processing.
    Validates batch completeness: rejects single-orientation submissions (0 fronts) unless explicitly permitted.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    uturn_session_id = ""
    batch_index = None
    allow_incomplete = False

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            uturn_session_id = body.get("uturn_session_id", "").strip()
            batch_index = body.get("batch_index")
            allow_incomplete = bool(body.get("allow_incomplete", False))
        except Exception:
            pass
    if not uturn_session_id:
        uturn_session_id = request.POST.get("uturn_session_id", "").strip()
        allow_incomplete = request.POST.get("allow_incomplete", "").lower() in ("true", "1")

    if not uturn_session_id:
        return JsonResponse({"success": False, "error": "uturn_session_id is required to seal a batch.", "code": "PARAM_REQUIRED"}, status=400)

    batch = IngestionBatch.objects.filter(
        source_type=IngestionBatch.SourceType.MOBILE,
        source_label__contains=uturn_session_id,
    ).first()

    if not batch:
        return JsonResponse({
            "success": False,
            "error": f"No active U-Turn batch found for session '{uturn_session_id}'.",
            "code": "BATCH_NOT_FOUND",
        }, status=404)

    r_count = batch.images.filter(orientation=EvidenceImage.Orientation.REAR).count()
    f_count = batch.images.filter(orientation=EvidenceImage.Orientation.FRONT).count()
    total_count = batch.images.count()

    if total_count == 0:
        return JsonResponse({
            "success": False,
            "error": "Cannot complete an empty batch. No photos have been uploaded for this session.",
            "code": "EMPTY_BATCH",
        }, status=400)

    # Completeness guard: A U-Turn yard walk inherently requires both Front and Rear photos.
    if not allow_incomplete:
        if r_count > 0 and f_count == 0:
            return JsonResponse({
                "success": False,
                "code": "MISSING_FRONT_PHOTOS",
                "error": f"Cannot complete batch: Only {r_count} REAR photo(s) captured. A U-Turn walk requires FRONT photos walking back to pair motorcycles.",
                "rear_count": r_count,
                "front_count": f_count,
            }, status=400)

        if f_count > 0 and r_count == 0:
            return JsonResponse({
                "success": False,
                "code": "MISSING_REAR_PHOTOS",
                "error": f"Cannot complete batch: Only {f_count} FRONT photo(s) captured. A U-Turn walk requires REAR photos to pair motorcycles.",
                "rear_count": r_count,
                "front_count": f_count,
            }, status=400)

    pairs_count = 0
    assoc_summary = None
    try:
        from core.matcher import association
        assoc_summary = association.run_association(batch_id=batch.batch_id)
        pairs_count = VehicleInstallationPair.objects.filter(
            Q(front_image__batch=batch) | Q(rear_image__batch=batch),
            is_complete=True,
        ).distinct().count()
    except Exception as e:
        logger.warning("U-turn finish association notice: %s", e)

    try:
        from core.services.pipeline_runner import runner as pipeline_runner
        pipeline_runner.start_pipeline("vision", batch_id=batch.batch_id)
    except Exception:
        pass

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "batch_label": batch.source_label,
        "rear_count": r_count,
        "front_count": f_count,
        "total_photos": total_count,
        "pairs_count": pairs_count,
        "incomplete_count": getattr(assoc_summary, "incomplete", 0) if assoc_summary else 0,
    })
