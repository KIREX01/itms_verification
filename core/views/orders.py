"""
Order registry search, live synchronization, and ITMS WebApp explorer APIs.
"""
import json
import logging
import math

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    InstallationKit,
    InstallationOrder,
    VehicleInstallationPair,
)
from core.services import auth_service, bond_service
from core.services.itms_web_client import get_web_client
from core.services.pipeline_runner import runner as pipeline_runner
from core.vision import normalizer

logger = logging.getLogger(__name__)


@require_GET
def api_orders_list(request: HttpRequest) -> JsonResponse:
    """Returns list of installation orders for search / manual linking dropdowns."""
    search = request.GET.get("search", "").strip()
    try:
        limit = max(1, min(500, int(request.GET.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50

    qs = InstallationOrder.objects.all().only(
        "id", "order_number", "registration_number", "vin", "warehouse_name", "status"
    )
    if search:
        qs = qs.filter(
            Q(order_number__icontains=search) |
            Q(registration_number__icontains=search) |
            Q(vin__icontains=search)
        )

    orders = [
        {
            "id": o.id,
            "order_number": o.order_number,
            "registration_number": o.registration_number,
            "vin": o.vin,
            "warehouse_name": o.warehouse_name,
            "status": o.status,
        }
        for o in qs[:limit]
    ]

    # If no local orders found and search query is provided, query ITMS live (TUI Parity)
    if not orders and search and len(search) >= 3:
        try:
            client = get_web_client()
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


@csrf_exempt
@require_POST
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


@csrf_exempt
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
    status = auth_service.get_itms_status()
    return JsonResponse({
        "success": True,
        "status": status,
        "active_bond": bond_service.get_active_bond(),
    })


@csrf_exempt
def api_itms_warehouses(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns active operating bond and all discovered warehouse facilities.
    POST: Sets the active operating bond facility (code, name).
    """
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


@csrf_exempt
def api_sync_bonds(request: HttpRequest) -> JsonResponse:
    """Dynamically queries ITMS WebApp and extracts available warehouse/bond facilities."""
    try:
        from core.services.itms_web_client import default_web_client
        res = default_web_client.fetch_warehouses_from_itms()
        return JsonResponse(res)
    except Exception as exc:
        logger.error("api_sync_bonds error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@csrf_exempt
@require_POST
def api_itms_connect(request: HttpRequest) -> JsonResponse:
    """Connects and authenticates against live ITMS WebApp (stock.itms.ug)."""
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
    ok, msg = auth_service.connect_itms_account(email, password, norm_url)
    status = auth_service.get_itms_status()
    return JsonResponse({
        "success": ok,
        "message": msg,
        "status": status,
        "active_bond": bond_service.get_active_bond(),
    }, status=200 if ok else 401)


@csrf_exempt
@require_POST
def api_itms_disconnect(request: HttpRequest) -> JsonResponse:
    """Disconnects from ITMS WebApp by clearing local session cookies."""
    try:
        from core.services.itms_web_client import get_web_client
        client = get_web_client()
        client.logout()
    except Exception as exc:
        logger.warning("ITMS disconnect error: %s", exc)

    return JsonResponse({
        "success": True,
        "message": "Disconnected from ITMS WebApp.",
        "status": auth_service.get_itms_status(),
        "active_bond": bond_service.get_active_bond(),
    })


@require_GET
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

    client = get_web_client()
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
            "has_photos": bool(o.front_photo_url or o.rear_photo_url or o.photos_json),
            "has_tracker": bool(o.gps_tracker_id),
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

    client = get_web_client()
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


@csrf_exempt
@require_POST
def api_itms_sync_now(request: HttpRequest) -> JsonResponse:
    """
    Directly triggers synchronization of active orders, archive orders, or
    installation kits from stock.itms.ug into the local database.
    """
    tab = request.POST.get("tab", "active").strip().lower()
    try:
        pages = max(1, min(50, int(request.POST.get("pages", 1))))
    except (ValueError, TypeError):
        pages = 1

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            tab = body.get("tab", tab)
            pages = max(1, min(50, int(body.get("pages", pages))))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("JSON decode error in api_itms_sync_now: %s", exc)

    client = get_web_client()
    if not client.session_store.session.is_cookie_valid():
        return JsonResponse({
            "success": False,
            "error": "ITMS WebApp session is not connected or has expired. Please connect your ITMS account.",
        }, status=401)

    from core.services.order_sync import OrderSyncService
    sync_svc = OrderSyncService(client=client)

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
        })

    if tab in ("shift", "both", "today", "all"):
        res = sync_svc.sync_shift_scoped(force=True)
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
            "message": res.get("message", "Shift sync complete."),
        })

    if tab == "archive":
        res = sync_svc.sync_archive_today(force=True)
        return JsonResponse({
            "success": True,
            "created": res.get("created", 0),
            "updated": res.get("updated", 0),
            "installed_verified": res.get("installed_verified", 0),
            "total_fetched": res.get("total_fetched", 0),
            "pages_fetched": res.get("pages_fetched", 0),
            "message": res.get("message", "Archive sync complete."),
        })

    # Default: active orders
    res = sync_svc.sync_active_orders(force=True, max_pages=25)
    return JsonResponse({
        "success": True,
        "created": res.get("created", 0),
        "updated": res.get("updated", 0),
        "total_fetched": res.get("total_active_seen", 0),
        "disappeared": res.get("disappeared_from_active", 0),
        "message": res.get("message", "Active orders sync complete."),
    })


@require_GET
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


@csrf_exempt
def api_itms_kits(request: HttpRequest) -> JsonResponse:
    """
    Lists or synchronizes ITMS Installation Kits stock inventory.
    """
    client = get_web_client()
    if request.method == "POST" or request.GET.get("sync") == "true":
        if not client.session_store.session.is_cookie_valid():
            return JsonResponse({"success": False, "error": "ITMS session not authenticated."}, status=401)
        from core.services.order_sync import OrderSyncService
        sync_svc = OrderSyncService(client=client)
        pages_to_sync = int(request.GET.get("pages", 25) or 25)
        res = sync_svc.sync_installation_kits_scoped(max_pages=pages_to_sync, force=True)
        return JsonResponse({"success": True, "sync": res})

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    wh_filter = request.GET.get("warehouse", "").strip()

    kits_qs = InstallationKit.objects.all().order_by("-created_date", "-id")
    if q:
        clean_q = normalizer.canonicalize(q) or q.upper()
        kits_qs = kits_qs.filter(
            Q(registration_number__icontains=clean_q) |
            Q(kit_code__icontains=clean_q) |
            Q(front_plate__icontains=q) |
            Q(rear_plate__icontains=q) |
            Q(gps_tracker__icontains=q)
        )
    if status_filter:
        kits_qs = kits_qs.filter(status__iexact=status_filter)
    if wh_filter:
        kits_qs = kits_qs.filter(warehouse__icontains=wh_filter)

    total = kits_qs.count()
    kits_data = [k.to_dict() for k in kits_qs[:100]]

    return JsonResponse({
        "success": True,
        "total": total,
        "kits": kits_data,
    })


