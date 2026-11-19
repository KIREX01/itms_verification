from django.urls import path

from backoffice import views

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
]
