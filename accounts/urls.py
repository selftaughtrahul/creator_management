from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("", views.home, name="home"),
    path("profile/", views.profile, name="profile"),
]
