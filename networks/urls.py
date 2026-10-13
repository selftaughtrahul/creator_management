from django.urls import path

from networks import views

app_name = "networks"

urlpatterns = [
    path("connections/", views.connections, name="connections"),
    path("connections/sync/", views.sync_all, name="sync_all"),
    path("connections/<slug:platform>/start/", views.start_oauth, name="start"),
    path("connections/<slug:platform>/callback/", views.oauth_callback, name="oauth_callback"),
    path("connections/<slug:platform>/sample/", views.load_sample, name="sample"),
    path("connections/<slug:platform>/sync/", views.sync, name="sync"),
    path("connections/<slug:platform>/disconnect/", views.disconnect, name="disconnect"),
]
