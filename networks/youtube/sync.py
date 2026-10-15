import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

from networks.fetchers import FetchedSnapshot
from networks.http import ProviderError
from networks.models import ContentItem
from networks.oauth import ensure_access_token
from networks.sync import _upsert_snapshot
from networks.youtube.aggregates import recalculate_channel
from networks.youtube.client import YouTubeAPIError, YouTubeClient
from networks.youtube.parse import (
    clip,
    date_from_string,
    optional_int,
    parse_dt,
    parse_iso8601_duration,
    preferred_thumbnail,
    thumbnail_map,
)
from networks.youtube.snapshots import (
    ANALYTICS_DAILY,
    ANALYTICS_RANGE,
    ANALYTICS_RETENTION,
    metrics_from_record,
    refresh_calculated_delta,
    replace_breakdowns,
    rows_from_report,
    save_snapshot,
)
from networks.youtube.transcripts import (
    ConfiguredTranscriptProvider,
    YouTubeCaptionProvider,
    record_status,
    scopes_allow_captions,
    store_transcript,
)
from networks.youtube_models import YouTubeChannel, YouTubeTranscript, YouTubeVideo, YouTubeVideoComment

logger = logging.getLogger("networks.youtube")

STAGES = (
    "credentials",
    "channel",
    "videos",
    "comments",
    "transcripts",
    "channel_analytics",
    "video_analytics",
    "snapshots",
    "aggregates",
)

CORE_METRICS = "views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares,subscribersGained,subscribersLost"
IMPRESSION_METRICS = "views,impressions,impressionClickThroughRate"
REVENUE_METRICS = "views,estimatedRevenue"
ENGAGED_METRICS = "engagedViews"
VIDEO_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost"


def limits_for(mode):
    full = mode == "full"
    return {
        "videos": int(settings.YOUTUBE_MAX_VIDEOS if full else settings.YOUTUBE_INCREMENTAL_VIDEOS),
        "comments_per_video": int(settings.YOUTUBE_COMMENT_LIMIT),
        "comment_videos": int(
            settings.YOUTUBE_FULL_COMMENT_VIDEO_LIMIT if full else settings.YOUTUBE_COMMENT_VIDEO_LIMIT
        ),
        "transcript_videos": int(
            settings.YOUTUBE_FULL_TRANSCRIPT_VIDEO_LIMIT if full else settings.YOUTUBE_TRANSCRIPT_VIDEO_LIMIT
        ),
        "analytics_days": int(settings.YOUTUBE_ANALYTICS_DAYS if full else min(settings.YOUTUBE_ANALYTICS_DAYS, 28)),
        "video_analytics": int(settings.YOUTUBE_VIDEO_ANALYTICS_LIMIT),
        "retention_videos": int(settings.YOUTUBE_RETENTION_VIDEO_LIMIT),
    }


def sync_youtube(account, job):
    started = timezone.now()
    limits = limits_for(job.mode)
    client = None
    try:
        client = _client_for(account, job)
        channels = _sync_channels(account, client, job)
        if not channels:
            _stage(job, "channel", "failed", "No YouTube channel was returned.")
            _finish(job, "failed", "permanent", "No YouTube channel was found for this Google account.")
            return job
        for channel in channels:
            _sync_channel_videos(channel, client, job, limits)
        _sync_comments(channels, client, job, limits)
        _sync_transcripts(channels, client, job, limits)
        _sync_channel_analytics(channels, client, job, limits)
        _sync_video_analytics(channels, client, job, limits)
        _stage(job, "snapshots", "success", "Snapshots were saved without replacing older days.")
        for channel in channels:
            recalculate_channel(channel)
            _touch_dashboard(account, channel)
        _stage(job, "aggregates", "success", "")
        outcome, status = _overall(job)
        error = "" if status == "completed" else _stage_errors(job)
        _finish(job, status, outcome, error)
    except YouTubeAPIError as exc:
        _fail_open_stage(job, exc)
        status = "failed"
        if exc.kind == "quota":
            outcome = "quota_exhausted"
        elif exc.kind == "permission":
            outcome = "permission_denied"
            _mark_reauth(account, True)
        elif exc.kind == "retryable":
            outcome = "retryable"
            status = "pending" if job.attempts < int(settings.YOUTUBE_JOB_MAX_ATTEMPTS) else "failed"
        else:
            outcome = "permanent"
        _finish(job, status, outcome, str(exc))
    except ProviderError as exc:
        message = str(exc)
        permanent = any(token in message.lower() for token in ("expired", "invalid_grant", "connect youtube"))
        if permanent:
            _mark_reauth(account, True)
        _stage(job, "credentials", "permission_denied" if permanent else "failed", message)
        outcome = "permission_denied" if permanent else "retryable"
        status = "failed"
        if outcome == "retryable" and job.attempts < int(settings.YOUTUBE_JOB_MAX_ATTEMPTS):
            status = "pending"
        _finish(job, status, outcome, message)
    finally:
        if client is not None:
            job.api_calls = client.quota.calls
            job.quota_units = client.quota.units
        elapsed = (timezone.now() - started).total_seconds()
        logger.info(
            "youtube_sync_finished job_id=%s status=%s videos=%s comments=%s api_calls=%s quota_units=%s seconds=%.2f",
            job.pk,
            job.status,
            job.videos_upserted,
            job.comments_upserted,
            job.api_calls,
            job.quota_units,
            elapsed,
        )
        job.save(update_fields=["api_calls", "quota_units", "status", "outcome", "last_error", "finished_at", "stages"])
    return job


