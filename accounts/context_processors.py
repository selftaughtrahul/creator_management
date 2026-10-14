from django.conf import settings


def app_context(request):
    from accounts.permissions import has_perm, is_admin, is_creator, is_employee, is_staff_role, permission_codes
    from networks.platforms import PLATFORMS, oauth_ready

    from accounts.models import StudioProfile

    user = getattr(request, "user", None)
    unread = 0
    user_theme = StudioProfile.Theme.TIDE
    if user is not None and getattr(user, "is_authenticated", False):
        unread = user.notifications.filter(read_at__isnull=True).count()
        try:
            studio = user.studio
        except StudioProfile.DoesNotExist:
            studio = None
        if studio and studio.theme in StudioProfile.Theme.values:
            user_theme = studio.theme
    return {
        "platform_catalog": PLATFORMS,
        "google_login_enabled": bool(
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
        ),
        "oauth_ready": {key: oauth_ready(key) for key in PLATFORMS},
        "debug_email": settings.DEBUG and not settings.EMAIL_HOST,
        "is_team_member": is_staff_role(user),
        "is_admin_member": is_admin(user),
        "is_employee_member": is_employee(user),
        "is_creator_member": is_creator(user),
        "staff_permissions": permission_codes(user) if user else [],
        "can_studio_dashboard": has_perm(user, "studio_dashboard"),
        "can_campaigns": has_perm(user, "campaigns"),
        "can_discover": has_perm(user, "discover"),
        "can_feedback": has_perm(user, "feedback"),
        "can_jobs": has_perm(user, "jobs"),
        "can_classifications": has_perm(user, "classifications"),
        "can_templates": has_perm(user, "templates"),
        "can_outreach": has_perm(user, "outreach"),
        "can_creators": has_perm(user, "creators"),
        "can_users": bool(user and getattr(user, "is_authenticated", False) and user.is_superuser),
        "unread_notifications": unread,
        "user_theme": user_theme,
    }
