"""
Order registry search, live synchronization, and ITMS WebApp explorer APIs.
"""
import json
import logging
import math

from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import (
    auth_service,
    bond_service,
    config_service,
    scoped_order_service,
    submission_worker,
    vault_service,
)
from core.services.itms_web_client import get_web_client
from core.services.pipeline_runner import runner as pipeline_runner
from core.services.security_envelope import require_itms_envelope
from core.vision import normalizer

logger = logging.getLogger(__name__)


@require_GET
@require_itms_envelope
def api_orders_list(request: HttpRequest) -> JsonResponse:
    """Returns list of installation orders for search / manual linking dropdowns."""
    search = request.GET.get("search", "").strip()
    try:
        limit = max(1, min(500, int(request.GET.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50

    envelope = getattr(request, "envelope", None)
    if envelope and envelope.is_valid:
        qs = scoped_order_service.get_scoped_orders(envelope=envelope, search=search).only(
            "id", "order_number", "registration_number", "vin", "warehouse_name", "status", "facility_code"
        )
    else:
        qs = InstallationOrder.objects.none()

    orders = [
        {
            "id": o.id,
            "order_number": o.order_number,
            "registration_number": o.registration_number,
            "vin": o.vin,
            "warehouse_name": o.warehouse_name,
            "status": o.status,
            "facility_code": getattr(o, "facility_code", "AGM"),
        }
        for o in qs[:limit]
    ]

    # If no local orders found and search query is provided, query ITMS live (TUI Parity)
    if not orders and search and len(search) >= 3:
        try:
            client = get_web_client(user=request.user)
            if client.session_store.session.is_cookie_valid():
                live_info = client.fetch_order_info(search, download_photos=False)
                if live_info.get("success"):
                    client.sync_order_info_to_local_db(live_info, order_uuid=live_info.get("order_uuid", ""))
                    o = (
                        InstallationOrder.objects.filter(order_number=live_info.get("order_number"))
                        .only("id", "order_number", "registration_number", "vin", "warehouse_name", "status")
                        .first()
                    )
                    if o:
                        orders.append({
                            "id": o.id,
                            "order_number": o.order_number,
                            "registration_number": o.registration_number,
                            "vin": o.vin,
                            "warehouse_name": o.warehouse_name,
                            "status": o.status,
                        })
        except Exception as exc:
            logger.debug("Live ITMS orders lookup error for '%s': %s", search, exc)

    return JsonResponse({"success": True, "orders": orders})


@require_POST
@require_itms_envelope
def api_sync_orders(request: HttpRequest) -> JsonResponse:
    """Triggers background order sync from ITMS WebApp or seeds mock orders."""
    started = pipeline_runner.start_pipeline("sync_orders")
    if not started:
        return JsonResponse({
            "success": False,
            "message": "Another task is already running in background.",
        }, status=409)

    return JsonResponse({
        "success": True,
        "message": "Order synchronization started in background.",
    })


@require_POST
def api_seed_orders(request: HttpRequest) -> JsonResponse:
    """Seeds demo/synthetic installation orders for developer or testing workflows."""
    from django.core.management import call_command
    try:
        call_command("seed_orders")
        return JsonResponse({
            "success": True,
            "message": "Demo installation orders seeded successfully.",
        })
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
def api_itms_status(request: HttpRequest) -> JsonResponse:
    """Returns current ITMS WebApp connection status, active user identity, and operating bond."""
    force = request.GET.get("force", "").lower() in ("1", "true", "yes")
    status = auth_service.get_itms_status(user=request.user, force_ping=force)
    return JsonResponse({
        "success": True,
        "status": status,
        "active_bond": bond_service.get_active_bond(),
    })


def api_itms_warehouses(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns active operating bond and all discovered warehouse facilities.
    POST: Sets the active operating bond facility (code, name).
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    if request.method == "POST":
        code = ""
        name = ""
        if request.content_type == "application/json" and request.body:
            try:
                body = json.loads(request.body.decode("utf-8"))
                code = str(body.get("code", "")).strip()
                name = str(body.get("name", "")).strip()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.debug("JSON decode error in api_itms_warehouses: %s", exc)
        if not code:
            code = request.POST.get("code", "").strip()
            name = request.POST.get("name", "").strip()

        if not code:
            return JsonResponse({"success": False, "error": "Warehouse/Bond code is required."}, status=400)

        ok = bond_service.set_active_bond(code, name or None)
        if ok and request.user and getattr(request.user, "is_authenticated", False):
            scoped_order_service.switch_operator_facility(user=request.user, new_facility=code)

        return JsonResponse({
            "success": ok,
            "active_bond": bond_service.get_active_bond(),
            "message": f"Active bond set to {code}.",
        })

    return JsonResponse({
        "success": True,
        "active_bond": bond_service.get_active_bond(),
        "warehouses": bond_service.get_all_discovered_warehouses(),
    })


def api_sync_bonds(request: HttpRequest) -> JsonResponse:
    """Dynamically queries ITMS WebApp and extracts available warehouse/bond facilities."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    try:
        from core.services.itms_web_client import get_web_client
        client = get_web_client(user=request.user)
        res = client.fetch_warehouses_from_itms()
        return JsonResponse(res)
    except Exception as exc:
        logger.exception("api_sync_bonds error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_POST
def api_itms_connect(request: HttpRequest) -> JsonResponse:
    """Connects and authenticates against live ITMS WebApp (stock.itms.ug)."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    email = request.POST.get("email")
    password = request.POST.get("password")
    base_url = request.POST.get("base_url")
    bond_code = request.POST.get("bond_code")
    bond_name = request.POST.get("bond_name")

    if not email and request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            email = body.get("email")
            password = body.get("password")
            base_url = body.get("base_url")
            bond_code = body.get("bond_code")
            bond_name = body.get("bond_name")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("JSON decode error in api_itms_connect: %s", exc)

    if not email or not password:
        return JsonResponse({"success": False, "error": "ITMS email and password are required."}, status=400)

    if bond_code:
        bond_service.set_active_bond(str(bond_code).strip(), str(bond_name).strip() if bond_name else None)

    from core.services.itms_web_client import normalize_itms_url
    norm_url = normalize_itms_url(base_url)
    ok, msg = auth_service.connect_itms_account(email, password, norm_url, user=request.user)
    status = auth_service.get_itms_status(user=request.user)
    return JsonResponse({
        "success": ok,
        "message": msg,
        "status": status,
        "active_bond": bond_service.get_active_bond(),
    }, status=200 if ok else 401)


@require_POST
def api_itms_disconnect(request: HttpRequest) -> JsonResponse:
    """Disconnects from ITMS WebApp by clearing local session cookies."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    try:
        from core.services.itms_web_client import get_web_client
        client = get_web_client(user=request.user)
        client.logout()
    except Exception as exc:
        logger.warning("ITMS disconnect error: %s", exc)

    return JsonResponse({
        "success": True,
        "message": "Disconnected from ITMS WebApp.",
        "status": auth_service.get_itms_status(user=request.user),
        "active_bond": bond_service.get_active_bond(),
    })


@require_GET
@require_itms_envelope
def api_itms_orders_explorer(request: HttpRequest) -> JsonResponse:
    """
    ITMS Orders & Archive Explorer API.
    Supports querying Active Orders (/installation-orders/index) and
    Completed Archive (/installation-orders/archive) from live ITMS or local cache.
    """
    tab = request.GET.get("tab", "active").strip().lower()  # 'active' or 'archive'
    is_archive = (tab == "archive")
    source = request.GET.get("source", "auto").strip().lower()  # 'auto', 'live', or 'local'
    search = request.GET.get("search", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = min(100, max(5, int(request.GET.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    client = get_web_client(user=request.user)
    session_valid = client.session_store.session.is_cookie_valid()

    should_try_live = (source == "live") or (source == "auto" and session_valid)

    # 1. Specialized handling for Installation Kits Tab
    if tab == "kits":
        if should_try_live:
            try:
                live_result = client.fetch_installation_kits(
                    page=page,
                    search_params=search if search else None,
                )
                if live_result.get("success"):
                    raw_kits = live_result.get("kits", [])
                    try:
                        client.sync_kits_to_local_db(raw_kits)
                    except Exception as exc:
                        logger.warning("Auto-sync error during kits explorer: %s", exc)

                    canonical_regs = [
                        normalizer.canonicalize(k.get("registration_number", ""))
                        for k in raw_kits
                        if k.get("registration_number")
                    ]
                    canonical_regs = [r for r in canonical_regs if r]
                    pairs_by_reg = {}
                    if canonical_regs:
                        pairs_qs = VehicleInstallationPair.objects.filter(
                            registration_number_detected__in=canonical_regs
                        ).only("id", "registration_number_detected", "verification_status")
                        for p in pairs_qs:
                            if p.registration_number_detected and p.registration_number_detected not in pairs_by_reg:
                                pairs_by_reg[p.registration_number_detected] = p

                    enriched_kits = []
                    for k in raw_kits:
                        reg_num = k.get("registration_number", "")
                        canonical_reg = normalizer.canonicalize(reg_num) if reg_num else ""
                        pair = pairs_by_reg.get(canonical_reg) if canonical_reg else None
                        k_dict = dict(k)
                        k_dict["matched_pair_id"] = pair.id if pair else None
                        k_dict["verification_status"] = pair.verification_status if pair else None
                        enriched_kits.append(k_dict)

                    return JsonResponse({
                        "success": True,
                        "source": "live",
                        "tab": "kits",
                        "page": page,
                        "count": len(enriched_kits),
                        "has_next_page": live_result.get("has_next_page", False),
                        "summary": live_result.get("summary") or f"Live Page {page} ({len(enriched_kits)} kits)",
                        "orders": enriched_kits,
                        "kits": enriched_kits,
                        "itms_connected": True,
                    })
                elif source == "live":
                    return JsonResponse({
                        "success": False,
                        "error": live_result.get("error", "Failed to fetch installation kits from live ITMS."),
                        "source": "live",
                        "orders": [],
                        "kits": [],
                        "itms_connected": session_valid,
                    }, status=502)
            except Exception as exc:
                logger.exception("Error querying live ITMS kits")
                if source == "live":
                    return JsonResponse({
                        "success": False,
                        "error": f"Live ITMS kits connection error: {exc}",
                        "source": "live",
                        "orders": [],
                        "kits": [],
                        "itms_connected": session_valid,
                    }, status=502)

        # Fallback to Local DB query for kits
        qs_kits = InstallationKit.objects.all()
        if search:
            clean_search = normalizer.canonicalize(search) or search.upper()
            qs_kits = qs_kits.filter(
                Q(kit_code__icontains=clean_search) |
                Q(registration_number__icontains=clean_search) |
                Q(front_plate__icontains=search) |
                Q(rear_plate__icontains=search) |
                Q(gps_tracker__icontains=search) |
                Q(warehouse__icontains=search) |
                Q(status__icontains=search)
            )

        total_count = qs_kits.count()
        total_pages = max(1, math.ceil(total_count / limit))
        offset = (page - 1) * limit
        paged_kits = list(qs_kits.order_by("-id")[offset : offset + limit])

        reg_nums = [k.registration_number for k in paged_kits if k.registration_number]
        pairs_by_reg = {}
        if reg_nums:
            pairs_qs = VehicleInstallationPair.objects.filter(
                registration_number_detected__in=reg_nums
            ).only("id", "registration_number_detected", "verification_status")
            for p in pairs_qs:
                if p.registration_number_detected and p.registration_number_detected not in pairs_by_reg:
                    pairs_by_reg[p.registration_number_detected] = p

        local_kits = []
        for k in paged_kits:
            pair = pairs_by_reg.get(k.registration_number) if k.registration_number else None

            local_kits.append({
                "id": k.id,
                "kit_code": k.kit_code,
                "registration_number": k.registration_number,
                "front_plate": k.front_plate,
                "rear_plate": k.rear_plate,
                "front_tracker": k.front_tracker,
                "rear_tracker": k.rear_tracker,
                "gps_tracker": k.gps_tracker,
                "warehouse": k.warehouse,
                "status": k.status,
                "created_date": k.created_date,
                "kit_uuid": k.kit_uuid,
                "detail_url": k.detail_url,
                "matched_pair_id": pair.id if pair else None,
                "verification_status": pair.verification_status if pair else None,
            })

        summary_text = f"Showing {offset + 1} to {min(offset + len(local_kits), total_count)} of {total_count} local kits" if total_count > 0 else "0 kits found"

        return JsonResponse({
            "success": True,
            "source": "local",
            "tab": "kits",
            "page": page,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next_page": page < total_pages,
            "has_prev_page": page > 1,
            "summary": summary_text,
            "orders": local_kits,
            "kits": local_kits,
            "itms_connected": session_valid,
        })

    # 2. Handling for Active and Archive Orders
    if should_try_live:
        try:
            live_result = client.fetch_installation_orders(
                page=page,
                search_params=search if search else None,
                archive=is_archive,
            )
            if live_result.get("success"):
                raw_orders = live_result.get("orders", [])
                # Auto-sync to local database so records are saved/updated
                try:
                    client.sync_orders_to_local_db(raw_orders)
                except Exception as exc:
                    logger.warning("Auto-sync error during orders explorer: %s", exc)

                # Annotate each order with local verification pair if one exists (batch query to avoid N+1)
                order_nums = [o.get("order_number") for o in raw_orders if o.get("order_number")]
                canonical_regs = [
                    normalizer.canonicalize(o.get("registration_number", ""))
                    for o in raw_orders
                    if o.get("registration_number")
                ]
                canonical_regs = [r for r in canonical_regs if r]

                pairs_by_order_num = {}
                pairs_by_reg = {}
                if order_nums or canonical_regs:
                    pairs_filter = Q()
                    if order_nums:
                        pairs_filter |= Q(order__order_number__in=order_nums)
                    if canonical_regs:
                        pairs_filter |= Q(registration_number_detected__in=canonical_regs)
                    pairs_qs = VehicleInstallationPair.objects.filter(pairs_filter).select_related("order").only(
                        "id", "order__order_number", "registration_number_detected", "verification_status"
                    )
                    for p in pairs_qs:
                        if p.order and p.order.order_number and p.order.order_number not in pairs_by_order_num:
                            pairs_by_order_num[p.order.order_number] = p
                        if p.registration_number_detected and p.registration_number_detected not in pairs_by_reg:
                            pairs_by_reg[p.registration_number_detected] = p

                cached_orders_by_num = {
                    loc.order_number: loc
                    for loc in InstallationOrder.objects.filter(order_number__in=order_nums)
                }

                enriched_orders = []
                for o in raw_orders:
                    order_num = o.get("order_number", "")
                    reg_num = o.get("registration_number", "")
                    canonical_reg = normalizer.canonicalize(reg_num) if reg_num else ""
                    pair = None
                    if order_num:
                        pair = pairs_by_order_num.get(order_num)
                    if not pair and canonical_reg:
                        pair = pairs_by_reg.get(canonical_reg)

                    o_dict = dict(o)
                    o_dict["matched_pair_id"] = pair.id if pair else None
                    o_dict["verification_status"] = pair.verification_status if pair else None

                    cached = cached_orders_by_num.get(order_num)
                    if cached:
                        has_cached_photos = bool(cached.front_photo_url or cached.rear_photo_url or cached.photos_json)
                        o_dict["has_photos"] = has_cached_photos
                        if has_cached_photos:
                            o_dict["photos"] = cached.photos_json or (
                                ([{"label": "Front Plate", "url": cached.front_photo_url, "orientation": "FRONT"}] if cached.front_photo_url else []) +
                                ([{"label": "Rear Plate", "url": cached.rear_photo_url, "orientation": "REAR"}] if cached.rear_photo_url else [])
                            )
                            o_dict["hardware"] = {
                                "gps_tracker_id": cached.gps_tracker_id,
                                "front_beacon_id": cached.front_beacon_id,
                                "rear_beacon_id": cached.rear_beacon_id,
                                "front_plate_serial": cached.front_plate_serial,
                                "rear_plate_serial": cached.rear_plate_serial,
                            }
                    enriched_orders.append(o_dict)

                return JsonResponse({
                    "success": True,
                    "source": "live",
                    "tab": tab,
                    "page": page,
                    "count": len(enriched_orders),
                    "has_next_page": live_result.get("has_next_page", False),
                    "summary": live_result.get("summary") or f"Live Page {page} ({len(enriched_orders)} items)",
                    "orders": enriched_orders,
                    "itms_connected": True,
                })
            elif source == "live":
                return JsonResponse({
                    "success": False,
                    "error": live_result.get("error", "Failed to fetch orders from live ITMS."),
                    "source": "live",
                    "orders": [],
                    "itms_connected": session_valid,
                }, status=502)
        except Exception as exc:
            logger.exception("Error querying live ITMS orders")
            if source == "live":
                return JsonResponse({
                    "success": False,
                    "error": f"Live ITMS connection error: {exc}",
                    "source": "live",
                    "orders": [],
                    "itms_connected": session_valid,
                }, status=502)

    # Fallback to Local Database query
    qs = InstallationOrder.objects.filter(is_archived=is_archive)
    target_date = request.GET.get("date") or request.GET.get("date_suffix") or request.GET.get("suffix")
    if target_date and str(target_date).strip().upper() != "ALL":
        from core.services import date_service
        work_d, sfx = date_service.resolve_date_and_suffix(target_date)
        if sfx and sfx != "ALL":
            qs = qs.filter(Q(order_number__endswith=sfx) | Q(installation_date=work_d))
    if search:
        qs = qs.filter(
            Q(order_number__icontains=search) |
            Q(registration_number__icontains=search) |
            Q(vin__icontains=search) |
            Q(warehouse_name__icontains=search) |
            Q(installation_officer__icontains=search)
        )

    total_count = qs.count()
    total_pages = max(1, math.ceil(total_count / limit))
    offset = (page - 1) * limit
    paged_qs = list(qs.order_by("-updated_at", "-created_at")[offset : offset + limit])

    order_ids = [o.id for o in paged_qs]
    reg_nums = [o.registration_number for o in paged_qs if o.registration_number]

    pairs_by_order_id = {}
    pairs_by_reg = {}
    if order_ids or reg_nums:
        pairs_filter = Q()
        if order_ids:
            pairs_filter |= Q(order_id__in=order_ids)
        if reg_nums:
            pairs_filter |= Q(registration_number_detected__in=reg_nums)
        pairs_qs = VehicleInstallationPair.objects.filter(pairs_filter).only(
            "id", "order_id", "registration_number_detected", "verification_status"
        )
        for p in pairs_qs:
            if p.order_id and p.order_id not in pairs_by_order_id:
                pairs_by_order_id[p.order_id] = p
            if p.registration_number_detected and p.registration_number_detected not in pairs_by_reg:
                pairs_by_reg[p.registration_number_detected] = p

    local_orders = []
    for o in paged_qs:
        pair = pairs_by_order_id.get(o.id) or (pairs_by_reg.get(o.registration_number) if o.registration_number else None)

        has_ph = bool(o.front_photo_url or o.rear_photo_url or o.photos_json)
        local_orders.append({
            "id": o.id,
            "order_number": o.order_number,
            "registration_number": o.registration_number,
            "vin": o.vin,
            "sales_order": o.sales_order,
            "service_type": o.service_type,
            "warehouse": o.warehouse_name,
            "officer": o.installation_officer,
            "installation_date": o.installation_date,
            "status": o.order_status or o.status,
            "registration_status": o.registration_status,
            "is_archived": o.is_archived,
            "itms_stage": o.itms_stage,
            "matched_pair_id": pair.id if pair else None,
            "verification_status": pair.verification_status if pair else None,
            "has_photos": has_ph,
            "has_tracker": bool(o.gps_tracker_id),
            "front_photo_url": o.front_photo_url,
            "rear_photo_url": o.rear_photo_url,
            "photos": o.photos_json or (
                ([{"label": "Front Plate", "url": o.front_photo_url, "orientation": "FRONT"}] if o.front_photo_url else []) +
                ([{"label": "Rear Plate", "url": o.rear_photo_url, "orientation": "REAR"}] if o.rear_photo_url else [])
            ),
            "hardware": {
                "gps_tracker_id": o.gps_tracker_id,
                "front_beacon_id": o.front_beacon_id,
                "rear_beacon_id": o.rear_beacon_id,
                "front_plate_serial": o.front_plate_serial,
                "rear_plate_serial": o.rear_plate_serial,
            },
        })

    summary_text = f"Showing {offset + 1} to {min(offset + len(local_orders), total_count)} of {total_count} local records" if total_count > 0 else "0 orders found"

    return JsonResponse({
        "success": True,
        "source": "local",
        "tab": tab,
        "page": page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next_page": page < total_pages,
        "has_prev_page": page > 1,
        "summary": summary_text,
        "orders": local_orders,
        "itms_connected": session_valid,
    })


@require_GET
@require_itms_envelope
def api_itms_order_detail(request: HttpRequest, order_ident: str) -> JsonResponse:
    """
    Returns full metadata, telematics (GPS tracker, front/rear beacons),
    plate serials, and photo URLs for a specific order.
    Optionally fetches live details and downloads photos from ITMS.
    """
    order_ident = order_ident.strip()
    download = request.GET.get("download") in ("1", "true", "yes")
    force_live = request.GET.get("live") in ("1", "true", "yes")

    # Look up in local database first
    order = InstallationOrder.objects.filter(
        Q(order_number__iexact=order_ident) |
        Q(registration_number__iexact=normalizer.canonicalize(order_ident) or order_ident) |
        Q(itms_order_uuid__iexact=order_ident) |
        Q(vin__iexact=order_ident)
    ).first()

    client = get_web_client(user=request.user)
    session_valid = client.session_store.session.is_cookie_valid()

    # If missing hardware/photos, or force_live requested, and session is valid:
    needs_live = force_live or (order and (not order.gps_tracker_id and not order.front_photo_url)) or not order
    if needs_live and session_valid:
        try:
            live_info = client.fetch_order_info(order_ident, download_photos=download)
            if live_info.get("success"):
                order_num = live_info.get("order_number") or (order.order_number if order else order_ident)
                reg_num = live_info.get("registration_number") or (order.registration_number if order else "")
                front_plate = live_info.get("front_plate", {})
                rear_plate = live_info.get("rear_plate", {})
                gps = live_info.get("gps_tracker", {})
                front_beacon = live_info.get("front_beacon", {})
                rear_beacon = live_info.get("rear_beacon", {})

                defaults = {
                    "registration_number": normalizer.canonicalize(reg_num) or reg_num,
                    "vin": live_info.get("vin") or (order.vin if order else ""),
                    "warehouse_name": live_info.get("warehouse") or (order.warehouse_name if order else ""),
                    "installation_officer": live_info.get("installed_by") or (order.installation_officer if order else ""),
                    "front_plate_serial": front_plate.get("serial", ""),
                    "rear_plate_serial": rear_plate.get("serial", ""),
                    "front_plate_type": front_plate.get("type", ""),
                    "rear_plate_type": rear_plate.get("type", ""),
                    "gps_tracker_id": gps.get("device_id", ""),
                    "front_beacon_id": front_beacon.get("device_id", ""),
                    "rear_beacon_id": rear_beacon.get("device_id", ""),
                    "front_photo_url": live_info.get("front_photo_url", ""),
                    "rear_photo_url": live_info.get("rear_photo_url", ""),
                    "photos_json": live_info.get("photos", []),
                    "details_json": live_info.get("raw_details", {}),
                    "itms_order_uuid": live_info.get("order_uuid", ""),
                    "is_archived": order.is_archived if order else False,
                    "info_fetched_at": timezone.now(),
                }
                order, _ = InstallationOrder.objects.update_or_create(
                    order_number=order_num,
                    defaults=defaults,
                )
        except Exception as exc:
            logger.warning("Error fetching live order info for %s: %s", order_ident, exc)

    if not order:
        kit = InstallationKit.objects.filter(
            Q(kit_code__iexact=order_ident) |
            Q(registration_number__iexact=normalizer.canonicalize(order_ident) or order_ident) |
            Q(kit_uuid__iexact=order_ident)
        ).first()
        if kit:
            if force_live and session_valid and kit.kit_uuid:
                try:
                    client.fetch_installation_kit_detail(kit.kit_uuid)
                    kit.refresh_from_db()
                except Exception as exc:
                    logger.debug("Error fetching kit details for %s: %s", kit.kit_uuid, exc)

            return JsonResponse({
                "success": True,
                "is_kit": True,
                "order": {
                    "id": kit.id,
                    "order_number": kit.kit_code,
                    "registration_number": kit.registration_number,
                    "vin": "---",
                    "sales_order": "---",
                    "service_type": "Installation Kit",
                    "warehouse_name": kit.warehouse,
                    "installation_officer": kit.created_by_user or "ITMS Central Stock",
                    "installation_date": kit.created_date,
                    "order_status": kit.status,
                    "registration_status": "Stock Kit",
                    "is_archived": False,
                    "itms_stage": "KIT_STOCK",
                    "itms_order_uuid": kit.kit_uuid,
                    "hardware": {
                        "gps_tracker_id": kit.gps_tracker or "Not Fitted",
                        "front_beacon_id": kit.front_tracker or "None",
                        "rear_beacon_id": kit.rear_tracker or "None",
                        "front_plate_serial": kit.front_plate or "---",
                        "front_plate_type": kit.front_plate_article or "---",
                        "rear_plate_serial": kit.rear_plate or "---",
                        "rear_plate_type": kit.rear_plate_article or "---",
                    },
                    "photos": [],
                    "front_photo_url": None,
                    "rear_photo_url": None,
                    "details_json": kit.details_json,
                    "matched_pair": None,
                    "detail_url": kit.detail_url or (f"https://stock.itms.ug/installation-kit/{kit.kit_uuid}/main/information" if kit.kit_uuid else ""),
                }
            })

        return JsonResponse({"success": False, "error": f"Record '{order_ident}' not found locally or on live ITMS."}, status=404)

    pair = VehicleInstallationPair.objects.filter(
        Q(order=order) | Q(registration_number_detected=order.registration_number)
    ).first()

    pair_data = None
    if pair:
        pair_data = {
            "id": pair.id,
            "verification_status": pair.verification_status,
            "match_score": pair.match_score,
            "match_type": pair.match_type,
            "has_front_photo": bool(pair.front_image),
            "has_rear_photo": bool(pair.rear_image),
        }

    return JsonResponse({
        "success": True,
        "order": {
            "id": order.id,
            "order_number": order.order_number,
            "registration_number": order.registration_number,
            "vin": order.vin,
            "sales_order": order.sales_order,
            "service_type": order.service_type,
            "warehouse_name": order.warehouse_name,
            "installation_officer": order.installation_officer,
            "installation_date": order.installation_date,
            "order_status": order.order_status or order.status,
            "registration_status": order.registration_status,
            "is_archived": order.is_archived,
            "itms_stage": order.itms_stage,
            "itms_order_uuid": order.itms_order_uuid,
            "hardware": {
                "gps_tracker_id": order.gps_tracker_id,
                "front_beacon_id": order.front_beacon_id,
                "rear_beacon_id": order.rear_beacon_id,
                "front_plate_serial": order.front_plate_serial,
                "front_plate_type": order.front_plate_type,
                "rear_plate_serial": order.rear_plate_serial,
                "rear_plate_type": order.rear_plate_type,
            },
            "photos": order.photos_json or (
                ([{"label": "Front Plate", "url": order.front_photo_url, "orientation": "FRONT"}] if order.front_photo_url else []) +
                ([{"label": "Rear Plate", "url": order.rear_photo_url, "orientation": "REAR"}] if order.rear_photo_url else [])
            ),
            "front_photo_url": order.front_photo_url,
            "rear_photo_url": order.rear_photo_url,
            "details_json": order.details_json,
            "matched_pair": pair_data,
        }
    })


@require_POST
@require_itms_envelope
def api_itms_sync_now(request: HttpRequest) -> JsonResponse:
    """
    Directly triggers synchronization of active orders, archive orders, or
    installation kits from stock.itms.ug into the local database.
    Supports shift-scoped date targeting (e.g. date=171026 or 17.10.2026).
    """
    tab = request.POST.get("tab", "active").strip().lower()
    target_date = (
        request.POST.get("target_date")
        or request.POST.get("date")
        or request.POST.get("suffix")
        or request.POST.get("target_date_suffix")
    )
    try:
        pages = max(1, min(50, int(request.POST.get("pages", 1))))
    except (ValueError, TypeError):
        pages = 1

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            tab = body.get("tab", tab)
            pages = max(1, min(50, int(body.get("pages", pages))))
            target_date = body.get("target_date") or body.get("date") or body.get("suffix") or body.get("target_date_suffix") or target_date
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("JSON decode error in api_itms_sync_now: %s", exc)

    client = get_web_client(user=request.user)
    if not client.session_store.session.is_cookie_valid():
        return JsonResponse({
            "success": False,
            "error": "ITMS WebApp session is not connected or has expired. Please connect your ITMS account.",
        }, status=401)

    from core.services.order_sync import OrderSyncService, ShiftOrderSyncDaemon
    sync_svc = OrderSyncService(client=client)
    daemon_status = ShiftOrderSyncDaemon.get_instance().get_status()

    if tab in ("kits", "installation_kits"):
        pages_to_sync = max(1, min(50, pages if pages > 1 else int(request.POST.get("pages", 25))))
        res = sync_svc.sync_installation_kits_scoped(max_pages=pages_to_sync, force=True)
        return JsonResponse({
            "success": res.get("success", False),
            "created": res.get("created", 0),
            "updated": res.get("updated", 0),
            "total_fetched": res.get("total_fetched", 0),
            "pages_fetched": res.get("pages_fetched", 0),
            "message": res.get("message", "Installation kits sync complete."),
            "daemon_status": daemon_status,
        })

    if tab in ("shift", "both", "today", "all"):
        shift_pages = max(1, min(50, pages if pages > 1 else 25))
        res = sync_svc.sync_shift_scoped(target_date=target_date, force=True, max_pages=shift_pages)
        act = res.get("active", {})
        arc = res.get("archive", {})
        total_created = act.get("created", 0) + arc.get("created", 0)
        total_updated = act.get("updated", 0) + arc.get("updated", 0)
        total_fetched = act.get("total_active_seen", 0) + arc.get("total_fetched", 0)
        total_installed = arc.get("installed_verified", 0)
        return JsonResponse({
            "success": True,
            "created": total_created,
            "updated": total_updated,
            "installed_verified": total_installed,
            "total_fetched": total_fetched,
            "active_seen": act.get("total_active_seen", 0),
            "archive_seen": arc.get("total_fetched", 0),
            "target_date": res.get("target_date", "today"),
            "message": res.get("message", "Shift sync complete."),
            "daemon_status": daemon_status,
        })

    if tab == "archive":
        arc_pages = max(1, min(50, pages if pages > 1 else 25))
        res = sync_svc.sync_archive_today(target_date=target_date, max_pages=arc_pages, force=True)
        return JsonResponse({
            "success": True,
            "created": res.get("created", 0),
            "updated": res.get("updated", 0),
            "installed_verified": res.get("installed_verified", 0),
            "total_fetched": res.get("total_fetched", 0),
            "pages_fetched": res.get("pages_fetched", 0),
            "target_date": res.get("target_date", "today"),
            "message": res.get("message", "Archive sync complete."),
            "daemon_status": daemon_status,
        })

    # Default: active orders
    res = sync_svc.sync_active_orders(force=True, max_pages=pages if pages > 1 else 25)
    return JsonResponse({
        "success": True,
        "created": res.get("created", 0),
        "updated": res.get("updated", 0),
        "total_fetched": res.get("total_active_seen", 0),
        "disappeared": res.get("disappeared_from_active", 0),
        "daemon_status": daemon_status,
        "message": res.get("message", "Active orders sync complete."),
    })


@require_GET
@require_itms_envelope
def api_itms_plate_lifecycle(request: HttpRequest) -> JsonResponse:
    """
    Detects and returns the ITMS plate lifecycle state for a given license plate
    or kit code (UNALLOCATED_KIT, ALLOCATED_ORDER, INSTALLED_ARCHIVE, UNREGISTERED).
    """
    from core.services.plate_lifecycle_service import (
        format_lifecycle_card,
        resolve_plate_lifecycle,
    )
    plate_query = request.GET.get("plate") or request.GET.get("query") or request.GET.get("search") or ""
    query_live = request.GET.get("live", "true").strip().lower() in ("true", "1", "yes")

    result = resolve_plate_lifecycle(plate_query, query_live_if_missing=query_live)
    return JsonResponse({
        "success": True,
        "result": result.to_dict(),
        "card_markup": format_lifecycle_card(result),
    })


@require_itms_envelope
def api_itms_kits(request: HttpRequest) -> JsonResponse:
    """
    Lists or synchronizes ITMS Installation Kits stock inventory.
    Enforces facility deduplication: reuses cached facility kits when fresh,
    avoiding duplicate scraping from multiple operators at the same bond.
    """
    envelope = getattr(request, "envelope", None)
    client = get_web_client(user=request.user)

    if request.method == "POST" or request.GET.get("sync") == "true":
        if not client.session_store.session.is_cookie_valid():
            return JsonResponse({"success": False, "error": "ITMS session not authenticated."}, status=401)

        force = request.GET.get("force") in ("true", "1", "yes") or request.POST.get("force") in ("true", "1", "yes")
        # Facility deduplication check
        if not force and envelope and not scoped_order_service.should_sync_facility_kits(envelope=envelope):
            meta = scoped_order_service.get_facility_sync_status(envelope.facility_code)
            return JsonResponse({
                "success": True,
                "deduplicated": True,
                "message": f"Facility {envelope.facility_code} kits are fresh (synced at {meta.last_kits_synced_at}). Reusing local cache.",
                "total_kits": meta.total_kits,
                "facility_code": envelope.facility_code,
            })

        from core.services.order_sync import OrderSyncService
        sync_svc = OrderSyncService(client=client)
        pages_to_sync = int(request.GET.get("pages", 25) or 25)
        res = sync_svc.sync_installation_kits_scoped(max_pages=pages_to_sync, force=True)

        facility_code = envelope.facility_code if envelope else "AGM"
        total_kits = InstallationKit.objects.filter(facility_code=facility_code).count()
        scoped_order_service.record_facility_kits_synced(
            facility_code=facility_code,
            total_kits=total_kits,
            operator=request.user,
        )
        return JsonResponse({"success": True, "sync": res, "facility_code": facility_code})

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    wh_filter = request.GET.get("warehouse", "").strip()

    if envelope and envelope.is_valid:
        kits_qs = scoped_order_service.get_scoped_kits(envelope=envelope, status=status_filter or None).order_by("-created_date", "-id")
    else:
        kits_qs = InstallationKit.objects.none()

    if q:
        clean_q = normalizer.canonicalize(q) or q.upper()
        kits_qs = kits_qs.filter(
            Q(registration_number__icontains=clean_q) |
            Q(kit_code__icontains=clean_q) |
            Q(front_plate__icontains=q) |
            Q(rear_plate__icontains=q) |
            Q(gps_tracker__icontains=q)
        )
    if wh_filter:
        kits_qs = kits_qs.filter(warehouse__icontains=wh_filter)

    total = kits_qs.count()
    kits_data = [k.to_dict() for k in kits_qs[:100]]

    return JsonResponse({
        "success": True,
        "total": total,
        "facility_code": envelope.facility_code if envelope else "AGM",
        "kits": kits_data,
    })




def _format_evidence_url(img) -> str:
    if not img:
        return ""
    raw = getattr(img, "preview_file", "") or getattr(img, "vault_file", "")
    if hasattr(raw, "url"):
        return raw.url
    if raw:
        base = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
        return f"{base}/{str(raw).lstrip('/').replace(chr(92), '/')}"
    return ""


@csrf_exempt
def api_itms_order_install(request: HttpRequest, order_ident: str) -> JsonResponse:
    """
    Direct Single-Order Installation & Submission API (Two-Way ITMS Flow).

    Supports:
      GET:
        Detects live order stage on ITMS (STAGE_1_INSTALLATION, STAGE_2_EVIDENCE, STAGE_3_CONFIRMATION, STAGE_ARCHIVED),
        returns hardware fitment details and attached local photos/pair if present.
      POST:
        Executes immediate single-order action:
        - action='save_step1': Submits Step 1 ("Save and continue") to advance ITMS to photo upload stage.
        - action='attach_photos': Uploads front and/or rear photos, ingests to vault, runs fast OCR detection.
        - action='submit' (default): Full submission. Ingests photos (if attached), drives ITMS workflow
          (Step 1 fitment -> Step 2 photo upload -> Step 3 finalize), updates pair/order to SUBMITTED,
          and writes audit trail.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    order_ident = (order_ident or "").strip()
    if not order_ident:
        return JsonResponse({"success": False, "error": "Order identifier is required."}, status=400)

    # Resolve order in local database
    order = InstallationOrder.objects.filter(
        Q(order_number__iexact=order_ident) |
        Q(registration_number__iexact=normalizer.canonicalize(order_ident) or order_ident) |
        Q(itms_order_uuid__iexact=order_ident) |
        Q(vin__iexact=order_ident)
    ).first()

    client = get_web_client(user=request.user)

    # If order is not found locally, attempt to discover from ITMS if session is valid
    if not order and client.session_store.session.is_cookie_valid():
        try:
            live_info = client.fetch_order_info(order_ident)
            if live_info.get("success"):
                reg_num = live_info.get("registration_number") or ""
                order_num = live_info.get("order_number") or order_ident
                front_plate = live_info.get("front_plate", {})
                rear_plate = live_info.get("rear_plate", {})
                gps = live_info.get("gps_tracker", {})
                order, _ = InstallationOrder.objects.get_or_create(
                    order_number=order_num,
                    defaults={
                        "registration_number": normalizer.canonicalize(reg_num) or reg_num,
                        "vin": live_info.get("vin") or "",
                        "warehouse_name": live_info.get("warehouse") or "",
                        "itms_order_uuid": live_info.get("order_uuid") or "",
                        "front_plate_serial": front_plate.get("serial", ""),
                        "rear_plate_serial": rear_plate.get("serial", ""),
                        "gps_tracker_id": gps.get("device_id", ""),
                    }
                )
        except Exception as exc:
            logger.warning("Error fetching live order info for '%s': %s", order_ident, exc)

    if not order:
        return JsonResponse({"success": False, "error": f"Order '{order_ident}' was not found in the local registry or ITMS."}, status=404)

    order_uuid = order.itms_order_uuid or order.order_number
    pair = VehicleInstallationPair.objects.filter(order=order).first()

    # --- GET: Stage & Hardware Inspection ---
    if request.method == "GET":
        stage_info = {}
        stage = "UNKNOWN"
        step1_form = {}
        if client.session_store.session.is_cookie_valid():
            try:
                stage_info = client.detect_order_stage(order_uuid)
                stage = stage_info.get("stage", "UNKNOWN")
                if stage == "STAGE_1_INSTALLATION":
                    step1_form = client.fetch_installation_step1(order_uuid)
            except Exception as exc:
                stage_info = {"success": False, "error": str(exc)}

        # Auto-match hardware from local InstallationKit if serials are missing
        if (not order.front_plate_serial or not order.rear_plate_serial or not order.gps_tracker_id) and order.registration_number:
            try:
                from core.models import InstallationKit
                kit = InstallationKit.objects.filter(
                    Q(registration_number__iexact=order.registration_number) |
                    Q(registration_number__iexact=normalizer.canonicalize(order.registration_number) or "") |
                    Q(kit_code__icontains=normalizer.canonicalize(order.registration_number) or "")
                ).first()
                if kit:
                    updated = False
                    if not order.front_plate_serial and kit.front_plate:
                        order.front_plate_serial = kit.front_plate
                        updated = True
                    if not order.rear_plate_serial and kit.rear_plate:
                        order.rear_plate_serial = kit.rear_plate
                        updated = True
                    if not order.gps_tracker_id and (kit.gps_tracker or kit.front_tracker or kit.rear_tracker):
                        order.gps_tracker_id = kit.gps_tracker or kit.front_tracker or kit.rear_tracker
                        updated = True
                    if updated:
                        order.save()
            except Exception as exc:
                logger.warning("Error pre-filling hardware from InstallationKit for '%s': %s", order.order_number, exc)

        # Fallback to step1_form selected text if order serials are still blank
        if step1_form.get("success"):
            front_text = step1_form.get("front_plate", {}).get("selected_text", "")
            rear_text = step1_form.get("rear_plate", {}).get("selected_text", "")
            tracker_text = step1_form.get("tracker", {}).get("selected_text", "")
            updated = False
            if not order.front_plate_serial and front_text:
                order.front_plate_serial = front_text
                updated = True
            if not order.rear_plate_serial and rear_text:
                order.rear_plate_serial = rear_text
                updated = True
            if not order.gps_tracker_id and tracker_text:
                order.gps_tracker_id = tracker_text
                updated = True
            if updated:
                order.save()

        pair_data = None
        if pair:
            pair_data = {
                "id": pair.id,
                "verification_status": pair.verification_status,
                "is_complete": pair.is_complete,
                "front_image_url": _format_evidence_url(pair.front_image),
                "rear_image_url": _format_evidence_url(pair.rear_image),
                "plate_detected": pair.registration_number_detected,
            }

        return JsonResponse({
            "success": True,
            "order": {
                "order_number": order.order_number,
                "registration_number": order.registration_number,
                "status": order.status,
                "vin": order.vin,
                "warehouse_name": order.warehouse_name,
                "itms_uuid": order.itms_order_uuid,
                "front_plate_serial": order.front_plate_serial,
                "rear_plate_serial": order.rear_plate_serial,
                "gps_tracker_id": order.gps_tracker_id,
            },
            "stage": stage,
            "stage_info": stage_info,
            "step1_form": {
                "front_plate": step1_form.get("front_plate", {}),
                "rear_plate": step1_form.get("rear_plate", {}),
                "tracker": step1_form.get("tracker", {}),
                "csrf_token": step1_form.get("csrf_token", ""),
            } if step1_form.get("success") else None,
            "existing_pair": pair_data,
        })

    # --- POST: Single-Order Action Execution ---
    action = (request.POST.get("action") or "submit").lower().strip()
    dry_run_param = request.POST.get("dry_run")
    if dry_run_param is not None and str(dry_run_param).strip() != "":
        is_dry_run = str(dry_run_param).lower().strip() in ("true", "1", "yes")
    else:
        is_dry_run = config_service.get_setting("submission.dry_run_mode", getattr(settings, "ITMS_WEB_DRY_RUN", True))

    # Action 1: Save and Proceed (Step 1 only)
    if action in ("save_step1", "save_continue"):
        front_plate_id = request.POST.get("front_plate_id", "").strip()
        rear_plate_id = request.POST.get("rear_plate_id", "").strip()
        tracker_id = request.POST.get("tracker_id", "").strip()

        # Step 1: Attempt to fetch fresh form context (CSRF token and option UUIDs) from ITMS
        step1_info = {}
        try:
            step1_info = client.fetch_installation_step1(order_uuid)
        except Exception as exc:
            logger.warning("Could not fetch Step 1 form page for %s: %s", order_uuid, exc)

        csrf_token = step1_info.get("csrf_token") if step1_info.get("success") else None

        # Resolve serials to ITMS dropdown option UUIDs if options are available
        if step1_info.get("success"):
            resolved_front = client.resolve_option_uuid(step1_info.get("front_plate", {}), front_plate_id)
            resolved_rear = client.resolve_option_uuid(step1_info.get("rear_plate", {}), rear_plate_id)
            resolved_tracker = client.resolve_option_uuid(step1_info.get("tracker", {}), tracker_id)
        else:
            resolved_front = front_plate_id
            resolved_rear = rear_plate_id
            resolved_tracker = tracker_id

        step1_res = client.submit_installation_step1(
            order_uuid=order_uuid,
            front_plate_id=resolved_front,
            back_plate_id=resolved_rear,
            tracker_id=resolved_tracker,
            csrf_token=csrf_token,
            just_save=False,
            run_validation_first=True,
            dry_run=is_dry_run,
        )
        if not step1_res.get("success"):
            return JsonResponse({
                "success": False,
                "error": f"Step 1 failed: {step1_res.get('error')}",
                "details": step1_res,
            }, status=400)

        # Update order with saved serials
        if front_plate_id:
            order.front_plate_serial = front_plate_id
        if rear_plate_id:
            order.rear_plate_serial = rear_plate_id
        if tracker_id:
            order.gps_tracker_id = tracker_id
        order.save()

        # Audit log
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=request.user,
            simulated_token=order.order_number,
            action=SubmissionAuditLog.Action.SUBMIT,
            result=SubmissionAuditLog.ResultStatus.SUCCESS if not is_dry_run else SubmissionAuditLog.ResultStatus.INFO,
            message=f"{'[DRY RUN] ' if is_dry_run else ''}Step 1 (Hardware fitment) saved and proceeded successfully for #{order.order_number}.",
        )

        return JsonResponse({
            "success": True,
            "stage": "STAGE_2_EVIDENCE",
            "message": f"{'[DRY RUN] ' if is_dry_run else ''}Step 1 saved successfully. Order is ready for photo upload.",
            "details": step1_res,
        })

    # Ingest any uploaded photos (front_photo / rear_photo)
    front_file = request.FILES.get("front_photo")
    rear_file = request.FILES.get("rear_photo")

    if front_file or rear_file:
        target_batch = IngestionBatch.objects.filter(
            source_type=IngestionBatch.SourceType.WEB,
            source_label=f"Direct Order #{order.order_number}",
        ).first()
        if not target_batch:
            target_batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label=f"Direct Order #{order.order_number}",
                user=request.user,
            )

        new_front = None
        new_rear = None

        if front_file:
            new_front, _st = vault_service.ingest_uploaded_file(
                uploaded_file=front_file,
                batch=target_batch,
                orientation_override="FRONT",
            )
            if new_front:
                vault_service.process_single_image_vision(new_front)

        if rear_file:
            new_rear, _st = vault_service.ingest_uploaded_file(
                uploaded_file=rear_file,
                batch=target_batch,
                orientation_override="REAR",
            )
            if new_rear:
                vault_service.process_single_image_vision(new_rear)

        if not pair:
            pair = VehicleInstallationPair.objects.create(
                order=order,
                registration_number_detected=order.registration_number,
                operator=request.user,
                match_type=VehicleInstallationPair.MatchType.EXACT,
                verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            )
        if new_front:
            pair.front_image = new_front
        if new_rear:
            pair.rear_image = new_rear
        pair.refresh_completeness()
        pair.save()

    # Action 2: Attach Photos Only
    if action == "attach_photos":
        if not pair:
            return JsonResponse({"success": False, "error": "No photos were uploaded or attached."}, status=400)

        return JsonResponse({
            "success": True,
            "message": "Photos attached and processed successfully.",
            "pair_id": pair.id,
            "is_complete": pair.is_complete,
            "front_image_url": _format_evidence_url(pair.front_image),
            "rear_image_url": _format_evidence_url(pair.rear_image),
            "front_plate": pair.front_image.detected_plate if pair.front_image else "",
            "rear_plate": pair.rear_image.detected_plate if pair.rear_image else "",
        })

    # Action 3: Submit / Direct Submit (Default)
    if not pair or not (pair.front_image and pair.rear_image):
        return JsonResponse({
            "success": False,
            "error": "Both front and rear evidence photos are required to submit installation.",
        }, status=400)

    # Update hardware serials on order from inputs if provided
    front_plate_input = request.POST.get("front_plate_id", "").strip()
    rear_plate_input = request.POST.get("rear_plate_id", "").strip()
    tracker_input = request.POST.get("tracker_id", "").strip()
    updated_hardware = False
    if front_plate_input and order.front_plate_serial != front_plate_input:
        order.front_plate_serial = front_plate_input
        updated_hardware = True
    if rear_plate_input and order.rear_plate_serial != rear_plate_input:
        order.rear_plate_serial = rear_plate_input
        updated_hardware = True
    if tracker_input and order.gps_tracker_id != tracker_input:
        order.gps_tracker_id = tracker_input
        updated_hardware = True
    if updated_hardware:
        order.save()

    # Submit using established submission_worker with hardware inputs passed through
    outcome = submission_worker.submit_pair(
        pair,
        backend="web",
        dry_run=is_dry_run,
        operator=request.user,
        front_plate_id=front_plate_input,
        rear_plate_id=rear_plate_input,
        tracker_id=tracker_input,
    )

    if outcome.success:
        return JsonResponse({
            "success": True,
            "message": f"{'[DRY RUN] ' if is_dry_run else ''}Order #{order.order_number} ({order.registration_number}) submitted successfully to ITMS!",
            "is_finalized": True,
            "dry_run": is_dry_run,
            "order_number": order.order_number,
            "registration_number": order.registration_number,
            "pair_id": pair.id,
            "token": outcome.token,
            "front_image_url": _format_evidence_url(pair.front_image),
            "rear_image_url": _format_evidence_url(pair.rear_image),
        })
    else:
        return JsonResponse({
            "success": False,
            "error": f"Submission failed: {outcome.error}",
            "dry_run": is_dry_run,
            "order_number": order.order_number,
            "registration_number": order.registration_number,
            "pair_id": pair.id,
        }, status=400)
