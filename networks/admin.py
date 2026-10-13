from django.contrib import admin

from networks.models import ContentItem, MetricSnapshot, SocialAccount


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
    exclude = ("access_token", "refresh_token", "resource_token")
    inlines = [SnapshotInline]

    @admin.display(boolean=True, description="Token stored")
    def token_stored(self, obj):
        return obj.has_token


@admin.register(ContentItem)
class ContentItemAdmin(admin.ModelAdmin):
    list_display = ("title", "account", "content_type", "views", "likes", "published_at")
    list_filter = ("content_type", "account__platform")
    search_fields = ("title", "caption", "external_id")
