from django.contrib import admin

from creators.models import (
    Category,
    Classification,
    ClassificationLabel,
    CreatorDocument,
    CreatorProfile,
    EnrichmentJob,
    Subcategory,
)


class SubcategoryInline(admin.TabularInline):
    model = Subcategory
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [SubcategoryInline]


@admin.register(CreatorProfile)
class CreatorProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "location", "status", "discoverable")
    list_filter = ("status", "discoverable", "share_metrics")
    search_fields = ("display_name", "user__email", "location")
    filter_horizontal = ("niches",)


@admin.register(Classification)
class ClassificationAdmin(admin.ModelAdmin):
    list_display = ("creator", "category", "method", "provenance", "needs_review", "created_at")
    list_filter = ("method", "provenance", "needs_review")
    search_fields = ("creator__display_name", "explanation")


@admin.register(ClassificationLabel)
class ClassificationLabelAdmin(admin.ModelAdmin):
    list_display = ("creator", "category", "labeled_by", "created_at")


@admin.register(EnrichmentJob)
class EnrichmentJobAdmin(admin.ModelAdmin):
    list_display = ("creator", "platform", "status", "attempts", "created_at")
    list_filter = ("status", "platform")


@admin.register(CreatorDocument)
class CreatorDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "creator", "uploaded_at")
