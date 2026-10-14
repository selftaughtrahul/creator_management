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
