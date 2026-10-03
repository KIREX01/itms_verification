"""
Views package for ITMS Verification Copilot.
Decomposed from monolithic views.py into domain modules:
- auth: Authentication & Operator Onboarding
- dashboard: Operator Web Dashboard, metrics, stats, totals, and audit history
- pairs: Pair Review & Approval REST APIs
- upload: Evidence photo upload, batch ingestion, and batch management
- orders: Order registry search, live synchronization, and ITMS WebApp explorer APIs
- bond: Stock Monitoring & Daily Plate Reconciliation / Bond Warehouse REST APIs
- system: System configuration, pipeline execution, media serving, and updater REST APIs

All symbols are re-exported here for 100% backward compatibility.
"""

from .auth import (
    login_view,
    signup_view,
    logout_view,
)

from .dashboard import (
    dashboard_view,
    api_stats,
    api_reports_totals,
    api_history_list,
)

from .pairs import (
    api_pairs_list,
    api_pair_detail,
    api_pair_action,
    api_batch_submit,
)

from .upload import (
    upload_photos_view,
    api_upload_photos,
    api_batch_detail,
    api_batches_list,
)

from .orders import (
    api_orders_list,
    api_sync_orders,
    api_seed_orders,
    api_itms_status,
    api_itms_warehouses,
    api_itms_connect,
    api_itms_disconnect,
    api_itms_orders_explorer,
    api_itms_order_detail,
    api_itms_sync_now,
    api_itms_plate_lifecycle,
    api_itms_kits,
)

from .bond import (
    api_stock_reconciliation,
    api_stock_dispatch,
    api_stock_delivery,
    api_stock_return,
    api_stock_bond_transfer,
    api_stock_scheduled,
    api_stock_opening,
    api_stock_physical_count,
    api_stock_export_csv,
    api_stock_delivery_notes,
    api_stock_delivery_detail,
    api_stock_upload_delivery_note,
    api_stock_mvr_docket,
    api_stock_shift_reconcile,
    api_stock_export_category_csv,
    api_stock_kits_sync,
    api_stock_kits_readiness,
)

from .system import (
    api_run_pipeline,
    api_pipeline_status,
    api_stream_events,
    api_export_report,
    api_settings,
    api_toggle_dry_run,
    api_check_updates,
    api_apply_update,
    _get_dir_disk_stats,
    api_vault_folder,
    api_browse_vault_folder,
    serve_media,
)

__all__ = [
    "_get_dir_disk_stats",
    "api_apply_update",
    "api_batch_detail",
    "api_batch_submit",
    "api_batches_list",
    "api_browse_vault_folder",
    "api_check_updates",
    "api_export_report",
    "api_history_list",
    "api_itms_connect",
    "api_itms_disconnect",
    "api_itms_kits",
    "api_itms_order_detail",
    "api_itms_orders_explorer",
    "api_itms_plate_lifecycle",
    "api_itms_status",
    "api_itms_sync_now",
    "api_itms_warehouses",
    "api_orders_list",
    "api_pair_action",
    "api_pair_detail",
    "api_pairs_list",
    "api_pipeline_status",
    "api_reports_totals",
    "api_run_pipeline",
    "api_seed_orders",
    "api_settings",
    "api_stats",
    "api_stock_bond_transfer",
    "api_stock_delivery",
    "api_stock_delivery_detail",
    "api_stock_delivery_notes",
    "api_stock_dispatch",
    "api_stock_export_category_csv",
    "api_stock_export_csv",
    "api_stock_kits_readiness",
    "api_stock_kits_sync",
    "api_stock_mvr_docket",
    "api_stock_opening",
    "api_stock_physical_count",
    "api_stock_reconciliation",
    "api_stock_return",
    "api_stock_scheduled",
    "api_stock_shift_reconcile",
    "api_stock_upload_delivery_note",
    "api_stream_events",
    "api_sync_orders",
    "api_toggle_dry_run",
    "api_upload_photos",
    "api_vault_folder",
    "dashboard_view",
    "login_view",
    "logout_view",
    "serve_media",
    "signup_view",
    "upload_photos_view",
]