def _client_for(account, job):
    if account.is_sample or not account.has_token:
        raise ProviderError("Connect a live YouTube account before syncing.")
    token = ensure_access_token(account)
    _stage(job, "credentials", "success", "")
    account.youtube_channels.update(reauthorization_required=False)
    if job.granted_scopes:
        account.youtube_channels.update(granted_scopes=job.granted_scopes)
    logger.info("youtube_sync_started job_id=%s account_id=%s mode=%s", job.pk, account.pk, job.mode)
    return YouTubeClient(token)


def _sync_channels(account, client, job):
    payload = client.channels()
    items = payload.get("items") or []
    if not items:
        return []
    saved = []
    for item in items:
        if not item.get("id"):
            continue
        channel = _upsert_channel(account, item, job.granted_scopes)
        saved.append(channel)
    _apply_scope_flags(saved, job.granted_scopes)
    primary = _primary(saved)
    _touch_account(account, primary, len(saved))
    _stage(job, "channel", "success", f"channels={len(saved)}")
    logger.info("youtube_channels job_id=%s count=%s", job.pk, len(saved))
    return saved


def _upsert_channel(account, item, scopes):
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or ""
    hidden = bool(stats.get("hiddenSubscriberCount"))
    channel, _created = YouTubeChannel.objects.update_or_create(
        account=account,
        channel_id=item.get("id") or "",
        defaults={
            "title": clip(snippet.get("title") or "", 255),
            "description": snippet.get("description") or "",
            "custom_url": clip(snippet.get("customUrl") or "", 255),
            "thumbnail_urls": thumbnail_map(snippet.get("thumbnails")),
            "published_at": parse_dt(snippet.get("publishedAt")),
            "country": clip(snippet.get("country") or "", 8),
            "subscriber_count": None if hidden else optional_int(stats, "subscriberCount"),
            "subscriber_count_hidden": hidden,
            "view_count": optional_int(stats, "viewCount"),
            "video_count": optional_int(stats, "videoCount"),
            "uploads_playlist_id": uploads,
            "data_source": "youtube_data_api",
            "source_updated_at": timezone.now(),
            "sync_status": YouTubeChannel.SyncStatus.RUNNING,
            "granted_scopes": scopes or "",
        },
    )
    today = timezone.localdate()
    save_snapshot(
        channel,
        snapshot_date=today,
        scope="public_cumulative",
        source="youtube_data_api",
        views=channel.view_count,
        subscribers=channel.subscriber_count,
    )
    refresh_calculated_delta(channel, "", today)
    return channel


