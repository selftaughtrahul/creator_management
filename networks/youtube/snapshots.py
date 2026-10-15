from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from networks.youtube.parse import optional_decimal
from networks.youtube_models import YouTubeAnalyticsBreakdown, YouTubeSnapshot

PUBLIC = YouTubeSnapshot.Scope.PUBLIC_CUMULATIVE
ANALYTICS_DAILY = YouTubeSnapshot.Scope.ANALYTICS_DAILY
ANALYTICS_RANGE = YouTubeSnapshot.Scope.ANALYTICS_RANGE
ANALYTICS_RETENTION = YouTubeSnapshot.Scope.ANALYTICS_RETENTION
DELTA = YouTubeSnapshot.Scope.CALCULATED_DELTA

METRIC_FIELDS = (
    "views",
    "engaged_views",
    "subscribers",
    "likes",
    "comments",
    "shares",
    "impressions",
    "ctr",
    "watch_time_minutes",
    "average_view_duration_seconds",
    "average_view_percentage",
    "subscribers_gained",
    "subscribers_lost",
    "estimated_revenue",
)


def save_snapshot(
    channel,
    *,
    video=None,
    video_key="",
    snapshot_date,
    scope,
    source,
    period_start=None,
    merge=False,
    **metrics,
):
    today = timezone.localdate()
    key = video_key or (video.video_id if video else "")
    defaults = {name: metrics.get(name) for name in METRIC_FIELDS}
    defaults.update(
        {
            "video": video,
            "period_start": period_start,
            "source": source,
            "collected_at": timezone.now(),
            "counter_reset": bool(metrics.get("counter_reset")),
        }
    )
    try:
        with transaction.atomic():
            row, created = YouTubeSnapshot.objects.get_or_create(
                channel=channel,
                video_key=key,
                snapshot_date=snapshot_date,
                scope=scope,
                defaults=defaults,
            )
    except IntegrityError:
        row = YouTubeSnapshot.objects.get(
            channel=channel,
            video_key=key,
            snapshot_date=snapshot_date,
            scope=scope,
        )
        created = False
    if created:
        return row, True
    historical = row.snapshot_date < today
    if historical and not merge:
        return row, False
    changed = False
    for name in METRIC_FIELDS:
        if name not in metrics:
            continue
        value = metrics[name]
        current = getattr(row, name)
        if historical and (value is None or current is not None):
            continue
        if current != value:
            setattr(row, name, value)
            changed = True
    if "counter_reset" in metrics and row.counter_reset != bool(metrics["counter_reset"]):
        row.counter_reset = bool(metrics["counter_reset"])
        changed = True
    if not historical:
        row.source = source
        row.collected_at = timezone.now()
        changed = True
    if period_start and row.period_start != period_start and not historical:
        row.period_start = period_start
        changed = True
    if video and row.video_id != video.pk:
        row.video = video
        changed = True
    if changed:
        row.save()
    return row, False


def replace_breakdowns(snapshot, rows):
    if snapshot.snapshot_date < timezone.localdate():
        return
    snapshot.breakdowns.all().delete()
    prepared = []
    seen = set()
    for item in rows:
        segment = str(item.get("segment") or "")[:80]
        dimension = str(item.get("dimension") or "")[:40]
        if not dimension or not segment:
            continue
        marker = (dimension, segment)
        if marker in seen:
            continue
        seen.add(marker)
        prepared.append(
            YouTubeAnalyticsBreakdown(
                snapshot=snapshot,
                dimension=dimension,
                segment=segment,
                value=optional_decimal(item.get("value")),
            )
        )
    if prepared:
        YouTubeAnalyticsBreakdown.objects.bulk_create(prepared)


def refresh_calculated_delta(channel, video_key, snapshot_date):
    current = (
        YouTubeSnapshot.objects.filter(
            channel=channel,
            video_key=video_key,
            scope=PUBLIC,
            snapshot_date=snapshot_date,
        )
        .order_by("-collected_at")
        .first()
    )
    if current is None:
        return None
    previous = (
        YouTubeSnapshot.objects.filter(
            channel=channel,
            video_key=video_key,
            scope=PUBLIC,
            snapshot_date__lt=snapshot_date,
        )
        .order_by("-snapshot_date")
        .first()
    )
    if previous is None:
        return None
    delta = {}
    reset = False
    comparable = ("views", "subscribers", "likes", "comments")
    for name in comparable:
        latest = getattr(current, name)
        prior = getattr(previous, name)
        if latest is None or prior is None:
            delta[name] = None
            continue
        if latest < prior:
            reset = True
            delta[name] = None
            continue
        delta[name] = latest - prior
    row, _created = save_snapshot(
        channel,
        video=current.video,
        video_key=video_key,
        snapshot_date=snapshot_date,
        scope=DELTA,
        source="calculated_snapshot_delta",
        views=delta["views"],
        subscribers=delta["subscribers"],
        likes=delta["likes"],
        comments=delta["comments"],
        counter_reset=reset,
    )
    return row


def rows_from_report(report):
    headers = [column.get("name") for column in report.get("columnHeaders") or []]
    parsed = []
    for row in report.get("rows") or []:
        if not isinstance(row, list):
            continue
        parsed.append(dict(zip(headers, row)))
    return parsed


def metrics_from_record(record):
    return {
        "views": _int_field(record, "views"),
        "engaged_views": _int_field(record, "engagedViews"),
        "likes": _int_field(record, "likes"),
        "comments": _int_field(record, "comments"),
        "shares": _int_field(record, "shares"),
        "impressions": _int_field(record, "impressions"),
        "ctr": optional_decimal(record.get("impressionClickThroughRate")),
        "watch_time_minutes": optional_decimal(record.get("estimatedMinutesWatched")),
        "average_view_duration_seconds": optional_decimal(record.get("averageViewDuration")),
        "average_view_percentage": optional_decimal(record.get("averageViewPercentage")),
        "subscribers_gained": _int_field(record, "subscribersGained"),
        "subscribers_lost": _int_field(record, "subscribersLost"),
        "estimated_revenue": optional_decimal(record.get("estimatedRevenue")),
    }


def _int_field(record, key):
    if key not in record or record.get(key) is None or record.get(key) == "":
        return None
    try:
        return int(Decimal(str(record.get(key))))
    except (ArithmeticError, ValueError, TypeError):
        return None
