from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Awisee"
admin.site.site_title = "Awisee"
admin.site.index_title = "Content analytics"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("accounts.urls")),
    path("", include("networks.urls")),
    path("", include("analytics.urls")),
]
