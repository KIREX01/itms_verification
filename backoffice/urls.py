from django.urls import path

from backoffice import sync_api, twofa_views, views

app_name = "backoffice"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("access/", views.access_list, name="access_list"),
    path("access/<int:pk>/", views.access_detail, name="access_detail"),
    path("people/", views.people_list, name="people_list"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("bonds/", views.bonds, name="bonds"),
    path("controls/", views.controls, name="controls"),
    path("devices/", views.devices, name="devices"),
    path("reports/", views.reports, name="reports"),
    path("audit/", views.audit_log, name="audit"),
    path("technical/", views.technical, name="technical"),
    path("installs/", views.installs, name="installs"),
    path("services/", views.services, name="services"),
    path("signins/", views.signins, name="signins"),
    path("roles/", views.roles, name="roles"),
    path("2fa/", twofa_views.twofa_verify, name="twofa_verify"),
    path("2fa/setup/", twofa_views.twofa_setup, name="twofa_setup"),
    # Machine-to-machine policy sync (HMAC-authenticated; central server only)
    path("api/v1/installs/enroll/", sync_api.install_enroll, name="install_enroll"),
    path("api/v1/installs/heartbeat/", sync_api.install_heartbeat, name="install_heartbeat"),
]
