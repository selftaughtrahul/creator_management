from django.contrib import admin

from campaigns.models import (
    Campaign,
    CampaignCreator,
    CampaignDocument,
    Invitation,
    MessageTemplate,
    Offer,
    RecommendationFeedback,
)


class MembershipInline(admin.TabularInline):
    model = CampaignCreator
    extra = 0


class DocumentInline(admin.TabularInline):
    model = CampaignDocument
    extra = 0
    fields = ("title", "file", "size", "uploaded_at")
    readonly_fields = ("size", "uploaded_at")


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "primary_market", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "client", "objective", "primary_niche", "primary_market")
    inlines = [DocumentInline, MembershipInline]


@admin.register(RecommendationFeedback)
class RecommendationFeedbackAdmin(admin.ModelAdmin):
    list_display = ("campaign", "creator", "user", "decision", "updated_at")
    list_filter = ("decision",)


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "updated_at")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("campaign", "creator", "status", "to_email", "created_at")
    list_filter = ("status",)
    search_fields = ("to_email", "subject")


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("campaign", "creator", "amount", "currency", "state", "updated_at")
    list_filter = ("state",)
