from django.conf import settings
from django.db import models

from creators.models import Category, CreatorProfile, Subcategory
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
    niche = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    subcategory = models.ForeignKey(Subcategory, on_delete=models.SET_NULL, null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    platforms = models.JSONField(default=list, blank=True)
    follower_min = models.PositiveIntegerField(null=True, blank=True)
    follower_max = models.PositiveIntegerField(null=True, blank=True)
    min_engagement = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    audience_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

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
