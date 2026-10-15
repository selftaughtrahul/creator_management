from django.contrib import admin

from networks.models import AudienceBreakdown, ContentItem, MetricSnapshot, SocialAccount
from networks.facebook_models import FacebookPage, FacebookPost, FacebookSyncJob
from networks.instagram_models import InstagramMedia, InstagramProfile, InstagramSyncJob
from networks.youtube_models import (
    YouTubeChannel,
    YouTubeSnapshot,
    YouTubeSyncJob,
    YouTubeTranscript,
    YouTubeVideo,
    YouTubeVideoComment,
)


class SnapshotInline(admin.TabularInline):
    model = MetricSnapshot
    extra = 0
    fields = ("captured_at", "followers", "views", "likes", "comments", "shares")
    readonly_fields = fields


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "platform",
        "display_name",
        "is_sample",
        "last_synced_at",
        "token_stored",
    )
    list_filter = ("platform", "is_sample")
    search_fields = ("display_name", "username", "external_id", "user__email")
    exclude = ("access_token", "refresh_token")
    inlines = [SnapshotInline]

    @admin.display(boolean=True, description="Token stored")
    def token_stored(self, obj):
        return obj.has_token


@admin.register(AudienceBreakdown)
class AudienceBreakdownAdmin(admin.ModelAdmin):
    list_display = ("snapshot", "dimension", "label", "count", "basis")
    list_filter = ("dimension", "basis", "snapshot__account__platform")
    search_fields = ("label", "segment")


@admin.register(YouTubeChannel)
class YouTubeChannelAdmin(admin.ModelAdmin):
    list_display = ("title", "channel_id", "account", "sync_status", "subscriber_count", "last_success_at")
    search_fields = ("title", "channel_id", "custom_url")
    list_filter = ("sync_status", "reauthorization_required")


@admin.register(YouTubeVideo)
class YouTubeVideoAdmin(admin.ModelAdmin):
    list_display = ("title", "video_id", "channel", "privacy_status", "view_count", "published_at")
    search_fields = ("title", "video_id")
    list_filter = ("privacy_status", "availability")


@admin.register(YouTubeVideoComment)
class YouTubeVideoCommentAdmin(admin.ModelAdmin):
    list_display = ("comment_id", "video", "author_display_name", "like_count", "is_reply", "published_at")
    search_fields = ("comment_id", "author_display_name", "text")


@admin.register(YouTubeTranscript)
class YouTubeTranscriptAdmin(admin.ModelAdmin):
    list_display = ("video", "language", "source", "status", "last_success_at")
    list_filter = ("status", "source")
    search_fields = ("video__video_id", "language", "track_name")


@admin.register(YouTubeSnapshot)
class YouTubeSnapshotAdmin(admin.ModelAdmin):
    list_display = ("channel", "video_key", "snapshot_date", "scope", "views", "source")
    list_filter = ("scope", "source")


@admin.register(YouTubeSyncJob)
class YouTubeSyncJobAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "mode", "status", "outcome", "videos_upserted", "created_at")
    list_filter = ("status", "outcome", "mode")


@admin.register(ContentItem)
class ContentItemAdmin(admin.ModelAdmin):
    list_display = ("title", "account", "content_type", "views", "likes", "published_at")
    list_filter = ("content_type", "account__platform")
    search_fields = ("title", "caption", "external_id")


@admin.register(InstagramProfile)
class InstagramProfileAdmin(admin.ModelAdmin):
    list_display = ("username", "ig_user_id", "account", "sync_status", "followers_count", "last_success_at")
    search_fields = ("username", "name", "ig_user_id")
    list_filter = ("sync_status", "reauthorization_required")


@admin.register(InstagramMedia)
class InstagramMediaAdmin(admin.ModelAdmin):
    list_display = ("media_id", "profile", "media_product_type", "like_count", "views", "published_at")
    search_fields = ("media_id", "caption")


@admin.register(InstagramSyncJob)
class InstagramSyncJobAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "mode", "status", "outcome", "media_upserted", "created_at")
    list_filter = ("status", "outcome", "mode")


@admin.register(FacebookPage)
class FacebookPageAdmin(admin.ModelAdmin):
    list_display = ("name", "page_id", "account", "is_primary", "sync_status", "followers_count", "fan_count")
    search_fields = ("name", "username", "page_id")
    list_filter = ("sync_status", "is_primary", "reauthorization_required")


@admin.register(FacebookPost)
class FacebookPostAdmin(admin.ModelAdmin):
    list_display = ("post_id", "page", "status_type", "reaction_count", "views", "published_at")
    search_fields = ("post_id", "message")


@admin.register(FacebookSyncJob)
class FacebookSyncJobAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "mode", "status", "outcome", "posts_upserted", "created_at")
    list_filter = ("status", "outcome", "mode")
