from django.urls import path

from core import views, views_mobile

app_name = "core"

urlpatterns = [
    # Authentication & Operator Onboarding
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),

    # Operator Web Dashboard
    path("", views.dashboard_view, name="dashboard"),
    path("upload/", views.upload_photos_view, name="upload_photos"),

    # ITMS WebApp Account Connection & Status
    path("api/itms/status/", views.api_itms_status, name="api_itms_status"),
    path("api/itms/connect/", views.api_itms_connect, name="api_itms_connect"),
    path("api/itms/disconnect/", views.api_itms_disconnect, name="api_itms_disconnect"),

    # ITMS WebApp Orders & Archive Explorer APIs
    path("api/itms/orders/", views.api_itms_orders_explorer, name="api_itms_orders_explorer"),
    path("api/itms/orders/sync/now/", views.api_itms_sync_now, name="api_itms_sync_now"),
    path("api/itms/orders/<str:order_ident>/", views.api_itms_order_detail, name="api_itms_order_detail"),
    path("api/itms/kits/", views.api_itms_kits, name="api_itms_kits"),
    path("api/itms/plate-lifecycle/", views.api_itms_plate_lifecycle, name="api_itms_plate_lifecycle"),

    # REST APIs for Operator Workflow & Reports
    path("api/reports/totals/", views.api_reports_totals, name="api_reports_totals"),
    path("api/stats/", views.api_stats, name="api_stats"),
    path("api/pairs/", views.api_pairs_list, name="api_pairs_list"),
    path("api/pairs/<int:pair_id>/", views.api_pair_detail, name="api_pair_detail"),
    path("api/pairs/<int:pair_id>/action/", views.api_pair_action, name="api_pair_action"),
    path("api/orders/", views.api_orders_list, name="api_orders_list"),
    path("api/orders/sync/", views.api_sync_orders, name="api_sync_orders"),
    path("api/orders/seed/", views.api_seed_orders, name="api_seed_orders"),
    path("api/pipeline/run/", views.api_run_pipeline, name="api_run_pipeline"),
    path("api/pipeline/status/", views.api_pipeline_status, name="api_pipeline_status"),
    path("api/stream/events/", views.api_stream_events, name="api_stream_events"),
    path("api/submissions/batch/", views.api_batch_submit, name="api_batch_submit"),
    path("api/export/report/", views.api_export_report, name="api_export_report"),
    path("api/settings/", views.api_settings, name="api_settings"),
    path("api/settings/toggle-dry-run/", views.api_toggle_dry_run, name="api_toggle_dry_run"),
    path("api/settings/vault-folder/", views.api_vault_folder, name="api_vault_folder"),
    path("api/settings/browse-vault-folder/", views.api_browse_vault_folder, name="api_browse_vault_folder"),
    path("api/updates/check/", views.api_check_updates, name="api_check_updates"),
    path("api/updates/apply/", views.api_apply_update, name="api_apply_update"),
    path("media/<path:path>", views.serve_media, name="serve_media"),

    # File Ingestion & Batches REST APIs
    path("api/upload/", views.api_upload_photos, name="api_upload_photos"),
    path("api/batches/", views.api_batches_list, name="api_batches_list"),
    path("api/batches/<str:batch_id>/", views.api_batch_detail, name="api_batch_detail"),

    # History & Audit Trail REST API
    path("api/history/", views.api_history_list, name="api_history_list"),

    # Wireless Mobile Companion & Direct Ingestion APIs
    path("mobile/", views_mobile.mobile_companion_view, name="mobile_companion"),
    path("api/mobile/ping/", views_mobile.api_mobile_ping, name="api_mobile_ping"),
    path("api/mobile/disconnect/", views_mobile.api_mobile_disconnect, name="api_mobile_disconnect"),
    path("api/mobile/new-batch/", views_mobile.api_mobile_new_batch, name="api_mobile_new_batch"),
    path("api/mobile/status/", views_mobile.api_mobile_status, name="api_mobile_status"),
    path("api/mobile/upload/", views_mobile.api_mobile_upload, name="api_mobile_upload"),
    path("api/network/info/", views_mobile.api_network_info, name="api_network_info"),

    # Stock Monitoring & Daily Plate Reconciliation APIs
    path("api/stock/reconciliation/", views.api_stock_reconciliation, name="api_stock_reconciliation"),
    path("api/stock/dispatch/", views.api_stock_dispatch, name="api_stock_dispatch"),
    path("api/stock/delivery/", views.api_stock_delivery, name="api_stock_delivery"),
    path("api/stock/transfer/", views.api_stock_bond_transfer, name="api_stock_bond_transfer"),
    path("api/stock/scheduled/", views.api_stock_scheduled, name="api_stock_scheduled"),
    path("api/stock/return/", views.api_stock_return, name="api_stock_return"),
    path("api/stock/opening/", views.api_stock_opening, name="api_stock_opening"),
    path("api/stock/physical/", views.api_stock_physical_count, name="api_stock_physical_count"),
    path("api/stock/export/", views.api_stock_export_csv, name="api_stock_export_csv"),
]
