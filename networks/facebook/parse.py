from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value).replace("Z", "+00:00"))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def insight_day(end_time):
    parsed = parse_dt(end_time)
    if parsed is None:
        return None
    return timezone.localtime(parsed - timedelta(seconds=1)).date()


def as_optional_int(value):
    if value is None or value == "" or isinstance(value, (dict, list)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def summary_count(node, key):
    if key not in node:
        return None
    summary = (node.get(key) or {}).get("summary") or {}
    if "total_count" not in summary:
        return None
    return as_optional_int(summary.get("total_count"))


def share_count(node):
    if "shares" not in node:
        return None
    return as_optional_int((node.get("shares") or {}).get("count"))


def title_from_message(message):
    text = (message or "").strip()
    if not text:
        return "Facebook post"
    line = text.splitlines()[0].strip()
    return line[:180] or "Facebook post"


def content_type(status_type):
    kind = (status_type or "").lower()
    if "video" in kind:
        return "video"
    if "photo" in kind:
        return "image"
    if "share" in kind:
        return "share"
    return "post"


def is_video(status_type):
    return "video" in (status_type or "").lower()


# Current Page Insights names win over the deprecated impression and fan metrics.
METRIC_FIELDS = {
    "page_follows": ("followers", 2),
    "page_daily_follows_unique": ("followers_gained", 2),
    "page_daily_unfollows_unique": ("followers_lost", 2),
    "page_fan_adds": ("followers_gained", 1),
    "page_fan_removes": ("followers_lost", 1),
    "page_media_view": ("views", 2),
    "page_impressions": ("views", 1),
    "page_total_media_view_unique": ("reach", 2),
    "page_impressions_unique": ("reach", 1),
    "page_post_engagements": ("engagements", 2),
    "page_video_views": ("video_views", 2),
    "page_video_view_time": ("watch_time_ms", 2),
    "post_media_view": ("views", 2),
    "post_impressions": ("views", 1),
    "post_total_media_view_unique": ("reach", 2),
    "post_impressions_unique": ("reach", 1),
    "post_clicks": ("clicks", 2),
    "post_video_views": ("video_views", 2),
    "post_video_avg_time_watched": ("avg_watch_time_ms", 2),
    "post_video_view_time": ("watch_time_ms", 2),
}

LEGACY_SUBSTITUTES = {
    "page_media_view": "page_impressions",
    "page_total_media_view_unique": "page_impressions_unique",
    "page_daily_follows_unique": "page_fan_adds",
    "page_daily_unfollows_unique": "page_fan_removes",
    "post_media_view": "post_impressions",
    "post_total_media_view_unique": "post_impressions_unique",
}


def apply_metric(bucket, name, value):
    mapped = METRIC_FIELDS.get(name)
    amount = as_optional_int(value)
    if not mapped or amount is None:
        return
    field, rank = mapped
    current = bucket.get(field)
    if current and current[0] > rank:
        return
    bucket[field] = (rank, amount)


def bucket_values(bucket):
    return {field: rank_amount[1] for field, rank_amount in bucket.items()}


def metric_points(payload):
    points = []
    for item in (payload or {}).get("data") or []:
        name = item.get("name") or ""
        for row in item.get("values") or []:
            points.append({"name": name, "end_time": row.get("end_time"), "value": row.get("value")})
    return points


def dict_rows(value, dimension):
    if not isinstance(value, dict):
        return []
    rows = []
    for key, amount in value.items():
        parsed = as_optional_int(amount)
        if parsed is None:
            continue
        rows.append((dimension, str(key)[:80], parsed))
    return rows
