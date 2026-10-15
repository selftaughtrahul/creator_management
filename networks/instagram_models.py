from django.conf import settings
from django.db import models
from django.db.models import Q


class InstagramProfile(models.Model):
    """Professional Instagram account behind one SocialAccount connection.

    Tokens stay on SocialAccount. Instagram Login returns one user, so this
    profile is one-to-one with that connection.
    """

    class SyncStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    account = models.OneToOneField(
        "networks.SocialAccount",
        on_delete=models.CASCADE,
        related_name="instagram_profile",
    )
    ig_user_id = models.CharField(max_length=64)
    username = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    biography = models.TextField(blank=True)
    website = models.TextField(blank=True)
    account_type = models.CharField(max_length=32, blank=True)
    profile_picture_url = models.TextField(blank=True)
    followers_count = models.BigIntegerField(null=True, blank=True)
    follows_count = models.BigIntegerField(null=True, blank=True)
    media_count = models.BigIntegerField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.IDLE)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    granted_scopes = models.TextField(blank=True)
    insights_authorized = models.BooleanField(null=True, blank=True)
    comments_authorized = models.BooleanField(null=True, blank=True)
    reauthorization_required = models.BooleanField(default=False)
    summed_likes = models.BigIntegerField(null=True, blank=True)
    summed_comments = models.BigIntegerField(null=True, blank=True)
    summed_views = models.BigIntegerField(null=True, blank=True)
    summed_reach = models.BigIntegerField(null=True, blank=True)
    hidden_like_count = models.PositiveIntegerField(null=True, blank=True)
    media_last_7_days = models.PositiveIntegerField(null=True, blank=True)
    media_last_30_days = models.PositiveIntegerField(null=True, blank=True)
    media_last_90_days = models.PositiveIntegerField(null=True, blank=True)
    media_last_365_days = models.PositiveIntegerField(null=True, blank=True)
    average_likes = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    most_liked_media_id = models.CharField(max_length=64, blank=True)
    latest_media_id = models.CharField(max_length=64, blank=True)

    class Meta:
        app_label = "networks"
        indexes = [models.Index(fields=["ig_user_id"])]

    def __str__(self):
        return self.username or self.ig_user_id


class InstagramMedia(models.Model):
    profile = models.ForeignKey(InstagramProfile, on_delete=models.CASCADE, related_name="media")
    media_id = models.CharField(max_length=64)
    caption = models.TextField(blank=True)
    media_type = models.CharField(max_length=32, blank=True)
    media_product_type = models.CharField(max_length=32, blank=True)
    permalink = models.TextField(blank=True)
    thumbnail_url = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    comments_count = models.BigIntegerField(null=True, blank=True)
    is_comment_enabled = models.BooleanField(null=True, blank=True)
    views = models.BigIntegerField(null=True, blank=True)
    reach = models.BigIntegerField(null=True, blank=True)
    saved = models.BigIntegerField(null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    total_interactions = models.BigIntegerField(null=True, blank=True)
    replies = models.BigIntegerField(null=True, blank=True)
    avg_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    total_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    insights_available = models.JSONField(default=dict, blank=True)
    comments_sync_status = models.CharField(max_length=32, blank=True)
    comments_sync_error = models.TextField(blank=True)
    comments_synced_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(fields=["profile", "media_id"], name="unique_instagram_media"),
        ]
        indexes = [
            models.Index(fields=["media_id"]),
            models.Index(fields=["profile", "published_at"]),
        ]
        ordering = ["-published_at", "media_id"]

    def __str__(self):
        return self.media_id


class InstagramComment(models.Model):
    media = models.ForeignKey(InstagramMedia, on_delete=models.CASCADE, related_name="comment_rows")
    comment_id = models.CharField(max_length=64, unique=True)
    parent_comment_id = models.CharField(max_length=64, blank=True)
    username = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    is_reply = models.BooleanField(default=False)
    synced_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        indexes = [
            models.Index(fields=["media", "published_at"]),
            models.Index(fields=["parent_comment_id"]),
        ]
        ordering = ["-published_at", "comment_id"]

    def __str__(self):
        return self.comment_id


class InstagramSnapshot(models.Model):
    """Daily facts for the profile or one media item.

    public_cumulative stores lifetime counters from the user or media node.
    insights_daily stores one day from the Insights API. follower_count on
    that API is new followers that day, stored as followers_gained.
    insights_range stores a total_value window.
    calculated_delta stores the change between public follower totals.
    A blank media_key is the profile-level row.
    """

    class Scope(models.TextChoices):
        PUBLIC_CUMULATIVE = "public_cumulative", "Public cumulative"
        INSIGHTS_DAILY = "insights_daily", "Insights daily"
        INSIGHTS_RANGE = "insights_range", "Insights range"
        CALCULATED_DELTA = "calculated_delta", "Calculated snapshot delta"

    profile = models.ForeignKey(InstagramProfile, on_delete=models.CASCADE, related_name="snapshots")
    media = models.ForeignKey(
        InstagramMedia,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    media_key = models.CharField(max_length=64, blank=True)
    snapshot_date = models.DateField()
    scope = models.CharField(max_length=32, choices=Scope.choices)
    period_start = models.DateField(null=True, blank=True)
    followers = models.BigIntegerField(null=True, blank=True)
    follows = models.BigIntegerField(null=True, blank=True)
    media_count = models.BigIntegerField(null=True, blank=True)
    reach = models.BigIntegerField(null=True, blank=True)
    views = models.BigIntegerField(null=True, blank=True)
    likes = models.BigIntegerField(null=True, blank=True)
    comments = models.BigIntegerField(null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    saves = models.BigIntegerField(null=True, blank=True)
    replies = models.BigIntegerField(null=True, blank=True)
    accounts_engaged = models.BigIntegerField(null=True, blank=True)
    total_interactions = models.BigIntegerField(null=True, blank=True)
    followers_gained = models.BigIntegerField(null=True, blank=True)
    avg_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    total_watch_time_ms = models.BigIntegerField(null=True, blank=True)
    counter_reset = models.BooleanField(default=False)
    source = models.CharField(max_length=40)
    collected_at = models.DateTimeField()

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "media_key", "snapshot_date", "scope"],
                name="unique_instagram_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["profile", "snapshot_date"]),
            models.Index(fields=["scope", "snapshot_date"]),
        ]
        ordering = ["snapshot_date", "media_key"]

    def __str__(self):
        target = self.media_key or self.profile.ig_user_id
        return f"{target} {self.scope} {self.snapshot_date}"


class InstagramInsightBreakdown(models.Model):
    snapshot = models.ForeignKey(InstagramSnapshot, on_delete=models.CASCADE, related_name="breakdowns")
    dimension = models.CharField(max_length=40)
    segment = models.CharField(max_length=80)
    value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        app_label = "networks"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "dimension", "segment"],
                name="unique_instagram_breakdown",
            ),
        ]
        ordering = ["dimension", "-value", "segment"]

    def __str__(self):
        return f"{self.dimension}:{self.segment}"


class InstagramSyncJob(models.Model):
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
        related_name="instagram_sync_jobs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instagram_sync_requests",
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.INCREMENTAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    outcome = models.CharField(max_length=32, choices=Outcome.choices, blank=True)
    stages = models.JSONField(default=dict, blank=True)
    granted_scopes = models.TextField(blank=True)
    media_seen = models.PositiveIntegerField(default=0)
    media_upserted = models.PositiveIntegerField(default=0)
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
                name="unique_open_instagram_sync",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["account", "status"]),
        ]

    def __str__(self):
        return f"Instagram sync {self.pk} ({self.status})"
