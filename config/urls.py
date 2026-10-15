from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path

from accounts.views import PasswordResetFromKeyToLoginView, creator_login, signup_closed

admin.site.site_header = "Awisee"
admin.site.site_title = "Awisee"
admin.site.index_title = "Content analytics"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", creator_login, name="account_login"),
    path("accounts/signup/", signup_closed, name="account_signup"),
    re_path(
        r"^accounts/password/reset/key/(?P<uidb36>[0-9A-Za-z]+)-(?P<key>.+)/$",
        PasswordResetFromKeyToLoginView.as_view(),
        name="account_reset_password_from_key",
    ),
    path("accounts/", include("allauth.urls")),
    path("", include("accounts.urls")),
    path("", include("networks.urls")),
    path("", include("analytics.urls")),
    path("", include("creators.urls")),
    path("", include("campaigns.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
