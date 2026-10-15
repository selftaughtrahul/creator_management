from django.conf import settings
from django.db import models
from django.db.models import Q


class FacebookPage(models.Model):
    """A Facebook Page reachable from the user token on SocialAccount.

    The Page access token is requested during sync and is not stored.
    followers_count and fan_count are different: followers are follows,
    fan_count is Page likes.
    """

    class SyncStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(
        "networks.SocialAccount",
        on_delete=models.CASCADE,
        related_name="facebook_pages",
    )
    page_id = models.CharField(max_length=64)
    name = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255, blank=True)
    about = models.TextField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    link = models.TextField(blank=True)
    picture_url = models.TextField(blank=True)
    followers_count = models.BigIntegerField(null=True, blank=True)
    fan_count = models.BigIntegerField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.IDLE)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    granted_scopes = models.TextField(blank=True)
    insights_authorized = models.BooleanField(null=True, blank=True)
    reauthorization_required = models.BooleanField(default=False)
    summed_reactions = models.BigIntegerField(null=True, blank=True)
    summed_comments = models.BigIntegerField(null=True, blank=True)
    summed_shares = models.BigIntegerField(null=True, blank=True)
    summed_views = models.BigIntegerField(null=True, blank=True)
    summed_reach = models.BigIntegerField(null=True, blank=True)
    hidden_reaction_count = models.PositiveIntegerField(null=True, blank=True)
    posts_last_7_days = models.PositiveIntegerField(null=True, blank=True)
    posts_last_30_days = models.PositiveIntegerField(null=True, blank=True)
    posts_last_90_days = models.PositiveIntegerField(null=True, blank=True)
    posts_last_365_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(fields=["account", "page_id"], name="unique_facebook_page"),
        ]
        indexes = [models.Index(fields=["page_id"])]
        ordering = ["-is_primary", "name"]

    def __str__(self):
        return self.name or self.page_id


class FacebookPost(models.Model):
    page = models.ForeignKey(FacebookPage, on_delete=models.CASCADE, related_name="posts")
    post_id = models.CharField(max_length=128)
    message = models.TextField(blank=True)
    status_type = models.CharField(max_length=64, blank=True)
    permalink = models.TextField(blank=True)
    thumbnail_url = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    reaction_count = models.BigIntegerField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    comment_count = models.BigIntegerField(null=True, blank=True)
    share_count = models.BigIntegerField(null=True, blank=True)
    views = models.BigIntegerField(null=True, blank=True)
    reach = models.BigIntegerField(null=True, blank=True)
    clicks = models.BigIntegerField(null=True, blank=True)
    video_views = models.BigIntegerField(null=True, blank=True)
    avg_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    watch_time_ms = models.BigIntegerField(null=True, blank=True)
    insights_available = models.JSONField(default=dict, blank=True)
    comments_sync_status = models.CharField(max_length=32, blank=True)
    comments_sync_error = models.TextField(blank=True)
    comments_synced_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(fields=["page", "post_id"], name="unique_facebook_post"),
        ]
        indexes = [
            models.Index(fields=["post_id"]),
            models.Index(fields=["page", "published_at"]),
        ]
        ordering = ["-published_at", "post_id"]

    def __str__(self):
        return self.post_id


class FacebookComment(models.Model):
    post = models.ForeignKey(FacebookPost, on_delete=models.CASCADE, related_name="comment_rows")
    comment_id = models.CharField(max_length=128, unique=True)
    parent_comment_id = models.CharField(max_length=128, blank=True)
    author_name = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    reply_count = models.BigIntegerField(null=True, blank=True)
    is_reply = models.BooleanField(default=False)
    synced_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        indexes = [
            models.Index(fields=["post", "published_at"]),
            models.Index(fields=["parent_comment_id"]),
        ]
        ordering = ["-published_at", "comment_id"]

    def __str__(self):
        return self.comment_id


