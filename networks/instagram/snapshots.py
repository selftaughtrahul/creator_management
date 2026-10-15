from decimal import Decimal

from django.utils import timezone

from networks.instagram_models import InstagramInsightBreakdown, InstagramSnapshot

VALUE_FIELDS = (
    "followers",
    "follows",
    "media_count",
    "reach",
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "replies",
    "accounts_engaged",
    "total_interactions",
    "followers_gained",
    "avg_watch_time_ms",
    "total_watch_time_ms",
    "counter_reset",
    "period_start",
    "source",
    "media",
)


def save_snapshot(profile, *, media=None, media_key="", snapshot_date, scope, source, values, period_start=None):
    """Write one snapshot. Past dates keep existing numbers and only fill blanks."""
    today = timezone.localdate()
    lookup = {
        "profile": profile,
        "media_key": media_key or "",
        "snapshot_date": snapshot_date,
        "scope": scope,
    }
    existing = InstagramSnapshot.objects.filter(**lookup).first()
    payload = {key: values[key] for key in VALUE_FIELDS if key in values}
    payload["source"] = source
    payload["media"] = media
    if period_start is not None:
        payload["period_start"] = period_start
    if existing and snapshot_date < today:
        changed = []
        for key, value in payload.items():
            if key in {"source", "media"}:
                continue
            if value is None:
                continue
            if getattr(existing, key) in (None, "") and value not in (None, ""):
                setattr(existing, key, value)
                changed.append(key)
        if changed:
            existing.collected_at = timezone.now()
            existing.save(update_fields=changed + ["collected_at"])
        return existing, False
    if existing:
        for key, value in payload.items():
            if value is None and key != "media":
                continue
            setattr(existing, key, value)
        existing.collected_at = timezone.now()
        existing.save()
        return existing, True
    row = InstagramSnapshot.objects.create(
        collected_at=timezone.now(),
        **lookup,
        **{key: value for key, value in payload.items() if key not in lookup},
    )
    return row, True


def replace_breakdowns(snapshot, rows, *, writable):
    if not writable or not rows:
        return
    snapshot.breakdowns.all().delete()
    InstagramInsightBreakdown.objects.bulk_create(
        [
            InstagramInsightBreakdown(
                snapshot=snapshot,
                dimension=dimension,
                segment=segment,
                value=None if amount is None else Decimal(amount),
            )
            for dimension, segment, amount in rows
        ]
    )


def refresh_follower_deltas(profile):
    rows = list(
        profile.snapshots.filter(media_key="", scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE).order_by(
            "snapshot_date"
        )
    )
    previous = None
    today = timezone.localdate()
    for row in rows:
        values = {"followers": None, "counter_reset": False}
        if previous and previous.followers is not None and row.followers is not None:
            delta = row.followers - previous.followers
            if delta < 0:
                values = {"followers": None, "counter_reset": True}
            else:
                values = {"followers": delta, "counter_reset": False}
        existing = profile.snapshots.filter(
            media_key="",
            snapshot_date=row.snapshot_date,
            scope=InstagramSnapshot.Scope.CALCULATED_DELTA,
        ).first()
        if existing and row.snapshot_date < today and existing.followers is not None:
            previous = row
            continue
        save_snapshot(
            profile,
            snapshot_date=row.snapshot_date,
            scope=InstagramSnapshot.Scope.CALCULATED_DELTA,
            source="calculated",
            values=values,
        )
        previous = row
