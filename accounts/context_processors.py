from django.conf import settings


def app_context(request):
    from networks.platforms import PLATFORMS, oauth_ready

    return {
        "platform_catalog": PLATFORMS,
        "google_login_enabled": bool(
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
        ),
        "oauth_ready": {key: oauth_ready(key) for key in PLATFORMS},
        "debug_email": settings.DEBUG and not settings.EMAIL_HOST,
    }
