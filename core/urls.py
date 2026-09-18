from django.urls import path

from core import views

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

    # REST APIs for Operator Workflow
    path("api/stats/", views.api_stats, name="api_stats"),
    path("api/pairs/", views.api_pairs_list, name="api_pairs_list"),
    path("api/pairs/<int:pair_id>/", views.api_pair_detail, name="api_pair_detail"),
    path("api/pairs/<int:pair_id>/action/", views.api_pair_action, name="api_pair_action"),
    path("api/orders/", views.api_orders_list, name="api_orders_list"),
    path("api/orders/sync/", views.api_sync_orders, name="api_sync_orders"),
    path("api/orders/seed/", views.api_seed_orders, name="api_seed_orders"),
    path("api/pipeline/run/", views.api_run_pipeline, name="api_run_pipeline"),
    path("api/pipeline/status/", views.api_pipeline_status, name="api_pipeline_status"),
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
]