def _sync_channel_videos(channel, client, job, limits):
    if not channel.uploads_playlist_id:
        _stage(job, "videos", "partial", f"{channel.channel_id} has no uploads playlist.")
        return
    cursor = dict(job.cursor or {})
    page_token = cursor.get(channel.channel_id) or ""
    started_at_beginning = not page_token
    seen = []
    remaining = limits["videos"]
    exhausted = False
    try:
        while remaining > 0:
            page = client.playlist_page(channel.uploads_playlist_id, page_token)
            ids = []
            for item in page.get("items") or []:
                video_id = ((item.get("contentDetails") or {}).get("videoId")) or ""
                if video_id:
                    ids.append(video_id)
            ids = ids[:remaining]
            if not ids:
                page_token = page.get("nextPageToken") or ""
                cursor[channel.channel_id] = page_token
                job.cursor = cursor
                job.save(update_fields=["cursor"])
                if not page_token:
                    exhausted = True
                    break
                continue
            seen.extend(ids)
            remaining -= len(ids)
            for offset in range(0, len(ids), 50):
                batch = ids[offset : offset + 50]
                details = client.videos(batch)
                returned = {row.get("id") for row in details.get("items") or []}
                for row in details.get("items") or []:
                    _upsert_video(channel, row)
                    job.videos_upserted += 1
                for missing in batch:
                    if missing not in returned:
                        _mark_video_unavailable(channel, missing)
            page_token = page.get("nextPageToken") or ""
            cursor[channel.channel_id] = page_token
            job.cursor = cursor
            job.videos_seen = job.videos_upserted
            job.save(update_fields=["cursor", "videos_seen", "videos_upserted"])
            if not page_token:
                exhausted = True
                break
            if not ids:
                break
        if job.mode == "full" and started_at_beginning and exhausted:
            _mark_removed(channel, seen)
        _stage(job, "videos", "success", f"videos={job.videos_upserted}")
        logger.info("youtube_videos job_id=%s channel_id=%s upserted=%s", job.pk, channel.channel_id, job.videos_upserted)
    except YouTubeAPIError as exc:
        if exc.kind in {"quota", "permission"}:
            raise
        _stage(job, "videos", "partial", str(exc))


def _upsert_video(channel, row):
    snippet = row.get("snippet") or {}
    stats = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
    status = row.get("status") if isinstance(row.get("status"), dict) else {}
    details = row.get("contentDetails") or {}
    duration_iso = details.get("duration") or ""
    known_stats = ("viewCount", "likeCount", "commentCount", "favoriteCount")
    extra = {key: value for key, value in stats.items() if key not in known_stats}
    availability = {key: key in stats and stats.get(key) not in (None, "") for key in known_stats}
    video_id = row.get("id") or ""
    published = parse_dt(snippet.get("publishedAt"))
    privacy = status.get("privacyStatus") if "privacyStatus" in status else None
    video, _created = YouTubeVideo.objects.update_or_create(
        channel=channel,
        video_id=video_id,
        defaults={
            "title": clip(snippet.get("title") or "Untitled video", 300),
            "description": snippet.get("description") or "",
            "published_at": published,
            "duration_seconds": parse_iso8601_duration(duration_iso),
            "duration_iso": duration_iso,
            "thumbnail_urls": thumbnail_map(snippet.get("thumbnails")),
            "category_id": snippet.get("categoryId") or "",
            "default_language": snippet.get("defaultLanguage") or snippet.get("defaultAudioLanguage") or "",
            "tags": snippet.get("tags") if isinstance(snippet.get("tags"), list) else [],
            "live_broadcast_status": snippet.get("liveBroadcastContent") or "",
            "privacy_status": privacy,
            "embeddable": status.get("embeddable") if "embeddable" in status else None,
            "made_for_kids": status.get("madeForKids") if "madeForKids" in status else None,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "view_count": optional_int(stats, "viewCount"),
            "like_count": optional_int(stats, "likeCount"),
            "comment_count": optional_int(stats, "commentCount"),
            "favorite_count": optional_int(stats, "favoriteCount"),
            "statistics": extra,
            "statistics_available": availability,
            "availability": YouTubeVideo.Availability.AVAILABLE,
            "synced_at": timezone.now(),
        },
    )
    _upsert_content_item(channel.account, video)
    save_snapshot(
        channel,
        video=video,
        video_key=video.video_id,
        snapshot_date=timezone.localdate(),
        scope="public_cumulative",
        source="youtube_data_api",
        views=video.view_count,
        likes=video.like_count,
        comments=video.comment_count,
    )
    refresh_calculated_delta(channel, video.video_id, timezone.localdate())
    return video


def _upsert_content_item(account, video):
    defaults = {
        "title": video.title or "Untitled video",
        "caption": (video.description or "")[:4000],
        "content_type": "short" if video.duration_seconds is not None and video.duration_seconds <= 60 else "video",
        "url": video.url,
        "thumbnail_url": preferred_thumbnail(video.thumbnail_urls),
        "published_at": video.published_at,
    }
    if video.view_count is not None:
        defaults["views"] = video.view_count
    if video.like_count is not None:
        defaults["likes"] = min(video.like_count, 2_147_483_647)
    if video.comment_count is not None:
        defaults["comments"] = min(video.comment_count, 2_147_483_647)
    ContentItem.objects.update_or_create(account=account, external_id=video.video_id, defaults=defaults)


