from django.conf import settings
from django.db import models


class SocialAccount(models.Model):
    class Platform(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        YOUTUBE = "youtube", "YouTube"
        TWITTER = "twitter", "X"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_accounts",
    )
    platform = models.CharField(max_length=20, choices=Platform.choices)
    external_id = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255, blank=True)
    profile_url = models.TextField(blank=True)
    avatar_url = models.TextField(blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    resource_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.CharField(max_length=500, blank=True)
    is_sample = models.BooleanField(default=False)
    status_note = models.CharField(max_length=400, blank=True)
    raw_profile = models.JSONField(default=dict, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "platform"],
                name="unique_user_platform",
            )
        ]
        ordering = ["platform"]

    def __str__(self):
        return f"{self.get_platform_display()} · {self.display_name or self.username or self.user}"

    @property
    def has_token(self):
        return bool(self.access_token)


class MetricSnapshot(models.Model):
    account = models.ForeignKey(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    captured_at = models.DateTimeField()
    followers = models.PositiveIntegerField(default=0)
    following = models.PositiveIntegerField(default=0)
    content_count = models.PositiveIntegerField(default=0)
    views = models.PositiveBigIntegerField(default=0)
    likes = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)
    impressions = models.PositiveBigIntegerField(default=0)
    reach = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["captured_at"]
        indexes = [
            models.Index(fields=["account", "captured_at"]),
        ]

    def __str__(self):
        return f"{self.account.platform} @ {self.captured_at:%Y-%m-%d}"

    @property
    def reactions(self):
        return self.likes + self.comments + self.shares


class AudienceBreakdown(models.Model):
    class Dimension(models.TextChoices):
        COUNTRY = "country", "Country"
        GENDER = "gender", "Gender"

    class Basis(models.TextChoices):
        FOLLOWERS = "followers", "Followers"
        ESTIMATED = "estimated", "Estimated from viewer share"

    snapshot = models.ForeignKey(
        MetricSnapshot,
        on_delete=models.CASCADE,
        related_name="breakdowns",
    )
    dimension = models.CharField(max_length=20, choices=Dimension.choices)
    segment = models.CharField(max_length=80)
    label = models.CharField(max_length=80)
    count = models.PositiveIntegerField(default=0)
    basis = models.CharField(max_length=20, choices=Basis.choices, default=Basis.FOLLOWERS)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "dimension", "segment"],
                name="unique_snapshot_segment",
            )
        ]
        ordering = ["dimension", "-count", "label"]

    def __str__(self):
        return f"{self.get_dimension_display()} · {self.label}"


class ContentItem(models.Model):
    account = models.ForeignKey(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="contents",
    )
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=300)
    caption = models.TextField(blank=True)
    content_type = models.CharField(max_length=40, default="post")
    url = models.TextField(blank=True)
    thumbnail_url = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    views = models.PositiveBigIntegerField(default=0)
    likes = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    impressions = models.PositiveBigIntegerField(default=0)
    watch_time_seconds = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_id"],
                name="unique_content_external",
            )
        ]
        ordering = ["-published_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("analytics:content", args=[self.pk])

    @property
    def reactions(self):
        return self.likes + self.comments + self.shares + self.saves

    @property
    def engagement_rate(self):
        base = self.views or self.impressions
        if not base:
            return 0
        return round(self.reactions / base * 100, 1)
