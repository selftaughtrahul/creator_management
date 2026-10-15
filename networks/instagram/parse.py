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
    """Date the day period belongs to.

    Instagram returns end_time at the close of the reporting day. Subtracting
    one second keeps a midnight boundary on the day that just finished.
    """
    parsed = parse_dt(end_time)
    if parsed is None:
        return None
    return timezone.localtime(parsed - timedelta(seconds=1)).date()


def as_optional_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def title_from_caption(caption):
    text = (caption or "").strip()
    if not text:
        return "Instagram post"
    line = text.splitlines()[0].strip()
    return line[:180] or "Instagram post"


def content_type(media_type, product_type):
    product = (product_type or "").upper()
    if product == "REELS":
        return "reel"
    if product == "STORY":
        return "story"
    kind = (media_type or "").upper()
    return {"IMAGE": "image", "VIDEO": "video", "CAROUSEL_ALBUM": "carousel"}.get(kind, "post")


METRIC_FIELDS = {
    "reach": "reach",
    "views": "views",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "saves": "saves",
    "saved": "saves",
    "replies": "replies",
    "accounts_engaged": "accounts_engaged",
    "total_interactions": "total_interactions",
    "follower_count": "followers_gained",
    "ig_reels_avg_watch_time": "avg_watch_time_ms",
    "ig_reels_video_view_total_time": "total_watch_time_ms",
}


def metric_points(payload):
    points = []
    for item in (payload or {}).get("data") or []:
        name = item.get("name") or ""
        total = item.get("total_value")
        if isinstance(total, dict) and ("value" in total or total.get("breakdowns")):
            points.append(
                {
                    "name": name,
                    "end_time": None,
                    "value": total.get("value"),
                    "breakdowns": total.get("breakdowns") or [],
                }
            )
        for row in item.get("values") or []:
            points.append(
                {
                    "name": name,
                    "end_time": row.get("end_time"),
                    "value": row.get("value"),
                    "breakdowns": [],
                }
            )
    return points


def breakdown_rows(blocks):
    rows = []
    for block in blocks or []:
        keys = block.get("dimension_keys") or []
        dimension = keys[0] if keys else "unknown"
        for result in block.get("results") or []:
            values = result.get("dimension_values") or []
            segment = str(values[0]) if values else ""
            if not segment:
                continue
            rows.append((dimension, segment[:80], as_optional_int(result.get("value"))))
    return rows