def _mark_video_unavailable(channel, video_id):
    YouTubeVideo.objects.filter(channel=channel, video_id=video_id).update(
        availability=YouTubeVideo.Availability.REMOVED
    )


def _mark_removed(channel, seen_ids):
    if not seen_ids:
        return
    channel.videos.exclude(video_id__in=seen_ids).update(availability=YouTubeVideo.Availability.REMOVED)


def _sync_comments(channels, client, job, limits):
    failures = 0
    synced = 0
    videos = _recent_videos(channels, limits["comment_videos"])
    for video in videos:
        try:
            synced += _sync_video_comments(client, video, limits["comments_per_video"])
        except YouTubeAPIError as exc:
            if exc.kind == "quota":
                _stage(job, "comments", "quota_exhausted", str(exc))
                raise
            if exc.kind == "unavailable" and exc.reason in {"commentsDisabled", "commentDisabled"}:
                video.comments_sync_status = "disabled"
                video.comments_sync_error = str(exc)
                video.comments_synced_at = timezone.now()
                video.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
                continue
            failures += 1
            video.comments_sync_status = "failed"
            video.comments_sync_error = str(exc)
            video.comments_synced_at = timezone.now()
            video.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
            logger.info("youtube_comments_failed video_id=%s kind=%s", video.video_id, exc.kind)
    job.comments_upserted = synced
    job.save(update_fields=["comments_upserted"])
    status = "partial" if failures else "success"
    _stage(job, "comments", status, f"comments={synced} failures={failures}")


def _sync_video_comments(client, video, limit):
    page_token = ""
    stored = 0
    stop = False
    while stored < limit and not stop:
        payload = client.comment_threads(video.video_id, page_token)
        items = payload.get("items") or []
        if not items and not page_token:
            video.comments_sync_status = "empty"
            video.comments_sync_error = ""
            video.comments_synced_at = timezone.now()
            video.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
            return 0
        for thread in items:
            if stored >= limit:
                break
            snippet = (thread.get("snippet") or {})
            top = snippet.get("topLevelComment") or {}
            if _older_than_watermark(top, video.comments_synced_at) and _comment_exists(top):
                stop = True
                break
            if _store_comment(video, top, reply_count=optional_int(snippet, "totalReplyCount"), is_reply=False):
                stored += 1
            replies = ((thread.get("replies") or {}).get("comments")) or []
            parent_id = top.get("id") or ""
            for reply in replies:
                if stored >= limit:
                    break
                if _store_comment(video, reply, is_reply=True):
                    stored += 1
            total_replies = optional_int(snippet, "totalReplyCount") or 0
            if parent_id and total_replies > len(replies) and stored < limit:
                stored += _sync_replies(client, video, parent_id, limit - stored)
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    video.comments_sync_status = "success"
    video.comments_sync_error = ""
    video.comments_synced_at = timezone.now()
    video.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
    return stored


def _sync_replies(client, video, parent_id, limit):
    stored = 0
    page_token = ""
    while stored < limit:
        payload = client.comment_replies(parent_id, page_token)
        for reply in payload.get("items") or []:
            if stored >= limit:
                break
            if _store_comment(video, reply, is_reply=True):
                stored += 1
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return stored


def _store_comment(video, node, reply_count=None, is_reply=False):
    snippet = node.get("snippet") or {}
    comment_id = node.get("id") or ""
    if not comment_id:
        return False
    author_channel = snippet.get("authorChannelId") or {}
    author_channel_id = author_channel.get("value") if isinstance(author_channel, dict) else ""
    text = snippet.get("textOriginal")
    if text is None:
        text = strip_tags(snippet.get("textDisplay") or "")
    parent = snippet.get("parentId") or ""
    YouTubeVideoComment.objects.update_or_create(
        comment_id=comment_id,
        defaults={
            "video": video,
            "parent_comment_id": parent,
            "author_display_name": clip(snippet.get("authorDisplayName") or "", 255),
            "author_channel_id": author_channel_id or "",
            "text": text or "",
            "published_at": parse_dt(snippet.get("publishedAt")),
            "updated_at_remote": parse_dt(snippet.get("updatedAt")),
            "like_count": optional_int(snippet, "likeCount"),
            "reply_count": reply_count,
            "is_reply": bool(is_reply or parent),
            "synced_at": timezone.now(),
        },
    )
    return True


