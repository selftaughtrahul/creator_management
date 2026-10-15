from django.contrib import admin

from creators.models import (
    Category,
    Classification,
    ClassificationLabel,
    CreatorDocument,
    CreatorProfile,
    EnrichmentJob,
    IABCategory,
    IABClassification,
    IABMapping,
    IABTaxonomyImport,
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


@admin.register(IABCategory)
class IABCategoryAdmin(admin.ModelAdmin):
    list_display = ("unique_id", "name", "parent_id", "tier_1", "tier_2", "taxonomy_version")
    search_fields = ("unique_id", "name", "tier_1", "tier_2")
    list_filter = ("taxonomy_version",)


@admin.register(IABMapping)
class IABMappingAdmin(admin.ModelAdmin):
    list_display = ("iab_category", "custom_niche", "custom_sub_niche", "custom_micro_niche")
    search_fields = ("custom_niche", "custom_sub_niche", "custom_micro_niche", "iab_category__unique_id")


@admin.register(IABClassification)
class IABClassificationAdmin(admin.ModelAdmin):
    list_display = ("creator", "iab_category", "custom_niche", "custom_micro_niche", "mode", "needs_review", "created_at")
    list_filter = ("mode", "method", "needs_review")
    search_fields = ("creator__display_name", "iab_category__unique_id", "custom_niche")


@admin.register(IABTaxonomyImport)
class IABTaxonomyImportAdmin(admin.ModelAdmin):
    list_display = ("taxonomy_version", "row_count", "source_name", "imported_at")
