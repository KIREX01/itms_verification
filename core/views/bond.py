"""
Stock Monitoring & Daily Plate Reconciliation / Bond Warehouse REST APIs.
"""
import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    StockDelivery,
    SubmissionAuditLog,
)
from core.services import bond_service
from core.services.security_envelope import require_itms_envelope

logger = logging.getLogger(__name__)


@require_GET
@require_itms_envelope
def api_stock_shift_dates(request: HttpRequest) -> JsonResponse:
    """Returns available shift date suffixes and detailed date metadata."""
    from core.services import date_service
    dates = date_service.get_available_shift_dates()
    active_shift = date_service.get_active_shift_info()
    return JsonResponse({
        "success": True,
        "dates": [d["suffix"] for d in dates],
        "detailed_dates": dates,
        "active_shift": active_shift,
    })


@require_GET
@require_itms_envelope(allow_expired=True)
def api_stock_reconciliation(request: HttpRequest) -> JsonResponse:
    """
    Returns live daily stock reconciliation metrics, opening/closing balance,
    inbound deliveries, dispatched plates, and floor unallocated discrepancies.
    """
    from core.services import stock_monitoring_service
    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    try:
        recon = stock_monitoring_service.compute_daily_reconciliation(date_suffix)
        return JsonResponse({"success": True, "reconciliation": recon})
    except Exception as exc:
        logger.error("api_stock_reconciliation error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope(allow_expired=True)
def api_stock_dispatch(request: HttpRequest) -> JsonResponse:
    """
    Records plates scanned when taken out of stock and issued to the
    installation/assembly floor for a shift.
    Accepts JSON body or POST form data with 'plates' as newline/comma separated text or array.
    """
    from core.services import stock_monitoring_service
    plates_raw = None
    target_date = None
    plate_category = "PSV"
    operator_name = "Operator"
    notes = ""

    auto_create_kits = False
    require_stock_verification = None
    check_itms_live = True

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            plates_raw = body.get("plates")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            plate_category = body.get("plate_category", "PSV")
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
            auto_create_kits = bool(body.get("auto_create_kits", False))
            if "require_stock_verification" in body:
                require_stock_verification = bool(body.get("require_stock_verification"))
            if "check_itms_live" in body:
                check_itms_live = bool(body.get("check_itms_live"))
        except Exception:
            pass

    if not plates_raw:
        plates_raw = request.POST.get("plates")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        plate_category = request.POST.get("plate_category", plate_category)
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)
        if "auto_create_kits" in request.POST:
            auto_create_kits = request.POST.get("auto_create_kits") in ("true", "True", "1")
        if "require_stock_verification" in request.POST:
            require_stock_verification = request.POST.get("require_stock_verification") in ("true", "True", "1")
        if "check_itms_live" in request.POST:
            check_itms_live = request.POST.get("check_itms_live") in ("true", "True", "1")

    if not plates_raw:
        return JsonResponse({"success": False, "error": "No plate numbers provided in 'plates'."}, status=400)

    # Ensure live floor scans from UI / Mobile Companion (which pass auto_create_kits=True)
    # always verify missing plates against the live ITMS app before rejecting as NOT_ON_STOCK.
    if auto_create_kits:
        check_itms_live = True

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        res = stock_monitoring_service.record_dispatch_scans(
            plates=plates_raw,
            plate_category=plate_category,
            target_date_suffix=target_date,
            operator_name=operator_name,
            dispatched_by=user,
            notes=notes,
            require_stock_verification=require_stock_verification if require_stock_verification is not None else True,
            check_itms_live=check_itms_live,
            auto_create_kits=auto_create_kits,
        )
        status_code = 200 if res.get("success") else 400
        return JsonResponse(res, status=status_code)
    except Exception as exc:
        logger.error("api_stock_dispatch error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_delivery(request: HttpRequest) -> JsonResponse:
    """
    Records an inbound delivery of license plates into warehouse physical stock.
    Auto-creates local InstallationKit records marked 'New' in stock.
    """
    from core.services import stock_monitoring_service
    delivery_number = ""
    supplier = "Factory / Central Depot"
    plates_raw = None
    plates_count = None
    target_date = None
    plate_category = "PSV"
    paper_note_reference = ""
    operator_name = "Operator"
    notes = ""
    auto_create_kits = True

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            delivery_number = body.get("delivery_number", "")
            supplier = body.get("supplier", "Factory / Central Depot")
            plates_raw = body.get("plates")
            plates_count = body.get("plates_count") or body.get("quantity") or body.get("count")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            plate_category = body.get("plate_category", "PSV")
            paper_note_reference = body.get("paper_note_reference", "")
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
            if "auto_create_kits" in body:
                auto_create_kits = bool(body["auto_create_kits"])
        except Exception:
            pass

    if not plates_raw and not plates_count:
        delivery_number = request.POST.get("delivery_number", delivery_number)
        supplier = request.POST.get("supplier", supplier)
        plates_raw = request.POST.get("plates")
        plates_count = request.POST.get("plates_count") or request.POST.get("quantity") or request.POST.get("count")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        plate_category = request.POST.get("plate_category", plate_category)
        paper_note_reference = request.POST.get("paper_note_reference", paper_note_reference)
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)
        if "auto_create_kits" in request.POST:
            auto_create_kits = request.POST.get("auto_create_kits", "").lower() in ("true", "1", "yes")

    delivery_note_image = (
        request.FILES.get("delivery_note_image")
        or request.FILES.get("image")
        or request.FILES.get("photo")
        or request.POST.get("delivery_note_image")
        or request.POST.get("photo_path")
    )

    if not plates_raw and not plates_count:
        return JsonResponse({"success": False, "error": "No plate numbers or quantity count provided."}, status=400)

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        res = stock_monitoring_service.record_delivery(
            delivery_number=delivery_number,
            plates=plates_raw or (),
            plates_count=int(plates_count) if plates_count else None,
            supplier=supplier,
            plate_category=plate_category,
            target_date_suffix=target_date,
            paper_note_reference=paper_note_reference,
            delivery_note_image=delivery_note_image,
            received_by=user,
            operator_name=operator_name,
            notes=notes,
            auto_create_kits=auto_create_kits,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_delivery error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_return(request: HttpRequest) -> JsonResponse:
    """
    Records plates returned to stock uninstalled (motorcycle no-show, defect, cancellation).
    Adds them back to warehouse stock.
    """
    from core.services import stock_monitoring_service
    plates_raw = None
    target_date = None
    reason = "BIKE_NO_SHOW"
    operator_name = "Operator"
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            plates_raw = body.get("plates")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            reason = body.get("reason", "BIKE_NO_SHOW")
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
        except Exception:
            pass

    if not plates_raw:
        plates_raw = request.POST.get("plates")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        reason = request.POST.get("reason", reason)
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)

    if not plates_raw:
        return JsonResponse({"success": False, "error": "No plate numbers provided in 'plates'."}, status=400)

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        res = stock_monitoring_service.record_return_scans(
            plates=plates_raw,
            target_date_suffix=target_date,
            reason=reason,
            operator_name=operator_name,
            returned_by=user,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_return error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_bond_transfer(request: HttpRequest) -> JsonResponse:
    """
    Records a bond transfer:
    - TRANSFER_IN: kits transferred into our bond from other bonds (+Stock)
    - TRANSFER_OUT: kits transferred from our bond to other bonds (-Stock)
    Supports plate_category='PSV' or 'PMO'.
    """
    from core.services import stock_monitoring_service
    transfer_type = "TRANSFER_IN"
    plate_category = "PSV"
    other_bond_name = "Other Bond"
    plates_count = 0
    plates_raw = None
    target_date = None
    operator_name = "Operator"
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            transfer_type = body.get("transfer_type", "TRANSFER_IN")
            plate_category = body.get("plate_category", "PSV")
            other_bond_name = body.get("other_bond_name", "Other Bond")
            plates_count = int(body.get("plates_count", 0))
            plates_raw = body.get("plates")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
        except Exception:
            pass

    if not plates_raw and not plates_count:
        transfer_type = request.POST.get("transfer_type", transfer_type)
        plate_category = request.POST.get("plate_category", plate_category)
        other_bond_name = request.POST.get("other_bond_name", other_bond_name)
        plates_count = int(request.POST.get("plates_count", 0) or 0)
        plates_raw = request.POST.get("plates")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)

    try:
        res = stock_monitoring_service.record_bond_transfer(
            transfer_type=transfer_type,
            plate_category=plate_category,
            plates_count=plates_count,
            other_bond_name=other_bond_name,
            plates=plates_raw,
            target_date_suffix=target_date,
            operator_name=operator_name,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_bond_transfer error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_scheduled(request: HttpRequest) -> JsonResponse:
    """
    Manually feeds in the target scheduled number of plates to be installed
    under the bond for the day (PSV White and PMO Yellow).
    """
    from core.services import stock_monitoring_service
    sched_target = None
    sched_psv = 0
    sched_pmo = 0
    target_date = None
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            if "scheduled_target" in body or "scheduled_total" in body or "target" in body:
                sched_target = int(body.get("scheduled_target") or body.get("scheduled_total") or body.get("target") or 0)
            sched_psv = int(body.get("scheduled_psv", 0) or 0)
            sched_pmo = int(body.get("scheduled_pmo", 0) or 0)
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            notes = body.get("notes") or ""
        except Exception:
            pass

    if sched_target is None and sched_psv == 0 and sched_pmo == 0:
        if "scheduled_target" in request.POST or "target" in request.POST:
            sched_target = int(request.POST.get("scheduled_target") or request.POST.get("target") or 0)
        sched_psv = int(request.POST.get("scheduled_psv", 0) or 0)
        sched_pmo = int(request.POST.get("scheduled_pmo", 0) or 0)
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        notes = request.POST.get("notes", notes)

    try:
        res = stock_monitoring_service.set_scheduled_target(
            scheduled_target=sched_target,
            scheduled_psv=sched_psv if sched_target is None else None,
            scheduled_pmo=sched_pmo if sched_target is None else None,
            target_date_suffix=target_date,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_scheduled error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_opening(request: HttpRequest) -> JsonResponse:
    """Manually sets or adjusts Opening Balance for PSV (White) and PMO (Yellow)."""
    from core.services import stock_monitoring_service
    opening_psv = None
    opening_pmo = 0
    target_date = None
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            opening_psv = body.get("opening_psv") or body.get("opening_balance_psv") or body.get("opening_stock")
            opening_pmo = body.get("opening_pmo") or body.get("opening_balance_pmo") or 0
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            notes = body.get("notes") or ""
        except Exception:
            pass

    if opening_psv is None:
        opening_psv = request.POST.get("opening_psv") or request.POST.get("opening_balance_psv") or request.POST.get("opening_stock")
        opening_pmo = request.POST.get("opening_pmo") or request.POST.get("opening_balance_pmo") or 0
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        notes = request.POST.get("notes", notes)

    if opening_psv is None:
        return JsonResponse({"success": False, "error": "opening_psv or opening_stock value is required."}, status=400)

    try:
        res = stock_monitoring_service.set_opening_balances(
            opening_psv=int(opening_psv),
            opening_pmo=int(opening_pmo or 0),
            target_date_suffix=target_date,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_opening error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_physical_count(request: HttpRequest) -> JsonResponse:
    """Records an end-of-shift physical stock count audit and computes variance."""
    from core.services import stock_monitoring_service
    physical_val = None
    target_date = None
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            physical_val = body.get("physical_count")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            notes = body.get("notes") or ""
        except Exception:
            pass

    if physical_val is None:
        physical_val = request.POST.get("physical_count")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        notes = request.POST.get("notes", notes)

    if physical_val is None:
        return JsonResponse({"success": False, "error": "physical_count value is required."}, status=400)

    try:
        res = stock_monitoring_service.set_physical_count(
            physical_count=int(physical_val),
            target_date_suffix=target_date,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_physical_count error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
def api_stock_export_csv(request: HttpRequest) -> HttpResponse:
    """Exports shift stock reconciliation and dispatched plates as a downloadable CSV."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Authentication required", status=401)

    from pathlib import Path
    from django.conf import settings
    from core.services import stock_monitoring_service

    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    csv_content = stock_monitoring_service.export_stock_reconciliation_csv(date_suffix)
    recon = stock_monitoring_service.compute_daily_reconciliation(date_suffix)
    suf = recon.get("work_date_suffix", "shift")

    exports_dir = Path(settings.BASE_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"itms_stock_reconciliation_{suf}.csv"
    (exports_dir / filename).write_bytes(csv_content.encode("utf-8-sig"))

    response = HttpResponse(csv_content.encode("utf-8-sig"), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_GET
@require_itms_envelope
def api_stock_delivery_notes(request: HttpRequest) -> JsonResponse:
    """Returns stored delivery notes for a specific date or suffix."""
    from core.services import stock_monitoring_service
    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    try:
        notes = stock_monitoring_service.get_delivery_notes_for_date(date_suffix)
        return JsonResponse({"success": True, "delivery_notes": notes, "count": len(notes)})
    except Exception as exc:
        logger.error("api_stock_delivery_notes error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
@require_itms_envelope
def api_stock_delivery_detail(request: HttpRequest, delivery_id: int) -> JsonResponse:
    """Returns detailed information and plates list for a specific delivery note."""
    from core.services import stock_monitoring_service
    detail = stock_monitoring_service.get_delivery_note_detail(delivery_id)
    if not detail:
        return JsonResponse({"success": False, "error": f"Delivery note #{delivery_id} not found."}, status=404)
    return JsonResponse({"success": True, "delivery_note": detail})


@require_POST
@require_itms_envelope
def api_stock_upload_delivery_note(request: HttpRequest) -> JsonResponse:
    """
    Uploads a photo of the paper delivery note and attaches it to an existing
    or upcoming delivery for the shift.
    """
    from core.services import vault_service
    photo = request.FILES.get("delivery_note_image") or request.FILES.get("photo") or request.FILES.get("file")
    if not photo:
        return JsonResponse({"success": False, "error": "No delivery note photo provided."}, status=400)

    delivery_id = request.POST.get("delivery_id")

    try:
        ev_img, status = vault_service.ingest_uploaded_file(photo)
        if not ev_img:
            return JsonResponse({"success": False, "error": f"Failed to ingest delivery note photo: {status}."}, status=400)

        delivery_obj = None
        if delivery_id:
            try:
                delivery_obj = StockDelivery.objects.get(id=int(delivery_id))
                delivery_obj.delivery_note_image = ev_img
                delivery_obj.save(update_fields=["delivery_note_image"])
            except StockDelivery.DoesNotExist:
                pass

        return JsonResponse({
            "success": True,
            "image_id": str(ev_img.id),
            "image_url": ev_img.url,
            "image_path": ev_img.absolute_path,
            "delivery_id": delivery_obj.id if delivery_obj else None,
            "message": "Delivery note photo uploaded successfully.",
        })
    except Exception as exc:
        logger.error("api_stock_upload_delivery_note error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
@require_itms_envelope
def api_stock_mvr_docket(request: HttpRequest) -> JsonResponse:
    """Returns the MVR Allocation Exception Docket (raw plates list and formatted text)."""
    from core.services import stock_monitoring_service
    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    try:
        docket = stock_monitoring_service.get_mvr_unallocated_docket(date_suffix)
        return JsonResponse({"success": True, "docket": docket})
    except Exception as exc:
        logger.error("api_stock_mvr_docket error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_itms_envelope(allow_expired=True)
def api_stock_blocked_plates(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns the unified list of plates not on stock and the ITMS Transfer Manager request docket.
    POST: Records or clears blocked plates from Mobile Companion or Web UI and returns the updated docket.
    """
    from core.services import stock_monitoring_service

    if request.method not in ("GET", "POST"):
        return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)

    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        if request.method == "POST":
            plates_raw = None
            reason = "NOT ON ITMS STOCK"
            operator_name = getattr(user, "username", "") or "Operator"
            action = "add"
            if request.content_type == "application/json" and request.body:
                try:
                    body = json.loads(request.body.decode("utf-8"))
                    plates_raw = body.get("plates") or body.get("blocked_plates")
                    date_suffix = body.get("date") or body.get("date_suffix") or body.get("suffix") or date_suffix
                    reason = body.get("reason") or reason
                    operator_name = body.get("operator_name") or operator_name
                    action = (body.get("action") or "add").strip().lower()
                except Exception:
                    pass
            if not plates_raw:
                plates_raw = request.POST.get("plates") or request.POST.get("blocked_plates")
                date_suffix = (
                    request.POST.get("date")
                    or request.POST.get("date_suffix")
                    or request.POST.get("suffix")
                    or date_suffix
                )
                reason = request.POST.get("reason", reason)
                operator_name = request.POST.get("operator_name", operator_name)
                action = (request.POST.get("action") or action).strip().lower()

            if action == "clear":
                cleared = stock_monitoring_service.clear_blocked_plates_for_date(date_suffix)
                docket = stock_monitoring_service.get_stock_transfer_request_docket(date_suffix)
                return JsonResponse({
                    "success": True,
                    "cleared_count": cleared,
                    "date_suffix": docket.get("work_date_suffix"),
                    "blocked_plates": docket.get("plates", []),
                    "count": docket.get("count", 0),
                    "docket": docket,
                })

            if plates_raw:
                stock_monitoring_service.record_blocked_plates(
                    plates_raw,
                    target_date_suffix=date_suffix,
                    reason=reason,
                    operator_name=operator_name,
                    scanned_by=user,
                )

        docket = stock_monitoring_service.get_stock_transfer_request_docket(date_suffix)
        return JsonResponse({
            "success": True,
            "date_suffix": docket.get("work_date_suffix"),
            "blocked_plates": docket.get("plates", []),
            "count": docket.get("count", 0),
            "docket": docket,
        })
    except Exception as exc:
        logger.exception("api_stock_blocked_plates error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope(allow_expired=True)
def api_stock_verify_batch(request: HttpRequest) -> JsonResponse:
    """
    Verifies a pasted batch of license plates against stock without creating dispatches.
    Returns which plates are valid on stock vs which are NOT on stock, along with
    a pre-formatted ITMS Transfer Manager request docket.
    """
    from core.services import stock_monitoring_service, kit_provisioning_service, bond_service
    plates_raw = None
    date_suffix = None
    check_itms_live = True

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            plates_raw = body.get("plates")
            date_suffix = body.get("date") or body.get("date_suffix") or body.get("suffix")
            if "check_itms_live" in body:
                check_itms_live = bool(body.get("check_itms_live"))
        except Exception:
            pass
    if not plates_raw:
        plates_raw = request.POST.get("plates")
        date_suffix = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or date_suffix
        )
        if "check_itms_live" in request.POST:
            check_itms_live = request.POST.get("check_itms_live") in ("true", "True", "1")

    if not plates_raw:
        return JsonResponse({"success": False, "error": "No plate numbers provided in 'plates'."}, status=400)

    clean_plates = stock_monitoring_service.parse_plate_input(plates_raw)
    if not clean_plates:
        return JsonResponse({"success": False, "error": "No valid license plates found."}, status=400)

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        active_bond = bond_service.get_active_bond()
        verify_res = kit_provisioning_service.verify_scanned_kits_stock(
            clean_plates,
            check_itms_live=check_itms_live,
            facility_name=active_bond.get("name"),
            max_live_itms_queries=500,
            user=user,
        )

        verified_plates = verify_res.get("verified_plates", [])
        rejected_not_on_stock = verify_res.get("rejected_not_on_stock", [])
        already_installed = verify_res.get("already_installed", [])
        blocked = rejected_not_on_stock + already_installed

        if blocked:
            stock_monitoring_service.record_blocked_plates(
                blocked,
                date_suffix,
                scanned_by=user,
                operator_name=getattr(user, "username", "") or "Operator",
            )

        docket = stock_monitoring_service.get_stock_transfer_request_docket(date_suffix, blocked_plates=blocked)

        return JsonResponse({
            "success": True,
            "total_submitted": len(clean_plates),
            "verified_plates": verified_plates,
            "rejected_not_on_stock": rejected_not_on_stock,
            "already_installed": already_installed,
            "blocked_plates": blocked,
            "docket": docket,
        })
    except Exception as exc:
        logger.exception("api_stock_verify_batch error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_shift_reconcile(request: HttpRequest) -> JsonResponse:
    """
    Unified Shift Stock Reconciliation and Inventory System Update:
    Ingests morning counted plates, evening returns, updates InstallationKit records,
    balances DailyStockLedger, and generates standardized CSV exports.
    """
    from core.services import stock_monitoring_service
    morning_plates = None
    return_plates = None
    target_date = None
    sync_itms = False
    operator_name = "Operator"
    notes = ""

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            morning_plates = body.get("morning_plates") or body.get("plates") or body.get("dispatched_plates")
            return_plates = body.get("return_plates") or body.get("returns")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            sync_itms = bool(body.get("sync_itms", False))
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
        except Exception:
            pass

    if not morning_plates and not return_plates:
        morning_plates = request.POST.get("morning_plates") or request.POST.get("plates") or request.POST.get("dispatched_plates")
        return_plates = request.POST.get("return_plates") or request.POST.get("returns")
        target_date = (
            request.POST.get("date")
            or request.POST.get("date_suffix")
            or request.POST.get("suffix")
            or target_date
        )
        sync_itms = request.POST.get("sync_itms", "").lower() in ("true", "1", "yes")
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)

    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        op_name = request.user.username if user else operator_name

        if sync_itms:
            try:
                from core.services.itms_web_client import get_web_client
                from core.services.order_sync import OrderSyncService
                sync_service = OrderSyncService(client=get_web_client(user=request.user))
                sync_service.sync_shift_scoped(target_date_suffix=target_date, force=True)
            except Exception as sync_err:
                logger.warning("Auto-sync prior to reconciliation encountered error: %s", sync_err)

        res = stock_monitoring_service.reconcile_and_update_shift(
            morning_plates=morning_plates,
            return_plates=return_plates,
            target_date_suffix=target_date,
            operator_name=op_name,
            notes=notes,
            auto_create_kits=True,
            export_csvs=True,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_shift_reconcile error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
def api_stock_export_category_csv(request: HttpRequest, category: str) -> HttpResponse:
    """
    Downloads standardized shift CSV for a given category:
    - 'unallocated'
    - 'archived'
    - 'pending'
    - 'dispatched'
    - 'returned'
    - 'master'
    - 'movements'
    - 'audit'
    - 'unregistered'
    - 'blocked'
    - 'spiked' / 'undispatched'
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Authentication required", status=401)

    from pathlib import Path
    from django.conf import settings
    from core.services import stock_monitoring_service

    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    _, suffix = stock_monitoring_service.resolve_date_and_suffix(date_suffix)
    cat_clean = str(category).strip().lower()
    csv_content = stock_monitoring_service.generate_category_csv_content(cat_clean, suffix)

    exports_dir = Path(settings.BASE_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"shift_{suffix}_{cat_clean}.csv"
    (exports_dir / filename).write_bytes(csv_content.encode("utf-8-sig"))

    response = HttpResponse(csv_content.encode("utf-8-sig"), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_POST
@require_itms_envelope
def api_stock_kits_sync(request: HttpRequest) -> JsonResponse:
    """
    Synchronizes installation kits from ITMS, inbound deliveries, and safe stock-taking,
    ensuring they are provisioned and marked as 'New' in warehouse stock.
    """
    from core.services import kit_provisioning_service, stock_monitoring_service
    target_date = None
    sync_itms = True
    plates_raw = None

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            if "sync_itms" in body:
                sync_itms = bool(body.get("sync_itms"))
            plates_raw = body.get("plates")
        except Exception:
            pass

    if not target_date:
        target_date = request.POST.get("date") or request.POST.get("date_suffix") or request.POST.get("suffix")
    if "sync_itms" in request.POST:
        sync_itms = request.POST.get("sync_itms", "").lower() in ("true", "1", "yes")
    if not plates_raw:
        plates_raw = request.POST.get("plates")

    source_plates = stock_monitoring_service.parse_plate_input(plates_raw) if plates_raw else None

    try:
        res = kit_provisioning_service.sync_and_provision_warehouse_kits(
            target_date_suffix=target_date,
            source_plates=source_plates,
            sync_itms=sync_itms,
        )
        payload = dict(res)
        payload["result"] = res
        return JsonResponse(payload)
    except Exception as exc:
        logger.error("api_stock_kits_sync error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_itms_envelope
def api_stock_kits_readiness(request: HttpRequest) -> JsonResponse:
    """
    Returns the warehouse stock readiness status for morning take-for-work dispatches.
    If 'plates' are provided (via GET query or POST payload), validates them against
    stock using two-tier verification (local synced kits + ITMS live fallback).
    """
    from core.models import InstallationKit
    from core.services import kit_provisioning_service
    active_bond = bond_service.get_active_bond()
    wh_name = active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    new_kits_qs = InstallationKit.objects.filter(status__iexact="New")
    total_new = new_kits_qs.count()

    series_breakdown: dict[str, int] = {}
    for p in new_kits_qs.values_list("registration_number", flat=True)[:500]:
        c = p.strip().upper()
        s = c[6:] if len(c) >= 7 else "UG"
        series_breakdown[s] = series_breakdown.get(s, 0) + 1

    daemon = kit_provisioning_service.MorningKitSyncDaemon.get_instance()
    daemon_status = daemon.get_status()

    # Check if candidate plates were provided for validation
    plates_raw = None
    if request.method == "POST":
        if request.content_type == "application/json" and request.body:
            try:
                body = json.loads(request.body.decode("utf-8"))
                plates_raw = body.get("plates")
            except Exception:
                pass
        if not plates_raw:
            plates_raw = request.POST.get("plates")
    elif request.method == "GET":
        plates_raw = request.GET.get("plates")

    validation_result = None
    if plates_raw:
        from core.services import stock_monitoring_service
        plates_list = stock_monitoring_service.parse_plate_input(plates_raw)
        if plates_list:
            validation_result = kit_provisioning_service.verify_scanned_kits_stock(
                plates_list,
                check_itms_live=True,
                facility_name=wh_name,
            )

    resp = {
        "success": True,
        "warehouse": wh_name,
        "total_new_in_stock": total_new,
        "series_breakdown": dict(sorted(series_breakdown.items(), key=lambda x: -x[1])),
        "daemon_status": daemon_status,
        "readiness": {
            "new_unallocated": total_new,
            "total_new": total_new,
            "warehouse": wh_name,
            "series_breakdown": dict(sorted(series_breakdown.items(), key=lambda x: -x[1])),
        },
    }
    if validation_result is not None:
        resp["validation"] = validation_result

    return JsonResponse(resp)



@require_GET
@require_itms_envelope(allow_expired=True)
def api_stock_shift_details(request: HttpRequest) -> JsonResponse:
    """Returns detailed lists of dispatches, movements, and audits for the web UI."""
    from django.utils import timezone
    from core.models import StockDispatchScan, StockDelivery, StockBondTransfer, StockReturnScan, SafeAuditScan, InstallationKit
    from core.services import stock_monitoring_service
    date_suffix = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or None
    )
    _, suffix = stock_monitoring_service.resolve_date_and_suffix(date_suffix)

    # 1. Dispatches (up to 2000 rows so large 500+ kit shifts are never truncated at 150)
    dispatches = list(StockDispatchScan.objects.filter(work_date_suffix=suffix).order_by("-dispatched_at")[:2000].values(
        "id", "registration_number", "plate_category", "dispatched_at", "status", "operator_name"
    ))
    if dispatches:
        disp_plates = [d["registration_number"] for d in dispatches if d.get("registration_number")]
        disp_kits_lookup = stock_monitoring_service.build_kit_lookup_map(disp_plates)
        for d in dispatches:
            reg = d.get("registration_number", "")
            matched_k = disp_kits_lookup.get(reg)
            d["gps_tracker"] = (getattr(matched_k, "gps_tracker", "") or getattr(matched_k, "gps_tracker_id", "") or "-") if matched_k else "-" 
    
    # 2. Movements
    movements = []
    dels = StockDelivery.objects.filter(target_date_suffix=suffix).order_by("-created_at")[:50]
    for d in dels:
        items = list(StockDeliveryItem.objects.filter(delivery=d).values_list("registration_number", flat=True)[:5])
        sample = ", ".join(items)
        if d.total_plates_count > 5:
            sample += f" (+{d.total_plates_count - 5} more)"
        movements.append({
            "type": "DELIVERY",
            "time": d.created_at.strftime("%H:%M:%S") if d.created_at else "",
            "category": d.plate_category,
            "partner": d.supplier,
            "ref": d.paper_note_reference or d.delivery_number,
            "count": d.total_plates_count,
            "sample_plates": sample or "—",
            "sort_time": d.created_at
        })
    transfers = StockBondTransfer.objects.filter(target_date_suffix=suffix).order_by("-created_at")[:50]
    for t in transfers:
        movements.append({
            "type": t.transfer_type,
            "time": t.created_at.strftime("%H:%M:%S") if t.created_at else "",
            "category": t.plate_category,
            "partner": t.other_bond_name,
            "ref": t.transfer_number,
            "count": t.plates_count,
            "sample_plates": "—",
            "sort_time": t.created_at
        })
    returns = StockReturnScan.objects.filter(work_date_suffix=suffix).order_by("-returned_at")[:50]
    for r in returns:
        movements.append({
            "type": "RETURN",
            "time": r.returned_at.strftime("%H:%M:%S") if r.returned_at else "",
            "category": r.plate_category,
            "partner": r.reason,
            "ref": r.registration_number,
            "count": 1,
            "sample_plates": r.registration_number,
            "sort_time": r.returned_at
        })
    movements.sort(key=lambda x: x["sort_time"] or timezone.now(), reverse=True)
    for m in movements:
        m.pop("sort_time", None)

    # 3. Audits
    audits_qs = SafeAuditScan.objects.filter(work_date_suffix=suffix).order_by("-scanned_at")[:150]
    audit_plates = [a.registration_number for a in audits_qs]
    kit_map = stock_monitoring_service.build_kit_lookup_map(audit_plates)
    audits = []
    for a in audits_qs:
        p = a.registration_number
        kit = kit_map.get(p)
        gps = (getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or "—") if kit else "—"
        front_ble = (getattr(kit, "front_tracker", "") or getattr(kit, "ble_beacon_front", "") or "—") if kit else "—"
        rear_ble = (getattr(kit, "rear_tracker", "") or getattr(kit, "ble_beacon_rear", "") or "—") if kit else "—"
        stat = "✓ AUDITED" if (kit and (gps != "—" or front_ble != "—")) else "⏳ AWAITING LINK"
        audits.append({
            "plate": p,
            "status": stat,
            "kit_code": kit.kit_code if kit else f"IK-{p}",
            "gps_tracker": gps,
            "front_ble": front_ble,
            "rear_ble": rear_ble,
        })

    # Summary metrics for quick UI footer updates
    total_dispatched = StockDispatchScan.objects.filter(work_date_suffix=suffix).count()
    psv_dispatched = StockDispatchScan.objects.filter(work_date_suffix=suffix, plate_category="PSV").count()
    pmo_dispatched = StockDispatchScan.objects.filter(work_date_suffix=suffix, plate_category="PMO").count()
    blocked_plates = stock_monitoring_service.get_blocked_plates_for_date(suffix)

    return JsonResponse({
        "success": True,
        "date_suffix": suffix,
        "dispatches": dispatches,
        "movements": movements,
        "audits": audits,
        "blocked_plates": blocked_plates,
        "blocked_count": len(blocked_plates),
        "dispatch_summary": {
            "total": total_dispatched,
            "psv": psv_dispatched,
            "pmo": pmo_dispatched,
        },
        "audit_count": len(audits),
    })


@require_GET
@require_itms_envelope(allow_expired=True)
def api_stock_inspect(request: HttpRequest) -> JsonResponse:
    """
    Returns full hardware profile, stock status, ITMS order linkage, and line
    dispatch record for any scanned or clicked plate, mirroring TUI InspectorPane.
    """
    from core.vision import normalizer
    from core.models import InstallationKit, StockDispatchScan, InstallationOrder
    from core.services import stock_monitoring_service, kit_provisioning_service
    from django.db.models import Q

    raw_plate = request.GET.get("plate", "").strip()
    if not raw_plate:
        return JsonResponse({"success": False, "error": "Plate parameter required"}, status=400)

    from core.services.plate_lifecycle_service import format_display_plate
    extracted_p = stock_monitoring_service.extract_single_plate(raw_plate)
    clean_p = extracted_p or normalizer.canonicalize(raw_plate) or raw_plate.upper().replace(" ", "")
    display_p = extracted_p or clean_p
    spaced_p = format_display_plate(clean_p)
    date_suffix = request.GET.get("date_suffix") or request.GET.get("suffix")
    work_d, resolved_suffix = stock_monitoring_service.resolve_date_and_suffix(date_suffix)
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

    target_codes = list(dict.fromkeys([f"IK-{clean_p}", f"IK-{display_p}", f"IK-{spaced_p}"]))
    kit = InstallationKit.objects.filter(
        Q(registration_number__iexact=clean_p) |
        Q(registration_number__iexact=display_p) |
        Q(registration_number__iexact=spaced_p) |
        Q(kit_code__in=target_codes)
    ).first()

    scan_qs = StockDispatchScan.objects.filter(
        Q(registration_number__iexact=clean_p) |
        Q(registration_number__iexact=display_p) |
        Q(registration_number__iexact=spaced_p)
    )
    if date_suffix:
        scan = scan_qs.filter(work_date_suffix=resolved_suffix).order_by("-dispatched_at").first() or scan_qs.order_by("-dispatched_at").first()
    else:
        scan = scan_qs.order_by("-dispatched_at").first()

    order = InstallationOrder.objects.filter(
        Q(registration_number__iexact=clean_p) |
        Q(registration_number__iexact=display_p) |
        Q(registration_number__iexact=spaced_p)
    ).first()

    # Tier 2: If kit is not in local synced InstallationKit records and not already dispatched/installed,
    # look it up live on the ITMS app (/installation-kits) and sync into local DB if it exists.
    synced_from_itms = False
    order_already_installed = bool(
        order and (order.is_archived or (order.order_status or "").strip().lower() == "installed")
    )
    if kit is None and scan is None and not order_already_installed:
        check_live_param = request.GET.get("check_itms_live", "true").strip().lower()
        check_itms_live = check_live_param not in ("0", "false", "no")
        if check_itms_live:
            try:
                verify_res = kit_provisioning_service.verify_scanned_kit_stock(
                    clean_p,
                    check_itms_live=True,
                    user=user,
                )
                kit = verify_res.kit or InstallationKit.objects.filter(
                    Q(registration_number__iexact=clean_p) |
                    Q(registration_number__iexact=display_p) |
                    Q(registration_number__iexact=spaced_p) |
                    Q(kit_code__in=target_codes)
                ).first()
                if kit is not None or verify_res.get("source") == "itms_live":
                    synced_from_itms = True
            except Exception as exc:
                logger.debug("Live ITMS lookup during api_stock_inspect failed for %s: %s", clean_p, exc)

    category = "PSV"
    if scan and getattr(scan, "plate_category", "") == "PMO":
        category = "PMO"
    elif kit and "PMO" in getattr(kit, "kit_code", "").upper():
        category = "PMO"
    elif "PMO" in clean_p:
        category = "PMO"

    # Determine Stock Status
    status_code = "UNVERIFIED"
    status_badge = "ℹ️ UNVERIFIED"
    status_badge_class = "badge-muted"
    status_desc = "Scan barcode or query ITMS to verify stock status."
    is_blocked = False

    if order and (order.is_archived or (order.order_status or "").strip().lower() == "installed"):
        status_code = "ALREADY_INSTALLED"
        status_badge = "🏆 INSTALLED / ARCHIVED"
        status_badge_class = "badge-blue"
        status_desc = f"Order #{order.order_number} installed on {getattr(order, 'installation_date', '') or getattr(kit, 'created_date', '') or 'record'}."
    elif scan:
        status_code = "DISPATCHED"
        status_badge = "📤 DISPATCHED TO LINE"
        status_badge_class = "badge-yellow"
        t_dt = getattr(scan, "dispatched_at", None) or getattr(scan, "created_at", None)
        t_str = t_dt.strftime("%H:%M:%S") if t_dt else "Today"
        op = getattr(scan, "operator_name", "") or "Operator"
        status_desc = f"Dispatched at {t_str} by {op} ({scan.status})."
    elif kit:
        k_stat = (getattr(kit, "status", "") or "New").strip()
        if k_stat.lower() == "new":
            status_code = "ON_STOCK"
            status_badge = "✓ ON STOCK (Safe Room)"
            status_badge_class = "badge-green"
            status_desc = f"Ready in warehouse stock ({kit.warehouse or 'Safe Room'}). Clean to dispatch."
        elif "installed" in k_stat.lower() or "archived" in k_stat.lower():
            status_code = "ALREADY_INSTALLED"
            status_badge = "🏆 INSTALLED / ARCHIVED"
            status_badge_class = "badge-blue"
            status_desc = "Marked installed in ITMS stock records."
        else:
            status_code = k_stat.upper()
            status_badge = f"⏳ {k_stat.upper()}"
            status_badge_class = "badge-yellow"
            status_desc = f"Status in ITMS: {k_stat}"
    else:
        status_code = "NOT_ON_STOCK"
        status_badge = "⛔ NOT ON STOCK"
        status_badge_class = "badge-red"
        status_desc = "Not found in synced kits or live ITMS app. Set physical box aside!"
        is_blocked = True

    if status_code == "ALREADY_INSTALLED":
        is_blocked = True

    # Persist blocked / not-on-stock plate to server immediately so Mobile Companion & Web UI match
    record_blocked_param = request.GET.get("record_blocked", "true").strip().lower()
    should_record_blocked = record_blocked_param not in ("0", "false", "no")
    if is_blocked and should_record_blocked:
        blocked_plates = stock_monitoring_service.record_blocked_plates(
            [clean_p],
            target_date_suffix=resolved_suffix,
            reason=status_code,
            operator_name=getattr(user, "username", "") or "Operator",
            scanned_by=user,
            plate_category=category,
        )
    else:
        blocked_plates = stock_monitoring_service.get_blocked_plates_for_date(resolved_suffix)

    # Symmetric persistence for Mobile Companion dispatch scans:
    # When a kit is ON_STOCK and scanned from Mobile Companion (or with auto_dispatch=1),
    # immediately stage the StockDispatchScan row in the DB so zero scans are ever lost
    # even if the secondary POST /api/stock/dispatch/ is delayed or skipped by client-side queueing.
    referer = (request.META.get("HTTP_REFERER") or "").lower()
    sub_mode = (request.GET.get("sub_mode") or "DISPATCH").strip().upper()
    auto_dispatch_param = (request.GET.get("auto_dispatch") or "").strip().lower()
    should_auto_stage_dispatch = (
        status_code == "ON_STOCK"
        and scan is None
        and not is_blocked
        and (
            auto_dispatch_param in ("1", "true", "yes")
            or ("/mobile" in referer and sub_mode == "DISPATCH")
        )
    )
    if should_auto_stage_dispatch:
        try:
            active_bond = bond_service.get_active_bond()
            b_code = (active_bond.get("code") or "AGM").strip().upper()
            op_name = getattr(user, "username", "") or "Mobile Scanner"
            scan, _ = StockDispatchScan.objects.get_or_create(
                work_date_suffix=resolved_suffix,
                registration_number=clean_p,
                defaults={
                    "plate_category": category,
                    "work_date": work_d,
                    "bond_code": b_code,
                    "dispatched_by": user,
                    "operator_name": op_name,
                    "status": StockDispatchScan.Status.ON_LINE_ACTIVE,
                    "notes": stock_monitoring_service.AUTO_INSPECT_STAGED_NOTE,
                },
            )
            if kit and getattr(kit, "status", "") != "Dispatched":
                kit.status = "Dispatched"
                kit.save(update_fields=["status"])
        except Exception as stage_err:
            logger.debug("Auto-stage dispatch during api_stock_inspect notice for %s: %s", clean_p, stage_err)

    has_telematics = bool(kit and (getattr(kit, "gps_tracker", None) or getattr(kit, "gps_tracker_id", None)))
    if kit is not None and not is_blocked and not has_telematics and not synced_from_itms:
        import sys
        from django.conf import settings
        is_testing = getattr(settings, "TESTING", False) or any("test" in arg for arg in sys.argv)
        if not is_testing:
            def _bg_inspect_enrich():
                from django.db import connection
                connection.close()
                try:
                    stock_monitoring_service.enrich_physical_plates_with_itms([clean_p])
                except Exception:
                    pass
                finally:
                    connection.close()

            import threading
            threading.Thread(target=_bg_inspect_enrich, daemon=True).start()

    kit_profile = {
        "kit_code": getattr(kit, "kit_code", "") or f"IK-{display_p}",
        "warehouse": getattr(kit, "warehouse", "") or "AGM Bonded Warehouse",
        "gps_tracker": getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or "—",
        "front_ble": getattr(kit, "front_tracker", "") or getattr(kit, "ble_beacon_front", "") or "—",
        "rear_ble": getattr(kit, "rear_tracker", "") or getattr(kit, "ble_beacon_rear", "") or "—",
        "front_plate": getattr(kit, "front_plate", "") or "—",
        "rear_plate": getattr(kit, "rear_plate", "") or "—",
        "status": getattr(kit, "status", "New") if kit else "Unregistered",
        "has_telematics": has_telematics,
    }

    order_linkage = {
        "has_order": order is not None,
        "order_number": getattr(order, "order_number", "") or "—",
        "order_status": getattr(order, "order_status", "") or getattr(order, "status", "") or "—",
        "owner_name": getattr(order, "owner_name", "") or "—",
        "motorcycle_model": getattr(order, "motorcycle_model", "") or "—",
        "chassis_number": getattr(order, "chassis_number", "") or "—",
    }

    dispatch_record = None
    if scan:
        t_dt = getattr(scan, "dispatched_at", None) or getattr(scan, "created_at", None)
        dispatch_record = {
            "is_dispatched": True,
            "dispatched_at": t_dt.strftime("%Y-%m-%d %H:%M:%S") if t_dt else "",
            "category": scan.plate_category,
            "suffix": scan.work_date_suffix,
            "status": scan.status,
            "operator": getattr(scan, "operator_name", "") or "Operator",
            "notes": getattr(scan, "notes", "") or "",
        }

    total_dispatched = StockDispatchScan.objects.filter(work_date_suffix=resolved_suffix).count()
    psv_dispatched = StockDispatchScan.objects.filter(work_date_suffix=resolved_suffix, plate_category="PSV").count()
    pmo_dispatched = StockDispatchScan.objects.filter(work_date_suffix=resolved_suffix, plate_category="PMO").count()
    blocked_total = len(blocked_plates)

    if should_auto_stage_dispatch and status_code == "ON_STOCK":
        status_badge = f"✓ ON STOCK → LINE (#{total_dispatched})"
        status_desc = (
            f"Dispatched to Line! Total Dispatched: {total_dispatched} "
            f"({psv_dispatched} PSV, {pmo_dispatched} PMO) • {blocked_total} Set Aside."
        )
    elif status_code == "ON_STOCK":
        status_badge = f"✓ ON STOCK (Safe Room • {total_dispatched} Dispatched)"
        status_desc = (
            f"Ready in warehouse stock ({getattr(kit, 'warehouse', '') or 'Safe Room'}). "
            f"Shift Dispatched: {total_dispatched} • Set Aside: {blocked_total}."
        )
    elif status_code == "DISPATCHED":
        status_badge = f"📤 DISPATCHED TO LINE (#{total_dispatched})"
        status_desc = (
            f"{status_desc} Shift Total: {total_dispatched} Dispatched "
            f"({psv_dispatched} PSV, {pmo_dispatched} PMO) • {blocked_total} Set Aside."
        )
    elif is_blocked:
        status_badge = f"⛔ NOT ON STOCK (SET ASIDE #{blocked_total})"
        status_desc = (
            f"Not on stock — SET PHYSICAL BOX ASIDE! "
            f"Shift Total: {total_dispatched} Dispatched • {blocked_total} Set Aside."
        )

    return JsonResponse({
        "success": True,
        "plate": display_p,
        "canonical_plate": clean_p,
        "category": category,
        "is_blocked": is_blocked,
        "synced_from_itms": synced_from_itms,
        "date_suffix": resolved_suffix,
        "blocked_plates": blocked_plates,
        "blocked_count": len(blocked_plates),
        "dispatch_summary": {
            "total": total_dispatched,
            "psv": psv_dispatched,
            "pmo": pmo_dispatched,
        },
        "stock_status": {
            "code": status_code,
            "badge": status_badge,
            "badge_class": status_badge_class,
            "desc": status_desc,
        },
        "kit_profile": kit_profile,
        "order_linkage": order_linkage,
        "dispatch_record": dispatch_record,
    })


@require_POST
@require_itms_envelope
def api_stock_dispatch_clear(request: HttpRequest) -> JsonResponse:
    """Clears all dispatched plates for the shift, matching TUI _handle_clear_dispatches."""
    from core.models import StockDispatchScan
    from core.services import stock_monitoring_service
    target_date = None
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
        except Exception:
            pass
    if not target_date:
        target_date = request.POST.get("date") or request.POST.get("date_suffix") or request.POST.get("suffix")

    _, suffix = stock_monitoring_service.resolve_date_and_suffix(target_date)
    deleted_count, _ = StockDispatchScan.objects.filter(work_date_suffix=suffix).delete()
    recon = stock_monitoring_service.compute_daily_reconciliation(suffix)
    return JsonResponse({
        "success": True,
        "date_suffix": suffix,
        "cleared_count": deleted_count,
        "reconciliation": recon,
    })


@require_POST
@require_itms_envelope
def api_stock_audit(request: HttpRequest) -> JsonResponse:
    """Performs safe room physical stocktaking audit from scanned or pasted plates."""
    from core.services import stock_monitoring_service
    plates_raw = None
    target_date = None
    operator_name = "Operator"
    notes = ""
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            plates_raw = body.get("plates")
            target_date = body.get("date") or body.get("date_suffix") or body.get("suffix")
            operator_name = body.get("operator_name") or "Operator"
            notes = body.get("notes") or ""
        except Exception:
            pass
    if not plates_raw:
        plates_raw = request.POST.get("plates")
        target_date = request.POST.get("date") or request.POST.get("date_suffix") or request.POST.get("suffix") or target_date
        operator_name = request.POST.get("operator_name", operator_name)
        notes = request.POST.get("notes", notes)

    if not plates_raw:
        return JsonResponse({"success": False, "error": "No plate numbers provided."}, status=400)

    try:
        plates_list = stock_monitoring_service.parse_plate_input(plates_raw)
        res = stock_monitoring_service.record_stock_taking_audit(
            scanned_plates=plates_list,
            target_date_suffix=target_date,
            operator_name=operator_name,
            notes=notes,
        )
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_stock_audit error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
@require_itms_envelope
def api_stock_previous_closing(request: HttpRequest) -> JsonResponse:
    """Returns previous shift closing balances for auto-carry in Opening & Target modal."""
    from core.services import stock_monitoring_service
    target_date = request.GET.get("date") or request.GET.get("date_suffix") or request.GET.get("suffix")
    try:
        data = stock_monitoring_service.get_previous_shift_closing_balances(target_date)
        return JsonResponse({"success": True, "data": data})
    except Exception as exc:
        logger.error("api_stock_previous_closing error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
def api_stock_export_unregistered_csv(request: HttpRequest) -> HttpResponse:
    """Exports safe room audit unregistered kits CSV."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Authentication required", status=401)

    from pathlib import Path
    from django.conf import settings
    from core.services import stock_monitoring_service
    from core.models import SafeAuditScan, InstallationKit
    from django.db.models import Q
    import io, csv

    target_date = request.GET.get("date") or request.GET.get("date_suffix") or request.GET.get("suffix")
    _, suffix = stock_monitoring_service.resolve_date_and_suffix(target_date)

    audits = list(SafeAuditScan.objects.filter(work_date_suffix=suffix).values_list("registration_number", flat=True))
    target_codes = [f"IK-{p}" for p in audits]
    known_kits = set(
        InstallationKit.objects.filter(
            Q(registration_number__in=audits) | Q(kit_code__in=target_codes)
        ).values_list("registration_number", flat=True)
    )
    unregistered = [p for p in audits if p not in known_kits]

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Row", "Plate Number", "Audit Shift Date", "Audit Status", "Action Required"])
    for i, plate in enumerate(unregistered, 1):
        writer.writerow([i, plate, suffix, "UNREGISTERED_IN_SAFE_ROOM", "Manual Registration Required"])

    csv_data = out.getvalue()

    exports_dir = Path(settings.BASE_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"unregistered_stocktake_{suffix}.csv"
    (exports_dir / filename).write_bytes(csv_data.encode("utf-8-sig"))

    response = HttpResponse(csv_data.encode("utf-8-sig"), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_POST
@require_itms_envelope
def api_stock_ledger_entry(request: HttpRequest) -> JsonResponse:
    """
    POST /api/stock/ledger/entry/
    Allows flexible manual entry/overrides of daily stock ledger metrics
    (opening, received, scheduled, installed, bond transfers, physical count, notes).
    """
    data = {}
    if request.content_type == "application/json" and request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception as e:
            return JsonResponse({"success": False, "error": f"Invalid JSON payload: {e}"}, status=400)
    else:
        data = request.POST.dict()

    target_date = data.get("date") or data.get("target_date_suffix") or data.get("date_suffix") or data.get("suffix")
    storage_bond_name = data.get("storage_bond_name") or data.get("warehouse_name")

    def _opt_int(k):
        val = data.get(k)
        if val is None or str(val).strip() == "":
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    opening_balance_pmo = _opt_int("opening_balance_pmo")
    opening_balance_psv = _opt_int("opening_balance_psv")
    kits_received_pmo = _opt_int("kits_received_pmo")
    kits_received_psv = _opt_int("kits_received_psv")
    scheduled_pmo = _opt_int("scheduled_pmo")
    scheduled_psv = _opt_int("scheduled_psv")
    scheduled_total = _opt_int("scheduled_total")
    kits_installed_pmo = _opt_int("kits_installed_pmo")
    kits_installed_psv = _opt_int("kits_installed_psv")
    bond_transfer_in_pmo = _opt_int("bond_transfer_in_pmo")
    bond_transfer_in_psv = _opt_int("bond_transfer_in_psv")
    bond_transfer_out_pmo = _opt_int("bond_transfer_out_pmo")
    bond_transfer_out_psv = _opt_int("bond_transfer_out_psv")
    physical_count = _opt_int("physical_count")
    notes = data.get("notes")
    operator_name = (
        data.get("operator_name")
        or (request.user.username if getattr(request, "user", None) and request.user.is_authenticated else "Operator")
    )

    try:
        from core.services import stock_monitoring_service
        recon = stock_monitoring_service.update_ledger_entry(
            target_date_suffix=target_date,
            storage_bond_name=storage_bond_name,
            opening_balance_pmo=opening_balance_pmo,
            opening_balance_psv=opening_balance_psv,
            kits_received_pmo=kits_received_pmo,
            kits_received_psv=kits_received_psv,
            scheduled_pmo=scheduled_pmo,
            scheduled_psv=scheduled_psv,
            scheduled_total=scheduled_total,
            kits_installed_pmo=kits_installed_pmo,
            kits_installed_psv=kits_installed_psv,
            bond_transfer_in_pmo=bond_transfer_in_pmo,
            bond_transfer_in_psv=bond_transfer_in_psv,
            bond_transfer_out_pmo=bond_transfer_out_pmo,
            bond_transfer_out_psv=bond_transfer_out_psv,
            physical_count=physical_count,
            notes=notes,
            operator_name=operator_name,
        )
        return JsonResponse({
            "success": True,
            "message": "Daily stock ledger updated successfully.",
            "reconciliation": recon,
        })
    except Exception as exc:
        logger.exception("Failed to update daily stock ledger: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
@require_itms_envelope
def api_stock_report_email(request: HttpRequest) -> JsonResponse:
    """
    POST /api/stock/report/email/
    Dispatches the official 9-row daily stock reconciliation report via email.
    Recipients can include operator email, connected ITMS account email, or explicitly provided emails.
    """
    data = {}
    if request.content_type == "application/json" and request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception as e:
            return JsonResponse({"success": False, "error": f"Invalid JSON payload: {e}"}, status=400)
    else:
        data = request.POST.dict()

    target_date = data.get("date") or data.get("target_date_suffix") or data.get("date_suffix") or data.get("suffix")
    notes = data.get("notes") or data.get("remarks") or ""
    operator_name = (
        data.get("operator_name")
        or (request.user.username if getattr(request, "user", None) and request.user.is_authenticated else "Operator")
    )

    # Resolve recipient email addresses
    recipients = []

    # 1. Explicitly provided recipient(s)
    raw_recipients = data.get("recipients") or data.get("recipient_email") or data.get("email")
    if raw_recipients:
        if isinstance(raw_recipients, list):
            recipients.extend(str(r).strip() for r in raw_recipients if str(r).strip())
        elif isinstance(raw_recipients, str):
            for part in raw_recipients.replace(";", ",").split(","):
                part_clean = part.strip()
                if part_clean:
                    recipients.append(part_clean)

    # 2. Operator email (from authenticated user)
    include_op = str(data.get("include_operator_email", "true")).lower() in ("true", "1", "yes")
    if include_op and getattr(request, "user", None) and request.user.is_authenticated:
        u_email = (getattr(request.user, "email", "") or "").strip()
        if u_email and "@" in u_email and u_email not in recipients:
            recipients.append(u_email)

    # 3. ITMS connected account email
    include_itms = str(data.get("include_itms_email", "true")).lower() in ("true", "1", "yes")
    if include_itms:
        try:
            from core.services.itms_web_client import get_current_itms_account
            itms_acc = get_current_itms_account()
            if itms_acc and "@" in itms_acc and itms_acc not in recipients:
                recipients.append(itms_acc)
        except Exception as itms_err:
            logger.warning("Could not resolve ITMS account email: %s", itms_err)

    # Validate and filter
    valid_recipients = [r for r in set(recipients) if "@" in r]
    op_email = (request.user.email if (request.user and request.user.is_authenticated) else "") or ""
    op_user = request.user if (request.user and request.user.is_authenticated) else None

    if not valid_recipients:
        SubmissionAuditLog.objects.create(
            action=SubmissionAuditLog.Action.STOCK_REPORT_EMAILED,
            result=SubmissionAuditLog.ResultStatus.FAILURE,
            operator=op_user,
            operator_username=operator_name,
            operator_email=op_email,
            simulated_token=str(target_date or "CURRENT"),
            message="FAILED: No valid recipient email addresses specified for stock report dispatch.",
        )
        return JsonResponse({
            "success": False,
            "error": "No valid recipient email addresses found. Please specify recipient emails or ensure an ITMS / operator email is configured.",
        }, status=400)

    try:
        from core.services import stock_monitoring_service, email_service
        recon = stock_monitoring_service.compute_daily_reconciliation(target_date)
        work_suffix = str(recon.get("work_date_suffix") or target_date or "")
        try:
            from django.templatetags.static import static
            logo_url = request.build_absolute_uri(static("core/logos/android-chrome-192x192.png"))
        except Exception:
            logo_url = ""

        success, msg, sent_to = email_service.send_stock_reconciliation_report_email(
            recon_data=recon,
            recipient_emails=valid_recipients,
            operator_name=operator_name,
            notes=notes,
            logo_url=logo_url,
        )

        if success:
            SubmissionAuditLog.objects.create(
                action=SubmissionAuditLog.Action.STOCK_REPORT_EMAILED,
                result=SubmissionAuditLog.ResultStatus.SUCCESS,
                operator=op_user,
                operator_username=operator_name,
                operator_email=op_email,
                simulated_token=work_suffix,
                message=f"Stock reconciliation report for {work_suffix} emailed successfully to: {', '.join(sent_to)}",
            )
            return JsonResponse({
                "success": True,
                "message": msg,
                "recipients": sent_to,
                "work_date_suffix": work_suffix,
            })
        else:
            SubmissionAuditLog.objects.create(
                action=SubmissionAuditLog.Action.STOCK_REPORT_EMAILED,
                result=SubmissionAuditLog.ResultStatus.FAILURE,
                operator=op_user,
                operator_username=operator_name,
                operator_email=op_email,
                simulated_token=work_suffix,
                message=f"FAILED emailing stock report for shift {work_suffix} to {', '.join(valid_recipients)}: {msg}",
            )
            return JsonResponse({
                "success": False,
                "error": msg,
                "recipients": valid_recipients,
            }, status=500)
    except Exception as exc:
        logger.exception("Failed to dispatch stock report email: %s", exc)
        SubmissionAuditLog.objects.create(
            action=SubmissionAuditLog.Action.STOCK_REPORT_EMAILED,
            result=SubmissionAuditLog.ResultStatus.FAILURE,
            operator=op_user,
            operator_username=operator_name,
            operator_email=op_email,
            simulated_token=str(target_date or "CURRENT"),
            message=f"ERROR dispatching stock report email for shift {target_date or 'current'}: {exc}",
        )
        return JsonResponse({"success": False, "error": str(exc)}, status=500)