def _comment_exists(node):
    comment_id = node.get("id") or ""
    return bool(comment_id) and YouTubeVideoComment.objects.filter(comment_id=comment_id).exists()


def _older_than_watermark(node, watermark):
    if not watermark:
        return False
    published = parse_dt((node.get("snippet") or {}).get("publishedAt"))
    return bool(published and published <= watermark)


def _sync_transcripts(channels, client, job, limits):
    failures = 0
    saved = 0
    provider = YouTubeCaptionProvider()
    external = ConfiguredTranscriptProvider()
    videos = _videos_needing_transcripts(channels, limits["transcript_videos"])
    scopes = job.granted_scopes or ""
    allowed = scopes_allow_captions(scopes)
    for video in videos:
        if allowed is False:
            record_status(
                video,
                language=video.default_language or "und",
                source=provider.name,
                status=YouTubeTranscript.Status.RESTRICTED,
                reason="Caption download needs the youtube.force-ssl scope. Connect YouTube again to grant it.",
            )
            continue
        try:
            results = provider.fetch(client, video)
        except YouTubeAPIError as exc:
            if exc.kind == "quota":
                _stage(job, "transcripts", "quota_exhausted", str(exc))
                raise
            status = YouTubeTranscript.Status.RESTRICTED if exc.kind == "permission" else YouTubeTranscript.Status.FAILED
            if exc.kind == "unavailable":
                status = YouTubeTranscript.Status.UNAVAILABLE
            record_status(
                video,
                language=video.default_language or "und",
                source=provider.name,
                status=status,
                reason=str(exc),
            )
            if exc.kind != "unavailable":
                failures += 1
            logger.info("youtube_transcript_failed video_id=%s kind=%s", video.video_id, exc.kind)
            results = []
        if not results and allowed is not False:
            record_status(
                video,
                language=video.default_language or "und",
                source=provider.name,
                status=YouTubeTranscript.Status.UNAVAILABLE,
                reason="No caption track was listed for this video.",
            )
        for result in results:
            if result.get("status") == YouTubeTranscript.Status.AVAILABLE:
                store_transcript(video, result)
                saved += 1
            else:
                store_transcript(video, result)
                if result.get("status") == YouTubeTranscript.Status.FAILED:
                    failures += 1
        needs_text = not any(result.get("text") for result in results)
        if needs_text and external.configured():
            try:
                extra = external.fetch(video)
            except YouTubeAPIError as exc:
                record_status(
                    video,
                    language=video.default_language or "und",
                    source=external.name,
                    status=YouTubeTranscript.Status.FAILED,
                    reason=str(exc),
                )
                failures += 1
            else:
                store_transcript(video, extra)
                if extra.get("status") == YouTubeTranscript.Status.AVAILABLE:
                    saved += 1
    status = "partial" if failures else "success"
    _stage(job, "transcripts", status, f"transcripts={saved} failures={failures}")


ANALYTICS_API_DISABLED = (
    "YouTube Analytics API is not enabled for this Google Cloud project. "
    "Enable it in Google Cloud Console, then sync again."
)


def _scopes_omit_analytics(channel):
    scopes = channel.granted_scopes or ""
    if not scopes:
        return False
    return "yt-analytics.readonly" not in scopes and "yt-analytics-monetary.readonly" not in scopes


def _sync_channel_analytics(channels, client, job, limits):
    end = timezone.localdate()
    start = end - timedelta(days=limits["analytics_days"])
    failures = 0
    for channel in channels:
        if _scopes_omit_analytics(channel):
            _stage(job, "channel_analytics", "permission_denied", "Analytics scope is not granted.")
            channel.analytics_authorized = False
            channel.reauthorization_required = True
            channel.save(update_fields=["analytics_authorized", "reauthorization_required"])
            return
        try:
            _analytics_daily(client, channel, start, end, "", None)
            _analytics_extras(client, channel, start, end)
            _analytics_breakdowns(client, channel, start, end)
            channel.analytics_authorized = True
            channel.save(update_fields=["analytics_authorized"])
        except YouTubeAPIError as exc:
            if exc.kind == "quota":
                _stage(job, "channel_analytics", "quota_exhausted", str(exc))
                raise
            if exc.kind == "not_configured" or exc.reason == "accessNotConfigured":
                channel.analytics_authorized = None
                channel.reauthorization_required = False
                channel.save(update_fields=["analytics_authorized", "reauthorization_required"])
                _stage(job, "channel_analytics", "unavailable", ANALYTICS_API_DISABLED)
                logger.info("youtube_analytics_not_configured channel_id=%s", channel.channel_id)
                return
            if exc.kind == "permission":
                channel.analytics_authorized = False
                channel.reauthorization_required = True
                channel.save(update_fields=["analytics_authorized", "reauthorization_required"])
                _stage(job, "channel_analytics", "permission_denied", str(exc))
                logger.info("youtube_analytics_denied channel_id=%s", channel.channel_id)
                return
            failures += 1
            logger.info("youtube_analytics_failed channel_id=%s kind=%s", channel.channel_id, exc.kind)
    _stage(job, "channel_analytics", "partial" if failures else "success", f"failures={failures}")


