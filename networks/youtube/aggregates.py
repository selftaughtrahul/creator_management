from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Max
from django.utils import timezone

from networks.youtube_models import YouTubeSnapshot, YouTubeVideo


def recalculate_channel(channel):
    videos = list(channel.videos.all())
    available = [video for video in videos if video.availability == YouTubeVideo.Availability.AVAILABLE]
    durations = [video.duration_seconds for video in available if video.duration_seconds is not None]
    views = [video.view_count for video in available if video.view_count is not None]
    likes = [video.like_count for video in available if video.like_count is not None]
    comments = [video.comment_count for video in available if video.comment_count is not None]
    privacy = [video.privacy_status for video in available]
    known_privacy = [status for status in privacy if status]
    channel.total_duration_seconds = sum(durations) if durations else None
    channel.summed_video_views = sum(views) if views else None
    channel.summed_video_likes = sum(likes) if likes else None
    channel.summed_video_comments = sum(comments) if comments else None
    channel.average_views = _mean(views)
    channel.average_duration_seconds = _mean(durations)
    channel.public_video_count = _count(known_privacy, "public") if known_privacy else None
    channel.private_video_count = _count(known_privacy, "private") if known_privacy else None
    channel.unlisted_video_count = _count(known_privacy, "unlisted") if known_privacy else None
    channel.deleted_video_count = None
    channel.removed_from_uploads_count = sum(
        1 for video in videos if video.availability == YouTubeVideo.Availability.REMOVED
    )
    channel.privacy_counts_complete = bool(available) and len(known_privacy) == len(available)
    now = timezone.now()
    published = [video.published_at for video in available if video.published_at]
    channel.videos_last_7_days = _since(published, now, 7)
    channel.videos_last_30_days = _since(published, now, 30)
    channel.videos_last_90_days = _since(published, now, 90)
    channel.videos_last_365_days = _since(published, now, 365)
    channel.upload_frequency_per_week = _frequency(published)
    ranked = [video for video in available if video.view_count is not None]
    channel.most_viewed_video_id = max(ranked, key=lambda video: video.view_count).video_id if ranked else ""
    latest = [video for video in available if video.published_at]
    channel.latest_video_id = max(latest, key=lambda video: video.published_at).video_id if latest else ""
    channel.save()
    return channel


def growth(channel):
    rows = list(
        YouTubeSnapshot.objects.filter(
            channel=channel,
            video_key="",
            scope=YouTubeSnapshot.Scope.PUBLIC_CUMULATIVE,
        ).order_by("snapshot_date")
    )
    if len(rows) < 2:
        return {"available": False, "subscribers": None, "views": None, "from": None, "to": None}
    first, last = rows[0], rows[-1]
    return {
        "available": True,
        "from": first.snapshot_date.isoformat(),
        "to": last.snapshot_date.isoformat(),
        "subscribers": _change(first.subscribers, last.subscribers),
        "views": _change(first.views, last.views),
        "points": len(rows),
    }


def _change(start, end):
    if start is None or end is None:
        return None
    return end - start


def _count(values, expected):
    return sum(1 for value in values if str(value).lower() == expected)


def _since(moments, now, days):
    if not moments:
        return None
    cutoff = now - timedelta(days=days)
    return sum(1 for moment in moments if moment >= cutoff)


def _frequency(moments):
    if len(moments) < 2:
        return None
    span = (max(moments) - min(moments)).total_seconds() / 86400
    if span <= 0:
        return None
    per_week = Decimal(len(moments) - 1) / Decimal(str(span)) * Decimal(7)
    return per_week.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _mean(values):
    if not values:
        return None
    mean = Decimal(sum(values)) / Decimal(len(values))
    return mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def latest_published(channel):
    return channel.videos.filter(published_at__isnull=False).aggregate(latest=Max("published_at"))["latest"]
