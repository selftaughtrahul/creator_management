from django.conf import settings


def app_context(request):
    from accounts.permissions import is_creator, is_team
    from networks.platforms import PLATFORMS, oauth_ready

    user = getattr(request, "user", None)
    return {
        "platform_catalog": PLATFORMS,
        "google_login_enabled": bool(
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
        ),
        "oauth_ready": {key: oauth_ready(key) for key in PLATFORMS},
        "debug_email": settings.DEBUG and not settings.EMAIL_HOST,
        "is_team_member": is_team(user),
        "is_creator_member": is_creator(user),
    }
