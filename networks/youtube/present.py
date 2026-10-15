from django.core.paginator import Paginator

from networks.countries import country_label
from networks.youtube.aggregates import growth

GENDER_LABELS = {
    "female": "Female",
    "male": "Male",
    "user_specified": "User specified",
}
AGE_LABELS = {
    "age13-17": "13–17",
    "age18-24": "18–24",
    "age25-34": "25–34",
    "age35-44": "35–44",
    "age45-54": "45–54",
    "age55-64": "55–64",
    "age65-": "65+",
}
AUDIENCE_DIMENSIONS = {"gender", "age", "age_gender", "country", "country_watch_time"}


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
        "audience": _audience(report),
        "other_breakdowns": _other_breakdowns(report),
        "duration_label": format_duration(channel.total_duration_seconds),
        "average_duration_label": format_duration(
            int(channel.average_duration_seconds) if channel.average_duration_seconds is not None else None
        ),
    }


def _audience(report):
    if report is None:
        return None
    rows = list(report.breakdowns.all())
    genders = [_share_row(row.segment, GENDER_LABELS.get(row.segment, row.segment), row.value) for row in rows if row.dimension == "gender"]
    ages = [_share_row(row.segment, AGE_LABELS.get(row.segment, row.segment), row.value) for row in rows if row.dimension == "age"]
    crossed = []
    for row in rows:
        if row.dimension != "age_gender" or "|" not in row.segment:
            continue
        age, gender = row.segment.split("|", 1)
        crossed.append(
            _share_row(
                row.segment,
                f"{AGE_LABELS.get(age, age)} · {GENDER_LABELS.get(gender, gender)}",
                row.value,
            )
        )
    watch = {row.segment: row.value for row in rows if row.dimension == "country_watch_time"}
    countries = []
    for row in rows:
        if row.dimension != "country":
            continue
        countries.append(
            {
                "code": row.segment,
                "label": country_label(row.segment),
                "views": row.value,
                "watch_time_minutes": watch.get(row.segment),
            }
        )
    genders.sort(key=lambda item: item["share"] or 0, reverse=True)
    ages.sort(key=lambda item: item["share"] or 0, reverse=True)
    crossed.sort(key=lambda item: item["share"] or 0, reverse=True)
    countries.sort(key=lambda item: item["views"] or 0, reverse=True)
    if not genders and not ages and not crossed and not countries:
        return None
    return {
        "period_start": report.period_start,
        "period_end": report.snapshot_date,
        "genders": genders,
        "ages": ages,
        "age_genders": crossed,
        "countries": countries,
    }


def _share_row(segment, label, value):
    return {"segment": segment, "label": label, "share": value}


def _other_breakdowns(report):
    if report is None:
        return []
    return [row for row in report.breakdowns.all() if row.dimension not in AUDIENCE_DIMENSIONS]


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