def _sync_video_analytics(channels, client, job, limits):
    channel_stage = (job.stages or {}).get("channel_analytics", {}).get("status")
    if channel_stage in {"permission_denied", "unavailable", "quota_exhausted"}:
        _stage(job, "video_analytics", "skipped", "Channel analytics did not succeed.")
        return
    end = timezone.localdate()
    start = end - timedelta(days=limits["analytics_days"])
    failures = 0
    for channel in channels:
        videos = _recent_videos([channel], limits["video_analytics"])
        for video in videos:
            try:
                _analytics_daily(client, channel, start, end, video.video_id, video, video_filter=True)
            except YouTubeAPIError as exc:
                if exc.kind == "quota":
                    _stage(job, "video_analytics", "quota_exhausted", str(exc))
                    raise
                if exc.kind == "permission":
                    _stage(job, "video_analytics", "permission_denied", str(exc))
                    return
                if exc.kind == "unsupported":
                    failures += 1
                    continue
                failures += 1
        try:
            _bulk_video_range(client, channel, start, end)
        except YouTubeAPIError as exc:
            if exc.kind in {"quota", "permission"}:
                _stage(job, "video_analytics", "permission_denied" if exc.kind == "permission" else "quota_exhausted", str(exc))
                if exc.kind == "quota":
                    raise
                return
            failures += 1
        _retention(client, channel, _recent_videos([channel], limits["retention_videos"]), start, end, job.mode)
    _stage(job, "video_analytics", "partial" if failures else "success", f"failures={failures}")


def _analytics_daily(client, channel, start, end, video_key, video, video_filter=False):
    params = {
        "ids": "channel==MINE",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metrics": CORE_METRICS,
        "dimensions": "day",
    }
    if video_filter:
        params["filters"] = f"video=={video_key}"
    report = client.analytics(params)
    for record in rows_from_report(report):
        day = date_from_string(record.get("day"))
        if day is None:
            continue
        save_snapshot(
            channel,
            video=video,
            video_key=video_key,
            snapshot_date=day,
            scope=ANALYTICS_DAILY,
            source="youtube_analytics_api",
            **metrics_from_record(record),
        )


def _analytics_extras(client, channel, start, end):
    for metrics, flag in (
        (IMPRESSION_METRICS, None),
        (ENGAGED_METRICS, None),
        (REVENUE_METRICS, "revenue"),
    ):
        if flag == "revenue" and channel.revenue_authorized is False:
            continue
        if flag == "revenue" and channel.granted_scopes and "yt-analytics-monetary.readonly" not in channel.granted_scopes:
            channel.revenue_authorized = False
            channel.save(update_fields=["revenue_authorized"])
            continue
        try:
            report = client.analytics(
                {
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": metrics,
                    "dimensions": "day",
                }
            )
        except YouTubeAPIError as exc:
            if flag == "revenue" and exc.kind == "permission":
                channel.revenue_authorized = False
                channel.save(update_fields=["revenue_authorized"])
                continue
            if exc.kind in {"unsupported", "permission", "unavailable"}:
                logger.info("youtube_analytics_metric_skipped metrics=%s kind=%s", metrics, exc.kind)
                continue
            raise
        if flag == "revenue":
            channel.revenue_authorized = True
            channel.save(update_fields=["revenue_authorized"])
        for record in rows_from_report(report):
            day = date_from_string(record.get("day"))
            if day is None:
                continue
            save_snapshot(
                channel,
                video_key="",
                snapshot_date=day,
                scope=ANALYTICS_DAILY,
                source="youtube_analytics_api",
                merge=True,
                **metrics_from_record(record),
            )


