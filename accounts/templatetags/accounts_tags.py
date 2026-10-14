from datetime import timedelta

from django import template
from django.conf import settings
from django.utils import timezone

register = template.Library()


@register.filter(name="has_perm")
def has_perm_filter(user, code):
    from accounts.permissions import has_perm

    return has_perm(user, code)


@register.simple_tag
def latest_dev_email():
    if not settings.DEBUG:
        return None
    from accounts.models import DevEmail

    cutoff = timezone.now() - timedelta(minutes=30)
    return DevEmail.objects.filter(created_at__gte=cutoff).first()
