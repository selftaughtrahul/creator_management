import json
from functools import wraps

from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import is_creator
from networks.background import run_in_background
from networks.youtube.jobs import enqueue_youtube_sync, process_youtube_job
from networks.youtube.aggregates import growth
from networks.youtube_models import YouTubeChannel, YouTubeSyncJob, YouTubeVideo


def creator_json(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error("Authentication required.", 401)
        if not is_creator(request.user):
            return _error("This YouTube data is available to the connected creator.", 403)
        return view(request, *args, **kwargs)

    return wrapped


def _error(message, status):
    return JsonResponse({"error": message}, status=status, encoder=DjangoJSONEncoder)


def _page(request, queryset, serializer):
    try:
        page_size = int(request.GET.get("page_size") or 20)
    except ValueError:
        page_size = 20
    page_size = min(max(page_size, 1), 50)
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page") or 1)
    return JsonResponse(
        {
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "results": [serializer(item) for item in page.object_list],
        },
        encoder=DjangoJSONEncoder,
    )


def _channels(user):
    return YouTubeChannel.objects.filter(account__user=user, account__platform="youtube").select_related("account")


def _videos(user):
    return YouTubeVideo.objects.filter(channel__account__user=user).select_related("channel", "channel__account")


@creator_json
@require_GET
def channel_list(request):
    return _page(request, _channels(request.user), _channel_payload)


@creator_json
@require_GET
def channel_detail(request, channel_id):
    channel = _channels(request.user).filter(channel_id=channel_id).first()
    if channel is None:
        return _error("Channel not found.", 404)
    payload = _channel_payload(channel)
    payload["description"] = channel.description
    payload["thumbnail_urls"] = channel.thumbnail_urls
    payload["growth"] = growth(channel)
    payload["latest_job"] = _job_brief(_latest_job(channel))
    return JsonResponse(payload, encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def channel_videos(request, channel_id):
    channel = _channels(request.user).filter(channel_id=channel_id).first()
    if channel is None:
        return _error("Channel not found.", 404)
    return _page(request, channel.videos.all(), _video_payload)


@creator_json
@require_GET
def channel_analytics(request, channel_id):
    channel = _channels(request.user).filter(channel_id=channel_id).first()
    if channel is None:
        return _error("Channel not found.", 404)
    rows = (
        channel.snapshots.select_related("channel")
        .prefetch_related("breakdowns")
        .filter(video_key="")
        .exclude(scope="public_cumulative")
        .order_by("-snapshot_date")
    )
    return _page(request, rows, _snapshot_payload)


@creator_json
@require_GET
def channel_snapshots(request, channel_id):
    channel = _channels(request.user).filter(channel_id=channel_id).first()
    if channel is None:
        return _error("Channel not found.", 404)
    rows = channel.snapshots.select_related("channel").prefetch_related("breakdowns").order_by("-snapshot_date", "video_key")
    scope = request.GET.get("scope") or ""
    if scope:
        rows = rows.filter(scope=scope)
    video_id = request.GET.get("video_id")
    if video_id is not None and video_id != "":
        rows = rows.filter(video_key=video_id)
    elif request.GET.get("level") == "channel":
        rows = rows.filter(video_key="")
    return _page(request, rows, _snapshot_payload)


@creator_json
@require_GET
def video_detail(request, video_id):
    video = _videos(request.user).filter(video_id=video_id).first()
    if video is None:
        return _error("Video not found.", 404)
    payload = _video_payload(video)
    payload["description"] = video.description
    payload["tags"] = video.tags
    payload["thumbnail_urls"] = video.thumbnail_urls
    payload["statistics_extra"] = video.statistics
    return JsonResponse(payload, encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def video_analytics(request, video_id):
    video = _videos(request.user).filter(video_id=video_id).first()
    if video is None:
        return _error("Video not found.", 404)
    rows = (
        video.channel.snapshots.select_related("channel")
        .prefetch_related("breakdowns")
        .filter(video_key=video.video_id)
        .exclude(scope="public_cumulative")
    )
    return _page(request, rows.order_by("-snapshot_date"), _snapshot_payload)


@creator_json
@require_GET
def video_comments(request, video_id):
    video = _videos(request.user).filter(video_id=video_id).first()
    if video is None:
        return _error("Video not found.", 404)
    payload_page = _page(request, video.comment_rows.all(), _comment_payload)
    return payload_page


@creator_json
@require_GET
def video_transcript(request, video_id):
    video = _videos(request.user).filter(video_id=video_id).first()
    if video is None:
        return _error("Video not found.", 404)
    rows = list(video.transcripts.all())
    return JsonResponse(
        {
            "video_id": video.video_id,
            "available": any(row.status == "available" and row.text for row in rows),
            "results": [_transcript_payload(row) for row in rows],
        },
        encoder=DjangoJSONEncoder,
    )


@creator_json
@require_POST
def channel_sync(request, channel_id):
    channel = _channels(request.user).filter(channel_id=channel_id).select_related("account").first()
    if channel is None:
        return _error("Channel not found.", 404)
    if channel.account.is_sample or not channel.account.has_token:
        return _error("Connect a live YouTube account before syncing.", 400)
    mode = _mode(request)
    job = enqueue_youtube_sync(channel.account, mode=mode, requested_by=request.user)
    job_id = job.pk
    run_in_background(
        f"youtube:{channel.account_id}",
        lambda: process_youtube_job(YouTubeSyncJob.objects.get(pk=job_id)),
    )
    job.refresh_from_db()
    return JsonResponse(_job_payload(job), status=202, encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def sync_job_detail(request, job_id):
    job = YouTubeSyncJob.objects.filter(pk=job_id, account__user=request.user).first()
    if job is None:
        return _error("Sync job not found.", 404)
    return JsonResponse(_job_payload(job), encoder=DjangoJSONEncoder)


def _mode(request):
    if request.content_type == "application/json" and request.body:
        try:
            payload = json.loads(request.body.decode() or "{}")
        except ValueError:
            payload = {}
        mode = payload.get("mode") or "incremental"
    else:
        mode = request.POST.get("mode") or "incremental"
    if mode not in {"full", "incremental"}:
        return "incremental"
    return mode


def _latest_job(channel):
    return channel.account.youtube_sync_jobs.order_by("-created_at").first()


def _channel_payload(channel):
    return {
        "channel_id": channel.channel_id,
        "title": channel.title,
        "custom_url": channel.custom_url,
        "published_at": channel.published_at,
        "country": channel.country,
        "subscriber_count": channel.subscriber_count,
        "subscriber_count_hidden": channel.subscriber_count_hidden,
        "view_count": channel.view_count,
        "video_count": channel.video_count,
        "uploads_playlist_id": channel.uploads_playlist_id,
        "sync_status": channel.sync_status,
        "last_success_at": channel.last_success_at,
        "last_error": channel.last_error,
        "data_source": channel.data_source,
        "source_updated_at": channel.source_updated_at,
        "analytics_authorized": channel.analytics_authorized,
        "revenue_authorized": channel.revenue_authorized,
        "reauthorization_required": channel.reauthorization_required,
        "aggregates": {
            "total_duration_seconds": channel.total_duration_seconds,
            "public_videos": channel.public_video_count,
            "private_videos": channel.private_video_count,
            "unlisted_videos": channel.unlisted_video_count,
            "deleted_videos": channel.deleted_video_count,
            "deleted_videos_available": False,
            "removed_from_uploads": channel.removed_from_uploads_count,
            "privacy_counts_complete": channel.privacy_counts_complete,
            "summed_video_views": channel.summed_video_views,
            "summed_video_likes": channel.summed_video_likes,
            "summed_video_comments": channel.summed_video_comments,
            "official_view_count": channel.view_count,
            "videos_last_7_days": channel.videos_last_7_days,
            "videos_last_30_days": channel.videos_last_30_days,
            "videos_last_90_days": channel.videos_last_90_days,
            "videos_last_365_days": channel.videos_last_365_days,
            "average_views": channel.average_views,
            "average_duration_seconds": channel.average_duration_seconds,
            "upload_frequency_per_week": channel.upload_frequency_per_week,
            "most_viewed_video_id": channel.most_viewed_video_id,
            "latest_video_id": channel.latest_video_id,
        },
    }


def _video_payload(video):
    available = video.statistics_available or {}
    return {
        "video_id": video.video_id,
        "channel_id": video.channel.channel_id,
        "title": video.title,
        "published_at": video.published_at,
        "duration_seconds": video.duration_seconds,
        "duration_iso": video.duration_iso,
        "category_id": video.category_id,
        "default_language": video.default_language,
        "live_broadcast_status": video.live_broadcast_status,
        "privacy_status": video.privacy_status,
        "embeddable": video.embeddable,
        "made_for_kids": video.made_for_kids,
        "url": video.url,
        "availability": video.availability,
        "synced_at": video.synced_at,
        "comments_sync_status": video.comments_sync_status,
        "statistics": {
            "view_count": video.view_count,
            "like_count": video.like_count,
            "comment_count": video.comment_count,
            "favorite_count": video.favorite_count,
            "availability": {
                "view_count": bool(available.get("viewCount")),
                "like_count": bool(available.get("likeCount")),
                "comment_count": bool(available.get("commentCount")),
                "favorite_count": bool(available.get("favoriteCount")),
            },
        },
    }


def _snapshot_payload(row):
    return {
        "snapshot_date": row.snapshot_date,
        "channel_id": row.channel.channel_id,
        "video_id": row.video_key or None,
        "scope": row.scope,
        "period_start": row.period_start,
        "views": row.views,
        "engaged_views": row.engaged_views,
        "subscribers": row.subscribers,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "impressions": row.impressions,
        "ctr": row.ctr,
        "watch_time_minutes": row.watch_time_minutes,
        "average_view_duration_seconds": row.average_view_duration_seconds,
        "average_view_percentage": row.average_view_percentage,
        "subscribers_gained": row.subscribers_gained,
        "subscribers_lost": row.subscribers_lost,
        "estimated_revenue": row.estimated_revenue,
        "counter_reset": row.counter_reset,
        "source": row.source,
        "collected_at": row.collected_at,
        "breakdowns": [
            {"dimension": item.dimension, "segment": item.segment, "value": item.value}
            for item in row.breakdowns.all()
        ],
    }


def _comment_payload(row):
    return {
        "comment_id": row.comment_id,
        "video_id": row.video.video_id,
        "parent_comment_id": row.parent_comment_id or None,
        "author_display_name": row.author_display_name,
        "author_channel_id": row.author_channel_id or None,
        "text": row.text,
        "published_at": row.published_at,
        "updated_at": row.updated_at_remote,
        "like_count": row.like_count,
        "reply_count": row.reply_count,
        "is_reply": row.is_reply,
        "synced_at": row.synced_at,
    }


def _transcript_payload(row):
    return {
        "language": row.language,
        "track_id": row.track_id,
        "track_name": row.track_name,
        "caption_kind": row.caption_kind,
        "text": row.text,
        "segments": row.segments,
        "source": row.source,
        "status": row.status,
        "last_attempt_at": row.last_attempt_at,
        "last_success_at": row.last_success_at,
        "failure_reason": row.failure_reason,
        "content_hash": row.content_hash,
    }


def _job_brief(job):
    if job is None:
        return None
    return {"id": job.pk, "status": job.status, "outcome": job.outcome, "mode": job.mode}


def _job_payload(job):
    done = sum(1 for item in (job.stages or {}).values() if item.get("status") in {"success", "skipped", "partial", "failed", "permission_denied", "quota_exhausted", "unavailable"})
    total = max(len(job.stages or {}), 1)
    return {
        "id": job.pk,
        "mode": job.mode,
        "status": job.status,
        "outcome": job.outcome,
        "stages": job.stages,
        "progress": int(done / total * 100),
        "videos_seen": job.videos_seen,
        "videos_upserted": job.videos_upserted,
        "comments_upserted": job.comments_upserted,
        "api_calls": job.api_calls,
        "quota_units": job.quota_units,
        "last_error": job.last_error,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
