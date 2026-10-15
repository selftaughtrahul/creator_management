from django.conf import settings
from django.db import models
from django.db.models import Q


class Category(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True)
    keywords = models.CharField(max_length=400, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def keyword_list(self):
        return [part.strip().lower() for part in self.keywords.split(",") if part.strip()]


class Subcategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    name = models.CharField(max_length=80)
    slug = models.SlugField()
    keywords = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["category__name", "name"]
        verbose_name_plural = "subcategories"
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="unique_category_subcategory"),
        ]

    def __str__(self):
        return f"{self.category.name} / {self.name}"

    def keyword_list(self):
        return [part.strip().lower() for part in self.keywords.split(",") if part.strip()]


class CreatorProfile(models.Model):
    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        PROFILE_COMPLETE = "profile_complete", "Profile complete"
        CONNECTED = "connected", "Accounts connected"
        ONBOARDING = "onboarding", "Onboarding"
        READY = "ready", "Ready"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="creator_profile",
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    audience_notes = models.TextField(blank=True)
    niches = models.ManyToManyField(Subcategory, blank=True, related_name="creators")
    rate_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_currency = models.CharField(max_length=8, default="INR")
    share_metrics = models.BooleanField(default=True)
    discoverable = models.BooleanField(default=False)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.REGISTERED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name or self.user.email

    def refresh_status(self, save=True):
        from campaigns.models import CampaignCreator, Offer

        assigned = CampaignCreator.objects.filter(
            creator=self,
            state=CampaignCreator.State.ASSIGNED,
        ).exists()
        offer_accepted = Offer.objects.filter(creator=self, state=Offer.State.ACCEPTED).exists()
        connected = self.user.social_accounts.exclude(external_id="").exists()
        complete = bool(self.display_name and self.bio and self.location)
        if offer_accepted:
            status = self.Status.READY
        elif assigned:
            status = self.Status.ONBOARDING
        elif connected:
            status = self.Status.CONNECTED
        elif complete:
            status = self.Status.PROFILE_COMPLETE
        else:
            status = self.Status.REGISTERED
        if self.status != status:
            self.status = status
            if save:
                self.save(update_fields=["status", "updated_at"])
        return self.status


class Classification(models.Model):
    class Method(models.TextChoices):
        RULES = "rules", "Rules"
        ANTHROPIC = "anthropic", "Claude"
        MANUAL = "manual", "Manual"

    class Provenance(models.TextChoices):
        VERIFIED = "verified", "Verified"
        AI_ASSESSMENT = "ai_assessment", "AI assessment"
        ESTIMATE = "estimate", "Estimate"
        UNAVAILABLE = "unavailable", "Unavailable"

    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="classifications",
    )
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    subcategory = models.ForeignKey(Subcategory, on_delete=models.SET_NULL, null=True, blank=True)
    method = models.CharField(max_length=20, choices=Method.choices)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    provenance = models.CharField(max_length=20, choices=Provenance.choices)
    needs_review = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classifications_made",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.category.name if self.category else "unclassified"
        return f"{self.creator} · {label}"


class ClassificationLabel(models.Model):
    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="labels",
    )
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    subcategory = models.ForeignKey(Subcategory, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    labeled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classification_labels",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["creator"], name="unique_creator_label"),
        ]

    def __str__(self):
        return f"{self.creator} labeled {self.category}"


class EnrichmentJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"

    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    platform = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_jobs",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["creator", "platform"],
                condition=Q(status__in=["pending", "running"]),
                name="unique_open_enrichment_job",
            )
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.creator} · {self.platform} · {self.status}"


class IABCategory(models.Model):
    """One official Content Taxonomy row. IDs are imported, never minted here."""

    unique_id = models.CharField(max_length=32)
    parent_id = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=255)
    tier_1 = models.CharField(max_length=255, blank=True)
    tier_2 = models.CharField(max_length=255, blank=True)
    tier_3 = models.CharField(max_length=255, blank=True)
    tier_4 = models.CharField(max_length=255, blank=True)
    taxonomy_version = models.CharField(max_length=16, default="3.1")

    class Meta:
        ordering = ["taxonomy_version", "name"]
        verbose_name = "IAB category"
        verbose_name_plural = "IAB categories"
        constraints = [
            models.UniqueConstraint(
                fields=["taxonomy_version", "unique_id"],
                name="unique_iab_category_version",
            ),
        ]
        indexes = [
            models.Index(fields=["taxonomy_version", "parent_id"]),
        ]

    def __str__(self):
        return f"{self.unique_id} {self.name}"

    @property
    def depth(self):
        return sum(1 for tier in (self.tier_1, self.tier_2, self.tier_3, self.tier_4) if tier)


class IABMapping(models.Model):
    """Awisee niche path for one official category. Several rows may share one IAB id."""

    iab_category = models.ForeignKey(IABCategory, on_delete=models.CASCADE, related_name="mappings")
    custom_niche = models.CharField(max_length=80)
    custom_sub_niche = models.CharField(max_length=80, blank=True)
    custom_micro_niche = models.CharField(max_length=120, blank=True)
    keywords = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["custom_niche", "custom_sub_niche", "custom_micro_niche"]
        constraints = [
            models.UniqueConstraint(
                fields=["iab_category", "custom_niche", "custom_sub_niche", "custom_micro_niche"],
                name="unique_iab_custom_mapping",
            ),
        ]

    def __str__(self):
        parts = [self.custom_niche, self.custom_sub_niche, self.custom_micro_niche]
        return f"{self.iab_category.unique_id} → {' / '.join(part for part in parts if part)}"

    def keyword_list(self):
        return [part.strip().lower() for part in self.keywords.split(",") if part.strip()]


class IABClassification(models.Model):
    class Mode(models.TextChoices):
        PROFILE = "profile", "Profile only"
        CONTENT = "content", "Posts and transcripts"
        RETRIEVED = "retrieved", "Retrieved content"

    class Method(models.TextChoices):
        RULES = "rules", "Rules"
        ANTHROPIC = "anthropic", "Claude"

    creator = models.ForeignKey(CreatorProfile, on_delete=models.CASCADE, related_name="iab_labels")
    iab_category = models.ForeignKey(IABCategory, on_delete=models.PROTECT, related_name="classifications")
    taxonomy_version = models.CharField(max_length=16)
    custom_niche = models.CharField(max_length=80, blank=True)
    custom_sub_niche = models.CharField(max_length=80, blank=True)
    custom_micro_niche = models.CharField(max_length=120, blank=True)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    method = models.CharField(max_length=20, choices=Method.choices)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    needs_review = models.BooleanField(default=True)
    explanation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="iab_classifications_made",
    )

    class Meta:
        ordering = ["-created_at", "custom_niche"]

    def __str__(self):
        return f"{self.creator} · {self.iab_category.unique_id} {self.iab_category.name}"


class IABTaxonomyImport(models.Model):
    taxonomy_version = models.CharField(max_length=16)
    row_count = models.PositiveIntegerField()
    source_name = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self):
        return f"Content Taxonomy {self.taxonomy_version} · {self.row_count} rows"


class CreatorDocument(models.Model):
    creator = models.ForeignKey(
        CreatorProfile,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    title = models.CharField(max_length=150)
    file = models.FileField(upload_to="creator_documents/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title
