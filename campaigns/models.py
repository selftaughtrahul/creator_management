from django.conf import settings
from django.db import models

from creators.models import CreatorProfile
from networks.models import SocialAccount


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    name = models.CharField(max_length=160)
    objective = models.TextField(blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    platforms = models.JSONField(default=list, blank=True)
    follower_min = models.PositiveIntegerField(null=True, blank=True)
    follower_max = models.PositiveIntegerField(null=True, blank=True)
    min_engagement = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    audience_notes = models.TextField("Notes", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    client = models.CharField(max_length=160, blank=True)
    scout_deadline = models.DateField("Deadline to scout the profiles", null=True, blank=True)
    primary_market = models.CharField(max_length=255, blank=True)
    additional_markets = models.CharField(max_length=255, blank=True)
    creators_needed = models.CharField("# Creators needed", max_length=160, blank=True)
    primary_niche = models.TextField("Primary niche / vertical", blank=True)
    secondary_niche = models.TextField("Secondary niche / sub niche", blank=True)
    content_format = models.TextField(blank=True)
    gender = models.CharField(max_length=80, blank=True)
    audience_location = models.CharField(max_length=255, blank=True)
    audience_language = models.CharField(max_length=160, blank=True)
    min_avg_views = models.CharField("Min avg views", max_length=80, blank=True)
    view_consistency = models.CharField("Min view consistency", max_length=80, blank=True)
    max_rate_usd = models.DecimalField(
        "Max rate per deliverable (USD)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    delivery_deadline = models.DateField(null=True, blank=True)
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    def niche_label(self):
        text = (self.primary_niche or "").strip()
        if not text:
            return ""
        return text.splitlines()[0][:80]

    def requirement_rows(self):
        """Intake fields from the campaign-requirements sheet, skipping blanks."""
        if not (self.client or self.primary_market or self.primary_niche):
            return []
        rows = []

        def add(label, value):
            if value is None or value == "":
                return
            rows.append((label, value))

        add("Client", self.client)
        add("Deadline to scout", self.scout_deadline)
        add("Primary market", self.primary_market)
        add("Additional markets", self.additional_markets)
        add("Creators needed", self.creators_needed)
        add("Platforms", ", ".join(self.platform_labels()) or None)
        add("Primary niche", self.primary_niche)
        add("Secondary niche", self.secondary_niche)
        add("Content format", self.content_format)
        add("Gender", self.gender)
        add("Audience location", self.audience_location)
        add("Audience language", self.audience_language)
        add("Min followers", self.follower_min)
        add("Max followers", self.follower_max)
        add("Min avg views", self.min_avg_views)
        add("Min engagement", f"{self.min_engagement}%" if self.min_engagement is not None else None)
        add("View consistency", self.view_consistency)
        add("Max rate (USD)", self.max_rate_usd)
        add("Delivery deadline", self.delivery_deadline)
        add("Notes", self.audience_notes)
        add("Brief", self.objective)
        add("Comments", self.comments)
        return rows

    def platform_labels(self):
        labels = []
        for key in self.platforms or []:
            try:
                labels.append(SocialAccount.Platform(key).label)
            except ValueError:
                labels.append(key)
        return labels

    def recorded_spend(self):
        from django.db.models import Sum

        total = self.memberships.aggregate(total=Sum("recorded_cost")).get("total")
        return total

    def remaining_budget(self):
        if self.budget is None:
            return None
        spend = self.recorded_spend()
        if spend is None:
            return self.budget
        return self.budget - spend


class CampaignDocument(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=180)
    file = models.FileField(upload_to="campaign_documents/%Y/%m/")
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title


class CampaignCreator(models.Model):
    class State(models.TextChoices):
        SHORTLISTED = "shortlisted", "Shortlisted"
        ASSIGNED = "assigned", "Assigned"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="memberships")
    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="campaign_memberships",
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.SHORTLISTED)
    recorded_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_additions",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "creator"], name="unique_campaign_creator"),
        ]

    def __str__(self):
        return f"{self.campaign} · {self.creator}"


class RecommendationFeedback(models.Model):
    class Decision(models.TextChoices):
        YES = "yes", "Yes — suitable"
        NO = "no", "No — unsuitable"
        NEEDS_REVIEW = "needs_review", "Needs review"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="feedback")
    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="recommendation_feedback",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recommendation_feedback",
    )
    decision = models.CharField(max_length=20, choices=Decision.choices)
    comment = models.TextField(blank=True)
    classification_snapshot = models.JSONField(default=dict, blank=True)
    recommendation_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "creator", "user"],
                name="unique_feedback_per_reviewer",
            )
        ]

    def __str__(self):
        return f"{self.campaign} · {self.creator} · {self.decision}"


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def render(self, *, campaign, creator):
        mapping = {
            "creator_name": creator.display_name,
            "campaign_name": campaign.name,
            "location": creator.location or campaign.location,
        }
        subject = self.subject
        body = self.body
        for key, value in mapping.items():
            subject = subject.replace("{" + key + "}", value or "")
            body = body.replace("{" + key + "}", value or "")
        return subject, body


class Invitation(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        STORED_LOCALLY = "stored_locally", "Stored locally"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        RESPONDED = "responded", "Responded"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="invitations")
    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations",
    )
    to_email = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    follow_up_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    outcome_at = models.DateTimeField(null=True, blank=True)
    outcome_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.campaign} → {self.creator} ({self.status})"


class Offer(models.Model):
    class State(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        COUNTERED = "countered", "Countered"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="offers")
    creator = models.ForeignKey(CreatorProfile, on_delete=models.CASCADE, related_name="offers")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="INR")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PROPOSED)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offers_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offers_decided",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.campaign} · {self.creator} · {self.amount} {self.currency}"