def _analytics_breakdowns(client, channel, start, end):
    collected = []
    queries = (
        ("insightTrafficSourceType", "views", "traffic_source"),
        ("country", "views", "country"),
        ("deviceType", "views", "device"),
    )
    for dimension, metric, label in queries:
        try:
            report = client.analytics(
                {
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": metric,
                    "dimensions": dimension,
                    "maxResults": 25,
                    "sort": f"-{metric}",
                }
            )
        except YouTubeAPIError as exc:
            if exc.kind in {"unsupported", "permission", "unavailable"}:
                logger.info("youtube_breakdown_skipped dimension=%s kind=%s", dimension, exc.kind)
                continue
            raise
        for record in rows_from_report(report):
            collected.append(
                {"dimension": label, "segment": record.get(dimension), "value": record.get(metric)}
            )
    snapshot, _created = save_snapshot(
        channel,
        video_key="",
        snapshot_date=end,
        scope=ANALYTICS_RANGE,
        source="youtube_analytics_api",
        period_start=start,
    )
    replace_breakdowns(snapshot, collected)


def _bulk_video_range(client, channel, start, end):
    start_index = 1
    while start_index <= 1000:
        try:
            report = client.analytics(
                {
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": VIDEO_METRICS,
                    "dimensions": "video",
                    "maxResults": 200,
                    "startIndex": start_index,
                    "sort": "-views",
                }
            )
        except YouTubeAPIError as exc:
            if exc.kind == "unsupported" and start_index == 1:
                return
            raise
        rows = rows_from_report(report)
        if not rows:
            return
        videos = {video.video_id: video for video in channel.videos.filter(video_id__in=[row.get("video") for row in rows])}
        for record in rows:
            video_id = str(record.get("video") or "")
            if not video_id:
                continue
            save_snapshot(
                channel,
                video=videos.get(video_id),
                video_key=video_id,
                snapshot_date=end,
                scope=ANALYTICS_RANGE,
                source="youtube_analytics_api",
                period_start=start,
                **metrics_from_record(record),
            )
        if len(rows) < 200:
            return
        start_index += 200


def _retention(client, channel, videos, start, end, mode):
    for video in videos:
        if mode != "full" and channel.snapshots.filter(video_key=video.video_id, scope=ANALYTICS_RETENTION).exists():
            continue
        try:
            report = client.analytics(
                {
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": "audienceWatchRatio,relativeRetentionPerformance",
                    "dimensions": "elapsedVideoTimeRatio",
                    "filters": f"video=={video.video_id}",
                }
            )
        except YouTubeAPIError as exc:
            if exc.kind in {"unsupported", "permission", "unavailable"}:
                logger.info("youtube_retention_skipped video_id=%s kind=%s", video.video_id, exc.kind)
                continue
            raise
        snapshot, _created = save_snapshot(
            channel,
            video=video,
            video_key=video.video_id,
            snapshot_date=end,
            scope=ANALYTICS_RETENTION,
            source="youtube_analytics_api",
            period_start=start,
        )
        rows = []
        for record in rows_from_report(report):
            ratio = record.get("elapsedVideoTimeRatio")
            if ratio is None:
                continue
            rows.append(
                {
                    "dimension": "elapsed_watch_ratio",
                    "segment": str(ratio)[:80],
                    "value": record.get("audienceWatchRatio"),
                }
            )
        replace_breakdowns(snapshot, rows)


def _recent_videos(channels, limit):
    if limit <= 0:
        return []
    ids = [channel.pk for channel in channels]
    return list(
        YouTubeVideo.objects.filter(channel_id__in=ids, availability=YouTubeVideo.Availability.AVAILABLE)
        .select_related("channel")
        .order_by("-published_at", "-synced_at")[:limit]
    )


def _videos_needing_transcripts(channels, limit):
    videos = _recent_videos(channels, max(limit * 3, limit))
    chosen = []
    for video in videos:
        fresh = video.transcripts.filter(status=YouTubeTranscript.Status.AVAILABLE, text__gt="").exists()
        if fresh:
            continue
        chosen.append(video)
        if len(chosen) >= limit:
            break
    return chosen


def _touch_account(account, channel, count):
    custom = channel.custom_url or ""
    account.external_id = account.external_id or channel.channel_id
    account.display_name = channel.title or account.display_name
    account.username = custom or account.username
    if custom:
        account.profile_url = f"https://www.youtube.com/{custom.lstrip('/')}"
    elif channel.channel_id:
        account.profile_url = f"https://www.youtube.com/channel/{channel.channel_id}"
    thumb = preferred_thumbnail(channel.thumbnail_urls)
    if thumb:
        account.avatar_url = thumb
    account.last_synced_at = timezone.now()
    account.last_error = ""
    account.status_note = f"YouTube sync saved {count} channel{'s' if count != 1 else ''}."
    account.save()


