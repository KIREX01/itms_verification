from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/static/core/logos/favicon.ico", permanent=True)),
    path("apple-touch-icon.png", RedirectView.as_view(url="/static/core/logos/apple-touch-icon.png", permanent=True)),
    path("site.webmanifest", RedirectView.as_view(url="/static/core/logos/site.webmanifest", permanent=True)),
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
