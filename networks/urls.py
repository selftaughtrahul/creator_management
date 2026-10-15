from django.urls import path

from networks import views
from networks.facebook import api as facebook_api
from networks.instagram import api as instagram_api
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
    path("api/instagram/profile/", instagram_api.profile_detail, name="instagram_profile"),
    path("api/instagram/media/", instagram_api.media_list, name="instagram_media"),
    path("api/instagram/media/<str:media_id>/", instagram_api.media_detail, name="instagram_media_detail"),
    path("api/instagram/media/<str:media_id>/comments/", instagram_api.media_comments, name="instagram_comments"),
    path("api/instagram/insights/", instagram_api.insights, name="instagram_insights"),
    path("api/instagram/sync/", instagram_api.sync_now, name="instagram_sync"),
    path("api/instagram/sync-jobs/<int:job_id>/", instagram_api.sync_job_detail, name="instagram_job"),
    path("api/facebook/pages/", facebook_api.page_list, name="facebook_pages"),
    path("api/facebook/pages/<str:page_id>/", facebook_api.page_detail, name="facebook_page"),
    path("api/facebook/pages/<str:page_id>/posts/", facebook_api.page_posts, name="facebook_posts"),
    path("api/facebook/pages/<str:page_id>/insights/", facebook_api.page_insights, name="facebook_insights"),
    path("api/facebook/posts/<str:post_id>/", facebook_api.post_detail, name="facebook_post"),
    path("api/facebook/posts/<str:post_id>/comments/", facebook_api.post_comments, name="facebook_comments"),
    path("api/facebook/sync/", facebook_api.sync_now, name="facebook_sync"),
    path("api/facebook/sync-jobs/<int:job_id>/", facebook_api.sync_job_detail, name="facebook_job"),
    path("connections/", views.connections, name="connections"),
    path("connections/sync/", views.sync_all, name="sync_all"),
    path("connections/<slug:platform>/start/", views.start_oauth, name="start"),
    path("connections/<slug:platform>/callback/", views.oauth_callback, name="oauth_callback"),
    path("connections/<slug:platform>/sample/", views.load_sample, name="sample"),
    path("connections/<slug:platform>/sync/", views.sync, name="sync"),
    path("connections/<slug:platform>/disconnect/", views.disconnect, name="disconnect"),
]