def _touch_dashboard(account, channel):
    if channel.subscriber_count is None and channel.view_count is None:
        return
    followers = channel.subscriber_count
    if followers is None:
        previous = account.snapshots.order_by("-captured_at").first()
        followers = previous.followers if previous else 0
    _upsert_snapshot(
        account,
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=int(followers or 0),
            content_count=int(channel.video_count or channel.videos.count()),
            views=int(channel.view_count or 0),
            likes=int(channel.summed_video_likes or 0),
            comments=int(channel.summed_video_comments or 0),
        ),
    )


def _primary(channels):
    def rank(channel):
        if channel.subscriber_count is None:
            return -1
        return channel.subscriber_count

    return max(channels, key=rank)


def _apply_scope_flags(channels, scopes):
    if not scopes:
        return
    analytics = "yt-analytics.readonly" in scopes or "yt-analytics-monetary.readonly" in scopes
    revenue = "yt-analytics-monetary.readonly" in scopes
    for channel in channels:
        channel.analytics_authorized = analytics
        channel.revenue_authorized = revenue
        channel.granted_scopes = scopes
        channel.save(update_fields=["analytics_authorized", "revenue_authorized", "granted_scopes"])


def _mark_reauth(account, required):
    account.youtube_channels.update(reauthorization_required=required)
    if required:
        logger.info("youtube_reauthorization_required account_id=%s", account.pk)


def _stage(job, name, status, detail):
    stages = dict(job.stages or {})
    stages[name] = {"status": status, "detail": (detail or "")[:400]}
    job.stages = stages
    job.save(update_fields=["stages"])


def _fail_open_stage(job, exc):
    mapping = {
        "quota": "quota_exhausted",
        "permission": "permission_denied",
        "unavailable": "unavailable",
        "retryable": "failed",
    }
    status = mapping.get(exc.kind, "failed")
    for name in STAGES:
        current = (job.stages or {}).get(name) or {}
        if not current:
            _stage(job, name, status, str(exc))
            return
        if current.get("status") in {"", "pending"}:
            _stage(job, name, status, str(exc))
            return
    open_name = STAGES[-1]
    for name in STAGES:
        current = (job.stages or {}).get(name) or {}
        if current.get("status") not in {"success", "skipped"}:
            open_name = name
    if (job.stages or {}).get(open_name, {}).get("status") in {"success", "skipped"}:
        return
    _stage(job, open_name, status, str(exc))


def _overall(job):
    statuses = [item.get("status") for item in (job.stages or {}).values()]
    if any(status == "quota_exhausted" for status in statuses):
        return "quota_exhausted", "failed"
    if any(status == "permission_denied" for status in statuses):
        return "permission_denied", "partial" if job.videos_upserted else "failed"
    if any(status in {"failed", "partial", "unavailable"} for status in statuses):
        return "partial", "partial" if job.videos_upserted or _stage_ok(job, "channel") else "failed"
    return "success", "completed"


def _stage_ok(job, name):
    return (job.stages or {}).get(name, {}).get("status") == "success"


def _stage_errors(job):
    parts = []
    for name, item in (job.stages or {}).items():
        if item.get("status") not in {"success", "skipped"} and item.get("detail"):
            parts.append(f"{name}: {item['detail']}")
    return " ".join(parts)[:1000]


def _finish(job, status, outcome, error):
    job.status = status
    job.outcome = outcome
    job.last_error = error or ""
    job.finished_at = timezone.now()
    sync_status = {
        "completed": YouTubeChannel.SyncStatus.SUCCEEDED,
        "partial": YouTubeChannel.SyncStatus.PARTIAL,
        "pending": YouTubeChannel.SyncStatus.RUNNING,
        "failed": YouTubeChannel.SyncStatus.FAILED,
    }.get(status, YouTubeChannel.SyncStatus.FAILED)
    job.account.youtube_channels.update(
        sync_status=sync_status,
        last_error="" if status == "completed" else (error or "")[:1000],
    )
    if status in {"completed", "partial"}:
        job.account.youtube_channels.update(last_success_at=timezone.now())
    if error and status == "failed":
        job.account.last_error = error[:1000]
        job.account.save(update_fields=["last_error"])
    logger.info("youtube_sync_status job_id=%s status=%s outcome=%s", job.pk, status, outcome)
