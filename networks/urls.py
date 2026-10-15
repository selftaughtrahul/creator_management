from django.urls import path

from networks import views
from networks.youtube import api as youtube_api

app_name = "networks"

urlpatterns = [
    path("api/youtube/channels/", youtube_api.channel_list, name="youtube_channels"),
    path("api/youtube/channels/<str:channel_id>/", youtube_api.channel_detail, name="youtube_channel"),
    path("api/youtube/channels/<str:channel_id>/videos/", youtube_api.channel_videos, name="youtube_videos"),
    path("api/youtube/channels/<str:channel_id>/analytics/", youtube_api.channel_analytics, name="youtube_channel_analytics"),
    path("api/youtube/channels/<str:channel_id>/snapshots/", youtube_api.channel_snapshots, name="youtube_snapshots"),
    path("api/youtube/channels/<str:channel_id>/sync/", youtube_api.channel_sync, name="youtube_sync"),
    path("api/youtube/videos/<str:video_id>/", youtube_api.video_detail, name="youtube_video"),
    path("api/youtube/videos/<str:video_id>/analytics/", youtube_api.video_analytics, name="youtube_video_analytics"),
    path("api/youtube/videos/<str:video_id>/comments/", youtube_api.video_comments, name="youtube_comments"),
    path("api/youtube/videos/<str:video_id>/transcript/", youtube_api.video_transcript, name="youtube_transcript"),
    path("api/youtube/sync-jobs/<int:job_id>/", youtube_api.sync_job_detail, name="youtube_job"),
    path("connections/", views.connections, name="connections"),
    path("connections/sync/", views.sync_all, name="sync_all"),
    path("connections/<slug:platform>/start/", views.start_oauth, name="start"),
    path("connections/<slug:platform>/callback/", views.oauth_callback, name="oauth_callback"),
    path("connections/<slug:platform>/sample/", views.load_sample, name="sample"),
    path("connections/<slug:platform>/sync/", views.sync, name="sync"),
    path("connections/<slug:platform>/disconnect/", views.disconnect, name="disconnect"),
]
