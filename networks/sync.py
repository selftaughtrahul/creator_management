from datetime import datetime, time, timedelta

from django.utils import timezone

from networks.demo import tick_sample
from networks.fetchers import fetch_account
from networks.models import AudienceBreakdown, ContentItem, MetricSnapshot


def sync_account(account):
    if account.is_sample:
        return tick_sample(account)
    result = fetch_account(account)
    if account.external_id and result.profile.external_id and account.external_id != result.profile.external_id:
        account.snapshots.all().delete()
        account.contents.all().delete()
    account.external_id = result.profile.external_id
    account.display_name = result.profile.display_name
    account.username = result.profile.username
    account.profile_url = result.profile.profile_url
    account.avatar_url = result.profile.avatar_url
    account.raw_profile = result.profile.raw
    account.status_note = result.note
    account.last_error = ""
    account.last_synced_at = timezone.now()
    if result.resource_token:
        account.resource_token = result.resource_token
    account.save()
    for snapshot in result.snapshots:
        saved = _upsert_snapshot(account, snapshot)
        _replace_breakdowns(saved, snapshot)
    for item in result.contents:
        if not item.external_id:
            continue
        ContentItem.objects.update_or_create(
            account=account,
            external_id=item.external_id,
            defaults={
                "title": item.title or "Untitled",
                "caption": item.caption,
                "content_type": item.content_type,
                "url": item.url,
                "thumbnail_url": item.thumbnail_url,
                "published_at": item.published_at,
                "views": item.views,
                "likes": item.likes,
                "comments": item.comments,
                "shares": item.shares,
                "saves": item.saves,
                "impressions": item.impressions,
                "watch_time_seconds": item.watch_time_seconds,
            },
        )
    return account


def _upsert_snapshot(account, snapshot):
    local_day = timezone.localtime(snapshot.captured_at).date()
    start = timezone.make_aware(datetime.combine(local_day, time.min))
    end = start + timedelta(days=1)
    existing = account.snapshots.filter(captured_at__gte=start, captured_at__lt=end).order_by("-captured_at").first()
    fields = {
        "captured_at": snapshot.captured_at,
        "followers": snapshot.followers,
        "following": snapshot.following,
        "content_count": snapshot.content_count,
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "shares": snapshot.shares,
        "impressions": snapshot.impressions,
        "reach": snapshot.reach,
    }
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.save()
        return existing
    return MetricSnapshot.objects.create(account=account, **fields)


def _replace_breakdowns(saved, snapshot):
    slices = list(snapshot.breakdowns or [])
    if not slices:
        return
    saved.breakdowns.all().delete()
    AudienceBreakdown.objects.bulk_create(
        [
            AudienceBreakdown(
                snapshot=saved,
                dimension=item.dimension,
                segment=item.segment,
                label=item.label,
                count=item.count,
                basis=item.basis,
            )
            for item in slices
            if item.count
        ]
    )
