from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.models import (
    DEFAULT_EMPLOYEE_PERMISSIONS,
    StudioProfile,
    studio_profile,
)

STAFF_ROLES = {
    StudioProfile.Role.ADMIN,
    StudioProfile.Role.EMPLOYEE,
}


def is_admin(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = studio_profile(user)
    return bool(profile and profile.role == StudioProfile.Role.ADMIN)


def is_employee(user):
    if not user or not user.is_authenticated:
        return False
    profile = studio_profile(user)
    return bool(profile and profile.role == StudioProfile.Role.EMPLOYEE)


def is_staff_role(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = studio_profile(user)
    return bool(profile and profile.role in STAFF_ROLES)


def is_team(user):
    """Staff access: admin or employee. Kept for older call sites."""
    return is_staff_role(user)


def is_creator(user):
    if not user or not user.is_authenticated:
        return False
    profile = studio_profile(user)
    return bool(profile and profile.role == StudioProfile.Role.CREATOR)


def permission_codes(user):
    if is_admin(user):
        return list(DEFAULT_EMPLOYEE_PERMISSIONS) + ["users"]
    if not is_employee(user):
        return []
    profile = studio_profile(user)
    stored = list(profile.permissions or []) if profile else []
    return stored or list(DEFAULT_EMPLOYEE_PERMISSIONS)


def has_perm(user, code):
    if is_admin(user):
        return True
    return code in permission_codes(user)


def home_url(user):
    if is_creator(user):
        return reverse("creators:me")
    if is_staff_role(user) and has_perm(user, "studio_dashboard"):
        return reverse("campaigns:dashboard")
    return reverse("analytics:dashboard")


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_staff_role(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def team_required(view):
    return staff_required(view)


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def superuser_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def creator_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_creator(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def permission_required(*codes):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not any(has_perm(request.user, code) for code in codes):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