class FacebookSnapshot(models.Model):
    """Daily facts for a Page or one post.

    public_cumulative stores the Page node totals.
    insights_daily stores one Insights day. page_follows is the follower
    total that day. page_daily_follows_unique is new followers, stored as
    followers_gained. views stores page_media_view or post_media_view.
    Older impression metrics are used only when the current name is rejected.
    calculated_delta is the change between public follower totals.
    A blank post_key is the Page-level row.
    """

    class Scope(models.TextChoices):
        PUBLIC_CUMULATIVE = "public_cumulative", "Public cumulative"
        INSIGHTS_DAILY = "insights_daily", "Insights daily"
        INSIGHTS_RANGE = "insights_range", "Insights range"
        CALCULATED_DELTA = "calculated_delta", "Calculated snapshot delta"

    page = models.ForeignKey(FacebookPage, on_delete=models.CASCADE, related_name="snapshots")
    post = models.ForeignKey(
        FacebookPost,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    post_key = models.CharField(max_length=128, blank=True)
    snapshot_date = models.DateField()
    scope = models.CharField(max_length=32, choices=Scope.choices)
    period_start = models.DateField(null=True, blank=True)
    followers = models.BigIntegerField(null=True, blank=True)
    fans = models.BigIntegerField(null=True, blank=True)
    followers_gained = models.BigIntegerField(null=True, blank=True)
    followers_lost = models.BigIntegerField(null=True, blank=True)
    views = models.BigIntegerField(null=True, blank=True)
    reach = models.BigIntegerField(null=True, blank=True)
    engagements = models.BigIntegerField(null=True, blank=True)
    reactions = models.BigIntegerField(null=True, blank=True)
    comments = models.BigIntegerField(null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    clicks = models.BigIntegerField(null=True, blank=True)
    video_views = models.BigIntegerField(null=True, blank=True)
    avg_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    watch_time_ms = models.BigIntegerField(null=True, blank=True)
    counter_reset = models.BooleanField(default=False)
    source = models.CharField(max_length=40)
    collected_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["page", "post_key", "snapshot_date", "scope"],
                name="unique_facebook_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["page", "snapshot_date"]),
            models.Index(fields=["scope", "snapshot_date"]),
        ]
        ordering = ["snapshot_date", "post_key"]

    def __str__(self):
        target = self.post_key or self.page.page_id
        return f"{target} {self.scope} {self.snapshot_date}"


class FacebookInsightBreakdown(models.Model):
    snapshot = models.ForeignKey(FacebookSnapshot, on_delete=models.CASCADE, related_name="breakdowns")
    dimension = models.CharField(max_length=40)
    segment = models.CharField(max_length=80)
    value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "dimension", "segment"],
                name="unique_facebook_breakdown",
            ),
        ]
        ordering = ["dimension", "-value", "segment"]

    def __str__(self):
        return f"{self.dimension}:{self.segment}"


class FacebookSyncJob(models.Model):
    class Mode(models.TextChoices):
        FULL = "full", "Full"
        INCREMENTAL = "incremental", "Incremental"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial success"
        RETRYABLE = "retryable", "Retryable failure"
        PERMANENT = "permanent", "Permanent failure"
        PERMISSION_DENIED = "permission_denied", "Permission denied"
        UNAVAILABLE = "unavailable", "Data unavailable"

    account = models.ForeignKey(
        "networks.SocialAccount",
        on_delete=models.CASCADE,
        related_name="facebook_sync_jobs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facebook_sync_requests",
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.INCREMENTAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    outcome = models.CharField(max_length=32, choices=Outcome.choices, blank=True)
    stages = models.JSONField(default=dict, blank=True)
    granted_scopes = models.TextField(blank=True)
    pages_seen = models.PositiveIntegerField(default=0)
    posts_seen = models.PositiveIntegerField(default=0)
    posts_upserted = models.PositiveIntegerField(default=0)
    comments_upserted = models.PositiveIntegerField(default=0)
    api_calls = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "networks"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account"],
                condition=Q(status__in=["pending", "running"]),
                name="unique_open_facebook_sync",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["account", "status"]),
        ]

    def __str__(self):
        return f"Facebook sync {self.pk} ({self.status})"
