from django.conf import settings
from django.db import models
from django.db.models import Q


class YouTubeChannel(models.Model):
    """Channel metadata for a connected YouTube account.

    OAuth tokens stay on SocialAccount. One Google connection can own more
    than one channel, so this is not a one-to-one profile.
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
        related_name="youtube_channels",
    )
    channel_id = models.CharField(max_length=64)
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    custom_url = models.CharField(max_length=255, blank=True)
    thumbnail_urls = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    country = models.CharField(max_length=8, blank=True)
    subscriber_count = models.BigIntegerField(null=True, blank=True)
    subscriber_count_hidden = models.BooleanField(default=False)
    view_count = models.BigIntegerField(null=True, blank=True)
    video_count = models.BigIntegerField(null=True, blank=True)
    uploads_playlist_id = models.CharField(max_length=64, blank=True)
    sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.IDLE)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    data_source = models.CharField(max_length=40, default="youtube_data_api")
    source_updated_at = models.DateTimeField(null=True, blank=True)
    granted_scopes = models.TextField(blank=True)
    analytics_authorized = models.BooleanField(null=True, blank=True)
    revenue_authorized = models.BooleanField(null=True, blank=True)
    reauthorization_required = models.BooleanField(default=False)
    total_duration_seconds = models.BigIntegerField(null=True, blank=True)
    public_video_count = models.PositiveIntegerField(null=True, blank=True)
    private_video_count = models.PositiveIntegerField(null=True, blank=True)
    unlisted_video_count = models.PositiveIntegerField(null=True, blank=True)
    deleted_video_count = models.PositiveIntegerField(null=True, blank=True)
    removed_from_uploads_count = models.PositiveIntegerField(null=True, blank=True)
    privacy_counts_complete = models.BooleanField(default=False)
    summed_video_views = models.BigIntegerField(null=True, blank=True)
    summed_video_likes = models.BigIntegerField(null=True, blank=True)
    summed_video_comments = models.BigIntegerField(null=True, blank=True)
    videos_last_7_days = models.PositiveIntegerField(null=True, blank=True)
    videos_last_30_days = models.PositiveIntegerField(null=True, blank=True)
    videos_last_90_days = models.PositiveIntegerField(null=True, blank=True)
    videos_last_365_days = models.PositiveIntegerField(null=True, blank=True)
    average_views = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    average_duration_seconds = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    upload_frequency_per_week = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    most_viewed_video_id = models.CharField(max_length=32, blank=True)
    latest_video_id = models.CharField(max_length=32, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(fields=["account", "channel_id"], name="unique_youtube_channel"),
        ]
        indexes = [
            models.Index(fields=["channel_id"]),
            models.Index(fields=["account", "sync_status"]),
        ]
        ordering = ["title", "channel_id"]

    def __str__(self):
        return self.title or self.channel_id


class YouTubeVideo(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = "available", "Available"
        REMOVED = "removed", "Removed from uploads"

    channel = models.ForeignKey(YouTubeChannel, on_delete=models.CASCADE, related_name="videos")
    video_id = models.CharField(max_length=32)
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    duration_iso = models.CharField(max_length=32, blank=True)
    thumbnail_urls = models.JSONField(default=dict, blank=True)
    category_id = models.CharField(max_length=16, blank=True)
    default_language = models.CharField(max_length=16, blank=True)
    tags = models.JSONField(default=list, blank=True)
    live_broadcast_status = models.CharField(max_length=32, blank=True)
    privacy_status = models.CharField(max_length=32, null=True, blank=True)
    embeddable = models.BooleanField(null=True, blank=True)
    made_for_kids = models.BooleanField(null=True, blank=True)
    url = models.TextField(blank=True)
    view_count = models.BigIntegerField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    comment_count = models.BigIntegerField(null=True, blank=True)
    favorite_count = models.BigIntegerField(null=True, blank=True)
    statistics = models.JSONField(default=dict, blank=True)
    statistics_available = models.JSONField(default=dict, blank=True)
    availability = models.CharField(
        max_length=20,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )
    comments_sync_status = models.CharField(max_length=32, blank=True)
    comments_sync_error = models.TextField(blank=True)
    comments_synced_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(fields=["channel", "video_id"], name="unique_youtube_video"),
        ]
        indexes = [
            models.Index(fields=["video_id"]),
            models.Index(fields=["channel", "published_at"]),
            models.Index(fields=["channel", "view_count"]),
        ]
        ordering = ["-published_at", "video_id"]

    def __str__(self):
        return self.title or self.video_id


class YouTubeVideoComment(models.Model):
    video = models.ForeignKey(YouTubeVideo, on_delete=models.CASCADE, related_name="comment_rows")
    comment_id = models.CharField(max_length=128, unique=True)
    parent_comment_id = models.CharField(max_length=128, blank=True)
    author_display_name = models.CharField(max_length=255, blank=True)
    author_channel_id = models.CharField(max_length=64, blank=True)
    text = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    updated_at_remote = models.DateTimeField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    reply_count = models.BigIntegerField(null=True, blank=True)
    is_reply = models.BooleanField(default=False)
    synced_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        indexes = [
            models.Index(fields=["video", "published_at"]),
            models.Index(fields=["parent_comment_id"]),
        ]
        ordering = ["-published_at", "comment_id"]

    def __str__(self):
        return self.comment_id


class YouTubeTranscript(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AVAILABLE = "available", "Available"
        UNAVAILABLE = "unavailable", "Unavailable"
        RESTRICTED = "restricted", "Restricted"
        FAILED = "failed", "Failed"

    video = models.ForeignKey(YouTubeVideo, on_delete=models.CASCADE, related_name="transcripts")
    language = models.CharField(max_length=32)
    track_id = models.CharField(max_length=128, blank=True)
    track_name = models.CharField(max_length=255, blank=True)
    caption_kind = models.CharField(max_length=32, blank=True)
    text = models.TextField(blank=True)
    segments = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["video", "language", "track_id", "source"],
                name="unique_youtube_transcript",
            ),
        ]
        indexes = [
            models.Index(fields=["video", "language"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.video.video_id} {self.language} ({self.source})"


class YouTubeSnapshot(models.Model):
    """One daily row for a channel or a video.

    scope=public_cumulative stores Data API counters.
    scope=analytics_daily stores Analytics API metrics for that day.
    scope=analytics_range stores an Analytics report over period_start..snapshot_date.
    scope=calculated_delta stores the change between cumulative snapshots.
    A blank video_key is the channel-level row.
    """

    class Scope(models.TextChoices):
        PUBLIC_CUMULATIVE = "public_cumulative", "Public cumulative"
        ANALYTICS_DAILY = "analytics_daily", "Analytics daily"
        ANALYTICS_RANGE = "analytics_range", "Analytics range"
        ANALYTICS_RETENTION = "analytics_retention", "Analytics retention"
        CALCULATED_DELTA = "calculated_delta", "Calculated snapshot delta"

    channel = models.ForeignKey(YouTubeChannel, on_delete=models.CASCADE, related_name="snapshots")
    video = models.ForeignKey(
        YouTubeVideo,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    video_key = models.CharField(max_length=32, blank=True)
    snapshot_date = models.DateField()
    scope = models.CharField(max_length=32, choices=Scope.choices)
    period_start = models.DateField(null=True, blank=True)
    views = models.BigIntegerField(null=True, blank=True)
    engaged_views = models.BigIntegerField(null=True, blank=True)
    subscribers = models.BigIntegerField(null=True, blank=True)
    likes = models.BigIntegerField(null=True, blank=True)
    comments = models.BigIntegerField(null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    impressions = models.BigIntegerField(null=True, blank=True)
    ctr = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    watch_time_minutes = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    average_view_duration_seconds = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    average_view_percentage = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    subscribers_gained = models.BigIntegerField(null=True, blank=True)
    subscribers_lost = models.BigIntegerField(null=True, blank=True)
    estimated_revenue = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    counter_reset = models.BooleanField(default=False)
    source = models.CharField(max_length=40)
    collected_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "video_key", "snapshot_date", "scope"],
                name="unique_youtube_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "snapshot_date"]),
            models.Index(fields=["video_key", "snapshot_date"]),
            models.Index(fields=["scope", "snapshot_date"]),
        ]
        ordering = ["snapshot_date", "video_key"]

    def __str__(self):
        target = self.video_key or self.channel.channel_id
        return f"{target} {self.scope} {self.snapshot_date}"


class YouTubeAnalyticsBreakdown(models.Model):
    snapshot = models.ForeignKey(YouTubeSnapshot, on_delete=models.CASCADE, related_name="breakdowns")
    dimension = models.CharField(max_length=40)
    segment = models.CharField(max_length=80)
    value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "dimension", "segment"],
                name="unique_youtube_breakdown",
            ),
        ]
        ordering = ["dimension", "-value", "segment"]

    def __str__(self):
        return f"{self.dimension}:{self.segment}"


class YouTubeSyncJob(models.Model):
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
        QUOTA_EXHAUSTED = "quota_exhausted", "Quota exhausted"
        UNAVAILABLE = "unavailable", "Data unavailable"

    account = models.ForeignKey(
        "networks.SocialAccount",
        on_delete=models.CASCADE,
        related_name="youtube_sync_jobs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="youtube_sync_requests",
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.INCREMENTAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    outcome = models.CharField(max_length=32, choices=Outcome.choices, blank=True)
    stages = models.JSONField(default=dict, blank=True)
    cursor = models.JSONField(default=dict, blank=True)
    granted_scopes = models.TextField(blank=True)
    videos_seen = models.PositiveIntegerField(default=0)
    videos_upserted = models.PositiveIntegerField(default=0)
    comments_upserted = models.PositiveIntegerField(default=0)
    api_calls = models.PositiveIntegerField(default=0)
    quota_units = models.PositiveIntegerField(default=0)
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
                name="unique_open_youtube_sync",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["account", "status"]),
        ]

    def __str__(self):
        return f"YouTube sync {self.pk} ({self.status})"
