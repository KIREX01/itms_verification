from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("upload/", views.upload_photos_view, name="upload_photos"),
    path("api/upload/", views.api_upload_photos, name="api_upload_photos"),
    path("api/batches/<str:batch_id>/", views.api_batch_detail, name="api_batch_detail"),
]
