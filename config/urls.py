from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts.views import creator_login, signup_closed

admin.site.site_header = "Awisee"
admin.site.site_title = "Awisee"
admin.site.index_title = "Content analytics"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", creator_login, name="account_login"),
    path("accounts/signup/", signup_closed, name="account_signup"),
    path("accounts/", include("allauth.urls")),
    path("", include("accounts.urls")),
    path("", include("networks.urls")),
    path("", include("analytics.urls")),
    path("", include("creators.urls")),
    path("", include("campaigns.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
