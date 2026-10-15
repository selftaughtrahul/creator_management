from decimal import Decimal

from django.utils import timezone

from networks.facebook_models import FacebookInsightBreakdown, FacebookSnapshot

VALUE_FIELDS = (
    "followers",
    "fans",
    "followers_gained",
    "followers_lost",
    "views",
    "reach",
    "engagements",
    "reactions",
    "comments",
    "shares",
    "clicks",
    "video_views",
    "avg_watch_time_ms",
    "watch_time_ms",
    "counter_reset",
    "period_start",
    "source",
    "post",
)


def save_snapshot(page, *, post=None, post_key="", snapshot_date, scope, source, values, period_start=None):
    """Write one snapshot. Past dates keep existing numbers and only fill blanks."""
    today = timezone.localdate()
    lookup = {
        "page": page,
        "post_key": post_key or "",
        "snapshot_date": snapshot_date,
        "scope": scope,
    }
    existing = FacebookSnapshot.objects.filter(**lookup).first()
    payload = {key: values[key] for key in VALUE_FIELDS if key in values}
    payload["source"] = source
    payload["post"] = post
    if period_start is not None:
        payload["period_start"] = period_start
    if existing and snapshot_date < today:
        changed = []
        for key, value in payload.items():
            if key in {"source", "post"}:
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
            if value is None and key != "post":
                continue
            setattr(existing, key, value)
        existing.collected_at = timezone.now()
        existing.save()
        return existing, True
    row = FacebookSnapshot.objects.create(
        collected_at=timezone.now(),
        **lookup,
        **{key: value for key, value in payload.items() if key not in lookup},
    )
    return row, True


def replace_breakdowns(snapshot, rows, *, writable):
    if not writable or not rows:
        return
    snapshot.breakdowns.all().delete()
    FacebookInsightBreakdown.objects.bulk_create(
        [
            FacebookInsightBreakdown(
                snapshot=snapshot,
                dimension=dimension,
                segment=segment,
                value=None if amount is None else Decimal(amount),
            )
            for dimension, segment, amount in rows
        ]
    )


def refresh_follower_deltas(page):
    rows = list(
        page.snapshots.filter(post_key="", scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE).order_by("snapshot_date")
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
        existing = page.snapshots.filter(
            post_key="",
            snapshot_date=row.snapshot_date,
            scope=FacebookSnapshot.Scope.CALCULATED_DELTA,
        ).first()
        if existing and row.snapshot_date < today and existing.followers is not None:
            previous = row
            continue
        save_snapshot(
            page,
            snapshot_date=row.snapshot_date,
            scope=FacebookSnapshot.Scope.CALCULATED_DELTA,
            source="calculated",
            values=values,
        )
        previous = row
