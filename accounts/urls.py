from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy/", views.privacy, name="privacy"),
    path("data-deletion/", views.data_deletion, name="data_deletion"),
    path("profile/", views.profile, name="profile"),
]
