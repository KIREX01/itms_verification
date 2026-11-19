from django.urls import path

from governance import views

app_name = "governance"

urlpatterns = [
    path("api/me/capabilities/", views.api_me_capabilities, name="api_me_capabilities"),
    path("api/me/profile/", views.api_me_profile, name="api_me_profile"),
    path("api/team/", views.api_team, name="api_team"),
]
