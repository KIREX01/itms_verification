"""
Route → capability table, enforced by governance.middleware.GovernanceMiddleware.

Every operator-facing view is listed with the capability needed for safe (GET/HEAD) and
unsafe (POST/PUT/PATCH/DELETE) requests. A tuple means "any of these". ADMIN means
superuser-only. PUBLIC means no capability check (the view handles its own access).

Deny by default: an /api/ view that is not listed here is refused for non-administrators,
so a new endpoint cannot ship unprotected.
"""
from __future__ import annotations

import json
from typing import Callable, Dict, Tuple, Union

from governance import capabilities as C
from governance.services.authz import ANY_MEMBER

ADMIN = "__admin__"
PUBLIC = "__public__"

Spec = Union[str, Tuple[str, ...]]
# view name -> {"GET": spec, "POST": spec}  ("POST" covers every unsafe method)
VIEW_CAPABILITIES: Dict[str, Dict[str, Spec]] = {
    # --- pages ---
    "dashboard_view": {"GET": ANY_MEMBER},
    "upload_photos_view": {"GET": C.CAPTURE_UPLOAD, "POST": C.CAPTURE_UPLOAD},
    "mobile_companion_view": {"GET": (C.CAPTURE_UPLOAD, C.STOCK_SCAN)},
    "serve_media": {"GET": (C.QUEUE_VIEW, C.CAPTURE_UPLOAD, C.STOCK_VIEW, C.STOCK_SCAN)},

    # --- self-protecting / public ---
    "api_itms_status": {"GET": PUBLIC},
    "api_security_overview": {"GET": ADMIN},
    "api_security_action": {"POST": ADMIN},
    "api_me_capabilities": {"GET": PUBLIC},
    "api_me_profile": {"GET": PUBLIC},
    "api_me_device_info": {"POST": PUBLIC},  # any signed-in device describes itself
    "install_view": {"GET": PUBLIC, "POST": PUBLIC},  # self-protecting: administrator-only POST
    "device_access_view": {"GET": PUBLIC, "POST": PUBLIC},  # unknown devices request access

    # --- ITMS connection & orders ---
    "api_itms_connect": {"POST": C.ITMS_CONNECT},
    "api_itms_disconnect": {"POST": C.ITMS_CONNECT},
    "api_itms_warehouses": {"GET": ANY_MEMBER, "POST": C.SETTINGS_OPERATOR},
    "api_sync_bonds": {"GET": C.ITMS_CONNECT, "POST": C.ITMS_CONNECT},
    "api_itms_orders_explorer": {"GET": (C.ORDERS_VIEW, C.QUEUE_VIEW)},
    "api_itms_sync_now": {"GET": C.ITMS_CONNECT, "POST": C.ITMS_CONNECT},
    "api_itms_order_detail": {"GET": (C.ORDERS_VIEW, C.QUEUE_VIEW)},
    "api_itms_order_install": {"POST": C.ITMS_SUBMIT},
    "api_itms_kits": {"GET": (C.ORDERS_VIEW, C.STOCK_VIEW)},
    "api_itms_plate_lifecycle": {"GET": (C.ORDERS_VIEW, C.STOCK_VIEW)},
    "api_orders_list": {"GET": (C.ORDERS_VIEW, C.QUEUE_VIEW)},
    "api_sync_orders": {"GET": C.ITMS_CONNECT, "POST": C.ITMS_CONNECT},
    "api_seed_orders": {"GET": ADMIN, "POST": ADMIN},

    # --- dashboard / reports / history ---
    "api_reports_totals": {"GET": (C.REPORTS_VIEW, C.DASHBOARD_VIEW)},
    "api_stats": {"GET": (C.DASHBOARD_VIEW, C.CAPTURE_UPLOAD)},
    "api_stream_events": {"GET": ANY_MEMBER},
    "api_export_report": {"GET": C.REPORTS_EXPORT},
    "api_history_list": {"GET": C.HISTORY_VIEW},
    "api_network_info": {"GET": ANY_MEMBER},

    # --- review queue ---
    "api_pairs_list": {"GET": C.QUEUE_VIEW},
    "api_pair_create": {"POST": C.QUEUE_EDIT},
    "api_pair_detail": {"GET": C.QUEUE_VIEW},
    "api_pair_action": {"POST": "__pair_action__"},
    "api_batch_submit": {"POST": C.ITMS_SUBMIT},
    "api_run_pipeline": {"POST": C.PIPELINE_RUN},
    "api_pipeline_status": {"GET": (C.QUEUE_VIEW, C.CAPTURE_UPLOAD)},
    "api_batches_list": {"GET": (C.QUEUE_VIEW, C.CAPTURE_UPLOAD)},
    "api_batch_detail": {"GET": (C.QUEUE_VIEW, C.CAPTURE_UPLOAD), "POST": C.QUEUE_EDIT},
    "api_batch_candidates": {"GET": C.QUEUE_VIEW},
    "api_replace_batch_image": {"POST": C.QUEUE_EDIT},
    "api_upload_photos": {"POST": C.CAPTURE_UPLOAD},

    # --- settings & system ---
    "api_settings": {"GET": ANY_MEMBER, "POST": C.SETTINGS_OPERATOR},
    "api_shift_date": {"GET": ANY_MEMBER, "POST": C.SETTINGS_OPERATOR},
    "api_toggle_dry_run": {"POST": C.ITMS_SUBMIT},
    "api_vault_folder": {"GET": ADMIN, "POST": ADMIN},
    "api_browse_vault_folder": {"GET": ADMIN, "POST": ADMIN},
    "api_browse_path": {"GET": ADMIN, "POST": ADMIN},
    "api_check_updates": {"GET": ADMIN},
    "api_apply_update": {"POST": ADMIN},

    # --- mobile companion ---
    "api_mobile_ping": {"GET": (C.CAPTURE_UPLOAD, C.STOCK_SCAN), "POST": (C.CAPTURE_UPLOAD, C.STOCK_SCAN)},
    "api_mobile_disconnect": {"POST": (C.CAPTURE_UPLOAD, C.STOCK_SCAN)},
    "api_mobile_status": {"GET": (C.CAPTURE_UPLOAD, C.STOCK_SCAN)},
    "api_mobile_new_batch": {"POST": C.CAPTURE_UPLOAD},
    "api_mobile_upload": {"POST": C.CAPTURE_UPLOAD},
    "api_mobile_uturn_status": {"GET": C.CAPTURE_UPLOAD},
    "api_mobile_uturn_abort": {"POST": C.CAPTURE_UPLOAD},
    "api_mobile_uturn_undo_snap": {"POST": C.CAPTURE_UPLOAD},
    "api_mobile_uturn_finish": {"POST": C.CAPTURE_UPLOAD},

    # --- stock: reads ---
    "api_stock_reconciliation": {"GET": C.STOCK_VIEW},
    "api_stock_shift_dates": {"GET": C.STOCK_VIEW},
    "api_stock_shift_details": {"GET": C.STOCK_VIEW},
    "api_stock_unallocated": {"GET": C.STOCK_VIEW},
    "api_stock_kits_readiness": {"GET": (C.STOCK_VIEW, C.STOCK_SCAN), "POST": C.STOCK_EDIT},
    "api_stock_previous_closing": {"GET": C.STOCK_VIEW},
    "api_stock_delivery_notes": {"GET": C.STOCK_VIEW},
    "api_stock_inspect": {"GET": (C.STOCK_VIEW, C.STOCK_SCAN)},
    "api_stock_blocked_plates": {"GET": (C.STOCK_VIEW, C.STOCK_SCAN), "POST": C.STOCK_SCAN},
    # --- stock: exports ---
    "api_stock_export_csv": {"GET": C.REPORTS_EXPORT},
    "api_stock_export_category_csv": {"GET": C.REPORTS_EXPORT},
    "api_stock_export_unregistered_csv": {"GET": C.REPORTS_EXPORT},
    "api_stock_mvr_docket": {"GET": C.REPORTS_EXPORT, "POST": C.REPORTS_EXPORT},
    "api_stock_report_email": {"POST": C.REPORTS_EXPORT},
    # --- stock: scans (Helper) ---
    "api_stock_dispatch": {"POST": C.STOCK_SCAN},
    "api_stock_return": {"POST": C.STOCK_SCAN},
    "api_stock_audit": {"POST": C.STOCK_SCAN},
    "api_stock_verify_batch": {"POST": C.STOCK_SCAN},
    # --- stock: edits (Team Leader) ---
    "api_stock_shift_reconcile": {"GET": C.STOCK_VIEW, "POST": C.STOCK_EDIT},
    "api_stock_delivery": {"GET": C.STOCK_VIEW, "POST": C.STOCK_EDIT},
    "api_stock_delivery_detail": {"GET": C.STOCK_VIEW, "POST": C.STOCK_EDIT},
    "api_stock_upload_delivery_note": {"POST": C.STOCK_EDIT},
    "api_stock_bond_transfer": {"POST": C.STOCK_EDIT},
    "api_stock_scheduled": {"POST": C.STOCK_EDIT},
    "api_stock_opening": {"POST": C.STOCK_EDIT},
    "api_stock_physical_count": {"POST": C.STOCK_EDIT},
    "api_stock_dispatch_clear": {"POST": C.STOCK_EDIT},
    "api_stock_ledger_entry": {"POST": C.STOCK_EDIT},
    "api_stock_kits_sync": {"POST": (C.ITMS_CONNECT, C.STOCK_EDIT)},

    # --- quality flags & team (added in later phases) ---
    "api_flags_list": {"GET": (C.QUEUE_VIEW, C.FLAG_RAISE)},
    "api_flag_raise": {"POST": C.FLAG_RAISE},
    "api_flag_resolve": {"POST": C.FLAG_RESOLVE},
    "api_team": {"GET": C.TEAM_INVITE, "POST": C.TEAM_INVITE},
}

# Pair actions: action name -> capability (anything else is an edit).
PAIR_ACTION_CAPABILITIES: Dict[str, str] = {
    "approve": C.QUEUE_APPROVE,
    "submit": C.ITMS_SUBMIT,
    "delete_pair": C.PAIR_DELETE,
}


def pair_action_capability(request) -> str:
    action = request.POST.get("action") if request.content_type != "application/json" else None
    if not action and request.body:
        try:
            action = json.loads(request.body.decode("utf-8")).get("action")
        except (ValueError, UnicodeDecodeError, AttributeError):
            action = None
    action = (action or "").strip().lower()
    return PAIR_ACTION_CAPABILITIES.get(action, C.QUEUE_EDIT)


SPECIAL_RESOLVERS: Dict[str, Callable] = {
    "__pair_action__": pair_action_capability,
}

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def spec_for(view_name: str, method: str):
    """Returns the capability spec for a view/method, or None if the view is unlisted."""
    entry = VIEW_CAPABILITIES.get(view_name)
    if entry is None:
        return None
    key = "GET" if method in SAFE_METHODS else "POST"
    if key in entry:
        return entry[key]
    # Method not declared for this view: unsafe methods fall back to the strictest declared spec.
    return entry.get("POST") or entry.get("GET")
