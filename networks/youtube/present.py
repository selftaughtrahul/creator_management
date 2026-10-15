from django.core.paginator import Paginator

from networks.youtube.aggregates import growth


def platform_context(account, page_number):
    channels = list(account.youtube_channels.all())
    if not channels:
        return None
    links = dict(account.contents.values_list("external_id", "pk"))
    job = account.youtube_sync_jobs.order_by("-created_at").first()
    return {
        "channels": [_channel_block(channel, links, page_number) for channel in channels],
        "job": _job_block(job),
    }


def video_context(item):
    video = None
    for channel in item.account.youtube_channels.all():
        video = channel.videos.filter(video_id=item.external_id).first()
        if video:
            break
    if video is None:
        return None
    analytics = list(
        video.channel.snapshots.filter(video_key=video.video_id)
        .exclude(scope="analytics_retention")
        .prefetch_related("breakdowns")
        .order_by("-snapshot_date", "scope")[:12]
    )
    retention = (
        video.channel.snapshots.filter(video_key=video.video_id, scope="analytics_retention")
        .prefetch_related("breakdowns")
        .order_by("-snapshot_date")
        .first()
    )
    return {
        "video": video,
        "duration_label": format_duration(video.duration_seconds),
        "comments": list(video.comment_rows.all()[:30]),
        "comment_total": video.comment_rows.count(),
        "transcripts": list(video.transcripts.all()),
        "analytics": analytics,
        "retention": retention,
        "channel": video.channel,
    }


def format_duration(seconds):
    if seconds is None:
        return None
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _channel_block(channel, links, page_number):
    videos = Paginator(channel.videos.all(), 8).get_page(page_number)
    for video in videos:
        video.content_pk = links.get(video.video_id)
        video.duration_label = format_duration(video.duration_seconds)
    daily = list(
        channel.snapshots.filter(video_key="", scope="analytics_daily").order_by("-snapshot_date")[:14]
    )
    cumulative = list(
        channel.snapshots.filter(video_key="", scope="public_cumulative").order_by("-snapshot_date")[:14]
    )
    report = (
        channel.snapshots.filter(video_key="", scope="analytics_range")
        .prefetch_related("breakdowns")
        .order_by("-snapshot_date")
        .first()
    )
    return {
        "channel": channel,
        "videos": videos,
        "growth": growth(channel),
        "daily": daily,
        "cumulative": cumulative,
        "report": report,
        "duration_label": format_duration(channel.total_duration_seconds),
        "average_duration_label": format_duration(
            int(channel.average_duration_seconds) if channel.average_duration_seconds is not None else None
        ),
    }


def _job_block(job):
    if job is None:
        return None
    stages = []
    for name, item in (job.stages or {}).items():
        stages.append(
            {
                "name": name.replace("_", " "),
                "status": item.get("status") or "",
                "detail": item.get("detail") or "",
            }
        )
    return {"job": job, "stages": stages}
