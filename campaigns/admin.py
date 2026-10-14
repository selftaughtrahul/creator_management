from django.contrib import admin

from campaigns.models import (
    Campaign,
    CampaignCreator,
    Invitation,
    MessageTemplate,
    Offer,
    RecommendationFeedback,
)


class MembershipInline(admin.TabularInline):
    model = CampaignCreator
    extra = 0


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "budget", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "objective")
    inlines = [MembershipInline]


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
