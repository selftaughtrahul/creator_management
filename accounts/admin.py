from django.contrib import admin

from accounts.models import DevEmail


@admin.register(DevEmail)
class DevEmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "to_email", "created_at")
    search_fields = ("subject", "to_email", "body")
    readonly_fields = ("to_email", "subject", "body", "created_at")
