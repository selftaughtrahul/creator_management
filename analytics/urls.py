from django.urls import path

from analytics import views

app_name = "analytics"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("analytics/<slug:platform>/", views.platform, name="platform"),
    path("content/<int:pk>/", views.content, name="content"),
]
