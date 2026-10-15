from django.core.paginator import Paginator
from django.urls import reverse

from networks.instagram_models import InstagramProfile, InstagramSnapshot


def attached_profile(account):
    try:
        return account.instagram_profile
    except InstagramProfile.DoesNotExist:
        return None


def platform_context(account, page_number):
    profile = attached_profile(account)
    if profile is None:
        return None
    links = dict(account.contents.values_list("external_id", "pk"))
    media = profile.media.all()
    paginator = Paginator(media, 12)
    page = paginator.get_page(page_number or 1)
    for item in page.object_list:
        content_id = links.get(item.media_id)
        item.studio_url = reverse("analytics:content", args=[content_id]) if content_id else ""
    daily = list(
        profile.snapshots.filter(media_key="", scope=InstagramSnapshot.Scope.INSIGHTS_DAILY).order_by("-snapshot_date")[:14]
    )
    window = (
        profile.snapshots.filter(media_key="", scope=InstagramSnapshot.Scope.INSIGHTS_RANGE)
        .order_by("-snapshot_date")
        .first()
    )
    delta = (
        profile.snapshots.filter(media_key="", scope=InstagramSnapshot.Scope.CALCULATED_DELTA)
        .order_by("-snapshot_date")
        .first()
    )
    demographics = (
        profile.snapshots.filter(media_key="demographics")
        .prefetch_related("breakdowns")
        .order_by("-snapshot_date")
        .first()
    )
    job = account.instagram_sync_jobs.order_by("-created_at").first()
    return {
        "profile": profile,
        "page": page,
        "links": links,
        "daily": daily,
        "window": window,
        "delta": delta,
        "demographics": list(demographics.breakdowns.all()) if demographics else [],
        "job": job,
    }


def media_context(item):
    profile = attached_profile(item.account)
    if profile is None:
        return None
    media = profile.media.filter(media_id=item.external_id).first()
    if media is None:
        return None
    return {
        "media": media,
        "comments": list(media.comment_rows.all()[:30]),
        "snapshot": media.snapshots.order_by("-snapshot_date").first(),
    }
