from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from networks.models import ContentItem, MetricSnapshot


STALE_AFTER = timedelta(days=30)


def creator_metrics(creator):
    accounts = list(creator.user.social_accounts.all())
    snapshots = []
    platforms = []
    followers = None
    views = None
    latest_sync = None
    sample = False
    for account in accounts:
        platforms.append(account.platform)
        sample = sample or account.is_sample
        latest = account.snapshots.order_by("-captured_at").first()
        if latest:
            snapshots.append({"account": account, "snapshot": latest})
            followers = (followers or 0) + latest.followers
            views = (views or 0) + latest.views
        if account.last_synced_at and (latest_sync is None or account.last_synced_at > latest_sync):
            latest_sync = account.last_synced_at

    engagement = _engagement(creator)
    classification = creator.classifications.select_related("category", "subcategory").first()
    missing = []
    if not creator.location:
        missing.append("location")
    if not creator.niches.exists() and not (classification and classification.category):
        missing.append("niche")
    if followers is None:
        missing.append("followers")
    if engagement["value"] is None:
        missing.append("engagement")
    if not accounts:
        missing.append("connected_account")
    stale = bool(latest_sync and timezone.now() - latest_sync > STALE_AFTER)
    if stale:
        missing.append("fresh_snapshot")

    follower_source = "unavailable"
    if snapshots:
        dates = ", ".join(
            timezone.localtime(row["snapshot"].captured_at).strftime("%Y-%m-%d")
            + f" {row['account'].platform}"
            for row in snapshots
        )
        follower_source = f"MetricSnapshot {dates}"
        if sample:
            follower_source += " (sample)"

    niche = None
    niche_source = "unavailable"
    niche_provenance = "unavailable"
    if classification and classification.category:
        niche = classification.subcategory.name if classification.subcategory else classification.category.name
        niche_source = f"Classification ({classification.get_method_display()})"
        niche_provenance = classification.provenance
    elif creator.niches.exists():
        niche = ", ".join(item.name for item in creator.niches.all())
        niche_source = "CreatorProfile.niches"
        niche_provenance = "verified"

    return {
        "creator": creator,
        "platforms": platforms,
        "sample": sample,
        "followers": {
            "value": followers,
            "source": follower_source,
            "provenance": "unavailable" if followers is None else ("estimate" if sample else "verified"),
        },
        "views": {
            "value": views,
            "source": follower_source if views is not None else "unavailable",
            "provenance": "unavailable" if views is None else ("estimate" if sample else "verified"),
        },
        "engagement": engagement,
        "location": {
            "value": creator.location or None,
            "source": "CreatorProfile" if creator.location else "unavailable",
            "provenance": "verified" if creator.location else "unavailable",
        },
        "niche": {
            "value": niche,
            "source": niche_source,
            "provenance": niche_provenance,
        },
        "classification": classification,
        "last_synced_at": latest_sync,
        "stale": stale,
        "missing": missing,
        "discoverable": creator.discoverable and creator.share_metrics,
        "accounts": accounts,
    }


def _engagement(creator):
    totals = ContentItem.objects.filter(account__user=creator.user).aggregate(
        views=Sum("views"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        shares=Sum("shares"),
        saves=Sum("saves"),
        impressions=Sum("impressions"),
    )
    reactions = sum(totals.get(key) or 0 for key in ("likes", "comments", "shares", "saves"))
    base = totals.get("views") or totals.get("impressions") or 0
    if not base:
        return {
            "value": None,
            "source": "unavailable",
            "provenance": "unavailable",
            "reactions": reactions,
            "base": 0,
        }
    rate = round(reactions / base * 100, 1)
    return {
        "value": rate,
        "source": "ContentItem aggregate (reactions / views or impressions)",
        "provenance": "verified",
        "reactions": reactions,
        "base": base,
    }


def growth_summary(creator):
    """30-day growth and averages from stored snapshots and posts. Missing history stays empty."""
    cutoff = timezone.now() - timedelta(days=30)
    followers = views = None
    follower_base = views_base = None
    posts = []
    for account in creator.user.social_accounts.all():
        snapshots = sorted(account.snapshots.all(), key=lambda item: item.captured_at)
        if snapshots:
            latest = snapshots[-1]
            followers = (followers or 0) + latest.followers
            views = (views or 0) + latest.views
            baseline = None
            target = timezone.localtime(latest.captured_at) - timedelta(days=30)
            for snapshot in snapshots:
                if snapshot.pk == latest.pk:
                    break
                if timezone.localtime(snapshot.captured_at) <= target:
                    baseline = snapshot
            if baseline is None and len(snapshots) > 1:
                baseline = snapshots[0]
            if baseline is not None and baseline.pk != latest.pk:
                follower_base = (follower_base or 0) + baseline.followers
                views_base = (views_base or 0) + baseline.views
        for item in account.contents.all():
            if item.published_at and item.published_at >= cutoff:
                posts.append(item)
    reactions = sum(item.reactions for item in posts)
    post_views = sum(item.views or item.impressions or 0 for item in posts)
    count = len(posts)
    return {
        "followers": followers,
        "follower_growth": _delta(followers, follower_base),
        "views": views,
        "views_growth": _delta(views, views_base),
        "engagement": round(reactions / post_views * 100, 1) if post_views else None,
        "avg_engagement": round(reactions / count) if count else None,
        "avg_views": round(post_views / count) if count else None,
        "posts": count,
    }


def _delta(latest, baseline):
    if latest is None or baseline is None:
        return None
    change = latest - baseline
    percent = round(change / baseline * 100, 1) if baseline else 0
    return {"absolute": change, "percent": percent}


def account_metrics(creator):
    """Latest stored numbers for each connected account. Missing snapshots stay unavailable."""
    rows = []
    for account in creator.user.social_accounts.all():
        snapshots = list(account.snapshots.all())
        latest = max(snapshots, key=lambda item: item.captured_at) if snapshots else None
        contents = list(account.contents.all())
        content_totals = {
            "posts": len(contents),
            "views": sum(item.views for item in contents),
            "likes": sum(item.likes for item in contents),
            "comments": sum(item.comments for item in contents),
            "shares": sum(item.shares for item in contents),
            "saves": sum(item.saves for item in contents),
            "impressions": sum(item.impressions for item in contents),
            "watch_time_seconds": sum(item.watch_time_seconds for item in contents),
        }
        breakdowns = list(latest.breakdowns.all()) if latest else []
        rows.append(
            {
                "account": account,
                "snapshot": latest,
                "breakdowns": breakdowns,
                "content": content_totals,
            }
        )
    return rows


def latest_snapshot_map(creators):
    user_ids = [creator.user_id for creator in creators]
    rows = (
        MetricSnapshot.objects.filter(account__user_id__in=user_ids)
        .select_related("account")
        .order_by("account_id", "-captured_at")
    )
    latest = {}
    seen = set()
    for snapshot in rows:
        if snapshot.account_id in seen:
            continue
        seen.add(snapshot.account_id)
        latest.setdefault(snapshot.account.user_id, []).append(snapshot)
    return latest
