from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from accounts.models import StudioProfile, studio_profile


def is_team(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = studio_profile(user)
    return bool(profile and profile.role == StudioProfile.Role.TEAM)


def is_creator(user):
    if not user or not user.is_authenticated:
        return False
    profile = studio_profile(user)
    return bool(profile and profile.role == StudioProfile.Role.CREATOR)


def team_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_team(request.user):
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
