import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Avg, Max, Sum
from django.utils import timezone

from networks.instagram.client import (
    COMMENT_FIELDS,
    MEDIA_FIELDS,
    MEDIA_METRICS,
    PROFILE_FIELDS,
    REEL_METRICS,
    USER_DAY_METRICS,
    USER_TOTAL_METRICS,
    InstagramAPIError,
    InstagramClient,
)
from networks.instagram.parse import (
    METRIC_FIELDS,
    as_optional_int,
    breakdown_rows,
    content_type,
    insight_day,
    metric_points,
    parse_dt,
    title_from_caption,
)
from networks.instagram.snapshots import refresh_follower_deltas, replace_breakdowns, save_snapshot
from networks.instagram_models import InstagramComment, InstagramMedia, InstagramProfile, InstagramSnapshot
from networks.fetchers import AudienceSlice, FetchedSnapshot
from networks.models import ContentItem
from networks.oauth import ensure_access_token
from networks.sync import _replace_breakdowns, _upsert_snapshot

logger = logging.getLogger("networks.instagram")

INSIGHTS_SCOPES = "instagram_business_manage_insights"
COMMENTS_SCOPE = "instagram_business_manage_comments"


def sync_instagram(account, job):
    from networks.instagram_models import InstagramSyncJob

    profile_row = None
    stages = {}
    notes = []
    try:
        token = ensure_access_token(account)
        client = InstagramClient(token)
        profile_row = _sync_profile(account, job, client, stages)
        if profile_row is None:
            return _finish(job, stages, notes, profile_row, client.calls)
        _sync_media(profile_row, job, client, stages, notes)
        _sync_comments(profile_row, job, client, stages, notes)
        _sync_user_insights(profile_row, job, client, stages, notes)
        _sync_media_insights(profile_row, job, client, stages, notes)
        _sync_demographics(profile_row, client, stages, notes)
        refresh_follower_deltas(profile_row)
        _aggregates(profile_row)
        _mirror_account(account, profile_row)
        from creators.scraped import attach_connected_account

        attach_connected_account(account)
    except InstagramAPIError as exc:
        stages["profile"] = stages.get("profile") or _stage(exc)
        notes.append(str(exc))
        logger.info("instagram_sync_error account_id=%s kind=%s", account.pk, exc.kind)
    except Exception as exc:
        stages["profile"] = {"status": "failed", "kind": "permanent"}
        notes.append(str(exc)[:500])
        logger.exception("instagram_sync_unexpected account_id=%s", account.pk)
    calls = client.calls if "client" in locals() else job.api_calls
    return _finish(job, stages, notes, profile_row, calls, job_model=InstagramSyncJob)


def _sync_profile(account, job, client, stages):
    payload = client.get("me", {"fields": PROFILE_FIELDS})
    ig_id = str(payload.get("user_id") or payload.get("id") or "")
    if not ig_id:
        raise InstagramAPIError("Instagram did not return this account.", kind="permanent")
    account_type = (payload.get("account_type") or "").upper()
    if account_type == "PERSONAL":
        raise InstagramAPIError(
            "This Instagram account is personal. Switch it to a professional account, then connect again.",
            kind="permanent",
        )
    followers = as_optional_int(payload.get("followers_count"))
    follows = as_optional_int(payload.get("follows_count"))
    media_count = as_optional_int(payload.get("media_count"))
    username = payload.get("username") or ""
    profile, _created = InstagramProfile.objects.update_or_create(
        account=account,
        defaults={
            "ig_user_id": ig_id,
            "username": username,
            "name": payload.get("name") or "",
            "biography": payload.get("biography") or "",
            "website": payload.get("website") or "",
            "account_type": account_type,
            "profile_picture_url": payload.get("profile_picture_url") or "",
            "followers_count": followers,
            "follows_count": follows,
            "media_count": media_count,
            "sync_status": InstagramProfile.SyncStatus.RUNNING,
            "granted_scopes": job.granted_scopes or "",
            "last_error": "",
        },
    )
    save_snapshot(
        profile,
        snapshot_date=timezone.localdate(),
        scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE,
        source="instagram_user",
        values={"followers": followers, "follows": follows, "media_count": media_count},
    )
    stages["profile"] = {"status": "success"}
    return profile


def _sync_media(profile, job, client, stages, notes):
    limit = _limit(job, "INSTAGRAM_MAX_MEDIA", "INSTAGRAM_INCREMENTAL_MEDIA", 500, 50)
    try:
        rows = client.paged(f"{profile.ig_user_id}/media", {"fields": MEDIA_FIELDS}, limit)
    except InstagramAPIError as exc:
        stages["media"] = _stage(exc)
        notes.append(f"media: {exc}")
        return
    now = timezone.now()
    upserted = 0
    for item in rows:
        media_id = str(item.get("id") or "")
        if not media_id:
            continue
        likes = item["like_count"] if "like_count" in item else None
        comments = item["comments_count"] if "comments_count" in item else None
        product = item.get("media_product_type") or ""
        media_type = item.get("media_type") or ""
        thumbnail = item.get("thumbnail_url") or ""
        media, _created = InstagramMedia.objects.update_or_create(
            profile=profile,
            media_id=media_id,
            defaults={
                "caption": item.get("caption") or "",
                "media_type": media_type,
                "media_product_type": product,
                "permalink": item.get("permalink") or "",
                "thumbnail_url": thumbnail,
                "published_at": parse_dt(item.get("timestamp")),
                "like_count": as_optional_int(likes),
                "comments_count": as_optional_int(comments),
                "is_comment_enabled": item.get("is_comment_enabled"),
                "synced_at": now,
            },
        )
        _mirror_content(profile.account, media)
        upserted += 1
    job.media_seen = len(rows)
    job.media_upserted = upserted
    stages["media"] = {"status": "success", "count": upserted}
    if upserted < (profile.media_count or 0) and job.mode == "full":
        notes.append("media: the catalog hit the configured media limit before the lifetime media count.")


def _sync_comments(profile, job, client, stages, notes):
    if _scopes_omit(job, profile, COMMENTS_SCOPE):
        stages["comments"] = {"status": "skipped", "reason": "scope_missing"}
        profile.comments_authorized = False
        profile.reauthorization_required = True
        notes.append("comments: reconnect Instagram and allow comment access.")
        return
    limit_media = _limit(job, "INSTAGRAM_FULL_COMMENT_MEDIA", "INSTAGRAM_COMMENT_MEDIA", 40, 15)
    per_media = int(getattr(settings, "INSTAGRAM_COMMENT_LIMIT", 100) or 100)
    media_rows = list(profile.media.order_by("-published_at")[:limit_media])
    saved = 0
    withheld = 0
    denied = False
    for media in media_rows:
        if media.is_comment_enabled is False:
            media.comments_sync_status = "disabled"
            media.comments_sync_error = ""
            media.comments_synced_at = timezone.now()
            media.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
            continue
        try:
            rows = client.paged(
                f"{media.media_id}/comments",
                {"fields": COMMENT_FIELDS},
                per_media,
            )
        except InstagramAPIError as exc:
            media.comments_sync_status = exc.kind
            media.comments_sync_error = str(exc)[:500]
            media.comments_synced_at = timezone.now()
            media.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
            if exc.kind == "permission":
                denied = True
                break
            if exc.kind == "retryable":
                stages["comments"] = _stage(exc)
                notes.append(f"comments: {exc}")
                return
            notes.append(f"comments {media.media_id}: {exc}")
            continue
        flat = []
        for row in rows:
            flat.append((row, ""))
            replies = ((row.get("replies") or {}).get("data")) or []
            for reply in replies:
                flat.append((reply, str(row.get("id") or "")))
        now = timezone.now()
        for row, parent_id in flat:
            comment_id = str(row.get("id") or "")
            if not comment_id:
                continue
            InstagramComment.objects.update_or_create(
                comment_id=comment_id,
                defaults={
                    "media": media,
                    "parent_comment_id": str(row.get("parent_id") or parent_id or ""),
                    "username": row.get("username") or "",
                    "text": row.get("text") or "",
                    "published_at": parse_dt(row.get("timestamp")),
                    "like_count": as_optional_int(row.get("like_count")) if "like_count" in row else None,
                    "is_reply": bool(row.get("parent_id") or parent_id),
                    "synced_at": now,
                },
            )
            saved += 1
        if (media.comments_count or 0) > 0 and not flat:
            withheld += 1
            media.comments_sync_status = "unavailable"
            media.comments_sync_error = "Instagram reported comments but did not return the text."
        else:
            media.comments_sync_status = "available"
            media.comments_sync_error = ""
        media.comments_synced_at = now
        media.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
    job.comments_upserted = saved
    if denied:
        stages["comments"] = {"status": "permission_denied"}
        profile.comments_authorized = False
        if _ask_reconnect(job, profile, COMMENTS_SCOPE):
            profile.reauthorization_required = True
        notes.append("comments: Instagram refused comment access. Public media counts are unchanged.")
        return
    profile.comments_authorized = True
    if withheld:
        stages["comments"] = {"status": "unavailable", "count": saved, "withheld": withheld}
        notes.append("comments: Instagram reported comments on some posts but did not return the text.")
        return
    stages["comments"] = {"status": "success", "count": saved}


def _sync_user_insights(profile, job, client, stages, notes):
    if _scopes_omit(job, profile, INSIGHTS_SCOPES):
        stages["insights"] = {"status": "skipped", "reason": "scope_missing"}
        profile.insights_authorized = False
        profile.reauthorization_required = True
        notes.append("insights: reconnect Instagram and allow insights.")
        return
    days = int(getattr(settings, "INSTAGRAM_INSIGHTS_DAYS", 28) or 28)
    until = timezone.localdate()
    since = until - timedelta(days=days)
    params = {
        "period": "day",
        "since": _unix(since),
        "until": _unix(until + timedelta(days=1)),
    }
    try:
        daily = client.insights(f"{profile.ig_user_id}/insights", USER_DAY_METRICS, params)
        totals = client.insights(
            f"{profile.ig_user_id}/insights",
            USER_TOTAL_METRICS,
            {**params, "metric_type": "total_value"},
        )
    except InstagramAPIError as exc:
        stages["insights"] = _stage(exc)
        if exc.kind == "permission":
            profile.insights_authorized = False
            if _ask_reconnect(job, profile, INSIGHTS_SCOPES):
                profile.reauthorization_required = True
            notes.append("insights: Instagram refused insights. Lifetime followers stay on the profile.")
        else:
            notes.append(f"insights: {exc}")
        return
    by_day = {}
    for point in metric_points(daily):
        day = insight_day(point["end_time"])
        field = METRIC_FIELDS.get(point["name"])
        if not day or not field:
            continue
        by_day.setdefault(day, {})[field] = as_optional_int(point["value"])
    for day, values in by_day.items():
        save_snapshot(
            profile,
            snapshot_date=day,
            scope=InstagramSnapshot.Scope.INSIGHTS_DAILY,
            source="instagram_insights",
            values=values,
        )
    window = {}
    for point in metric_points(totals):
        field = METRIC_FIELDS.get(point["name"])
        if field:
            window[field] = as_optional_int(point["value"])
    if window:
        save_snapshot(
            profile,
            snapshot_date=until,
            scope=InstagramSnapshot.Scope.INSIGHTS_RANGE,
            source="instagram_insights",
            values=window,
            period_start=since,
        )
    unsupported = (daily.get("unsupported") or []) + (totals.get("unsupported") or [])
    profile.insights_authorized = True
    stages["insights"] = {"status": "success", "unsupported": unsupported}
    if unsupported:
        notes.append("insights: Instagram does not provide " + ", ".join(unsupported) + " for this account.")


def _sync_media_insights(profile, job, client, stages, notes):
    if stages.get("insights", {}).get("status") in {"permission_denied", "skipped"}:
        stages["media_insights"] = {"status": "skipped"}
        return
    limit = _limit(job, "INSTAGRAM_FULL_MEDIA_INSIGHTS", "INSTAGRAM_MEDIA_INSIGHTS", 40, 25)
    denied = False
    unsupported = []
    for media in profile.media.order_by("-published_at")[:limit]:
        metrics = REEL_METRICS if (media.media_product_type or "").upper() == "REELS" or media.media_type == "VIDEO" else MEDIA_METRICS
        try:
            payload = client.insights(f"{media.media_id}/insights", metrics, {"period": "lifetime"})
        except InstagramAPIError as exc:
            if exc.kind == "permission":
                denied = True
                break
            notes.append(f"media insights {media.media_id}: {exc}")
            continue
        values = {}
        available = {}
        media_fields = {field.name for field in media._meta.concrete_fields}
        for point in metric_points(payload):
            field = METRIC_FIELDS.get(point["name"])
            amount = as_optional_int(point["value"])
            available[point["name"]] = amount is not None
            if field in media_fields and amount is not None:
                values[field] = amount
        for name in payload.get("unsupported") or []:
            available[name] = False
            if name not in unsupported:
                unsupported.append(name)
        if values:
            for field, amount in values.items():
                setattr(media, field, amount)
            media.insights_available = available
            media.save(update_fields=[*values.keys(), "insights_available"])
            _mirror_content(profile.account, media)
            save_snapshot(
                profile,
                media=media,
                media_key=media.media_id,
                snapshot_date=timezone.localdate(),
                scope=InstagramSnapshot.Scope.INSIGHTS_RANGE,
                source="instagram_media_insights",
                values=values,
            )
    if denied:
        stages["media_insights"] = {"status": "permission_denied"}
        profile.insights_authorized = False
        notes.append("media insights: Instagram refused insights on posts.")
        return
    stages["media_insights"] = {"status": "success", "unsupported": unsupported}


def _sync_demographics(profile, client, stages, notes):
    if stages.get("insights", {}).get("status") in {"permission_denied", "skipped"}:
        stages["demographics"] = {"status": "skipped"}
        return
    rows = []
    for breakdown in ("country", "gender", "age"):
        try:
            payload = client.get(
                f"{profile.ig_user_id}/insights",
                {
                    "metric": "follower_demographics",
                    "period": "lifetime",
                    "metric_type": "total_value",
                    "timeframe": "this_month",
                    "breakdown": breakdown,
                },
            )
        except InstagramAPIError as exc:
            if exc.kind in {"unsupported", "permission"}:
                notes.append(f"demographics {breakdown}: {exc}")
                continue
            stages["demographics"] = _stage(exc)
            notes.append(f"demographics: {exc}")
            return
        for point in metric_points(payload):
            rows.extend(breakdown_rows(point["breakdowns"]))
    if not rows:
        stages["demographics"] = {"status": "unavailable"}
        return
    snapshot, writable = save_snapshot(
        profile,
        media_key="demographics",
        snapshot_date=timezone.localdate(),
        scope=InstagramSnapshot.Scope.INSIGHTS_RANGE,
        source="instagram_demographics",
        values={},
        period_start=timezone.localdate().replace(day=1),
    )
    replace_breakdowns(snapshot, rows, writable=writable or snapshot.snapshot_date == timezone.localdate())
    stages["demographics"] = {"status": "success", "segments": len(rows)}


def _aggregates(profile):
    media = profile.media.all()
    known_likes = media.filter(like_count__isnull=False)
    totals = known_likes.aggregate(total=Sum("like_count"), average=Avg("like_count"), top=Max("like_count"))
    profile.summed_likes = totals["total"]
    profile.average_likes = totals["average"]
    profile.hidden_like_count = media.filter(like_count__isnull=True).count()
    profile.summed_comments = media.aggregate(total=Sum("comments_count"))["total"]
    view_known = media.filter(views__isnull=False)
    profile.summed_views = view_known.aggregate(total=Sum("views"))["total"] if view_known.exists() else None
    reach_known = media.filter(reach__isnull=False)
    profile.summed_reach = reach_known.aggregate(total=Sum("reach"))["total"] if reach_known.exists() else None
    top = known_likes.order_by("-like_count", "media_id").first()
    profile.most_liked_media_id = top.media_id if top else ""
    latest = media.order_by("-published_at", "media_id").first()
    profile.latest_media_id = latest.media_id if latest else ""
    now = timezone.now()
    for field, days in (
        ("media_last_7_days", 7),
        ("media_last_30_days", 30),
        ("media_last_90_days", 90),
        ("media_last_365_days", 365),
    ):
        setattr(profile, field, media.filter(published_at__gte=now - timedelta(days=days)).count())


def _mirror_account(account, profile):
    account.external_id = profile.ig_user_id
    account.display_name = profile.name or profile.username or "Instagram"
    account.username = profile.username
    account.profile_url = f"https://www.instagram.com/{profile.username}/" if profile.username else ""
    account.avatar_url = profile.profile_picture_url
    account.last_error = ""
    account.last_synced_at = timezone.now()
    account.status_note = "Professional Instagram account. Insights are a separate daily series from lifetime followers."
    account.save()
    window = (
        profile.snapshots.filter(media_key="", scope=InstagramSnapshot.Scope.INSIGHTS_RANGE)
        .order_by("-snapshot_date")
        .first()
    )
    demo = (
        profile.snapshots.filter(media_key="demographics")
        .prefetch_related("breakdowns")
        .order_by("-snapshot_date")
        .first()
    )
    slices = []
    if demo:
        for row in demo.breakdowns.all():
            if row.dimension not in {"country", "gender"} or row.value is None:
                continue
            slices.append(
                AudienceSlice(
                    dimension=row.dimension,
                    segment=row.segment,
                    label=row.segment,
                    count=int(row.value),
                )
            )
    saved = _upsert_snapshot(
        account,
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=profile.followers_count or 0,
            following=profile.follows_count or 0,
            content_count=profile.media_count or profile.media.count(),
            views=(window.views if window and window.views is not None else 0),
            likes=(window.likes if window and window.likes is not None else profile.summed_likes or 0),
            comments=(window.comments if window and window.comments is not None else profile.summed_comments or 0),
            shares=(window.shares if window and window.shares is not None else 0),
            reach=(window.reach if window and window.reach is not None else 0),
            breakdowns=slices,
        ),
    )
    _replace_breakdowns(saved, FetchedSnapshot(captured_at=timezone.now(), breakdowns=slices))


def _mirror_content(account, media):
    defaults = {
        "title": title_from_caption(media.caption),
        "caption": media.caption,
        "content_type": content_type(media.media_type, media.media_product_type),
        "url": media.permalink,
        "thumbnail_url": media.thumbnail_url,
        "published_at": media.published_at,
    }
    if media.like_count is not None:
        defaults["likes"] = media.like_count
    if media.comments_count is not None:
        defaults["comments"] = media.comments_count
    if media.views is not None:
        defaults["views"] = media.views
    if media.shares is not None:
        defaults["shares"] = media.shares
    if media.saved is not None:
        defaults["saves"] = media.saved
    ContentItem.objects.update_or_create(account=account, external_id=media.media_id, defaults=defaults)


def _finish(job, stages, notes, profile, calls, job_model=None):
    from networks.instagram_models import InstagramSyncJob

    job_model = job_model or InstagramSyncJob
    statuses = [item.get("status") for item in stages.values()]
    profile_stage = stages.get("profile") or {}
    if profile_stage.get("status") != "success":
        job.status = job_model.Status.FAILED
        kind = profile_stage.get("kind")
        job.outcome = {
            "retryable": job_model.Outcome.RETRYABLE,
            "permission": job_model.Outcome.PERMISSION_DENIED,
            "token": job_model.Outcome.PERMISSION_DENIED,
        }.get(kind, job_model.Outcome.PERMANENT)
    elif any(item.get("kind") == "retryable" or item.get("status") == "retryable" for item in stages.values()):
        job.status = job_model.Status.FAILED
        job.outcome = job_model.Outcome.RETRYABLE
    elif any(status in {"permission_denied", "unavailable", "skipped", "failed"} for status in statuses):
        job.status = job_model.Status.PARTIAL
        job.outcome = job_model.Outcome.PARTIAL
    else:
        job.status = job_model.Status.COMPLETED
        job.outcome = job_model.Outcome.SUCCESS
    job.stages = stages
    job.api_calls = calls
    job.last_error = "" if job.outcome == job_model.Outcome.SUCCESS else " ".join(notes)[:2000]
    job.finished_at = timezone.now()
    job.save()
    if profile is not None:
        if job.status == job_model.Status.COMPLETED:
            profile.sync_status = InstagramProfile.SyncStatus.SUCCEEDED
        elif job.status == job_model.Status.PARTIAL:
            profile.sync_status = InstagramProfile.SyncStatus.PARTIAL
        else:
            profile.sync_status = InstagramProfile.SyncStatus.FAILED
        profile.last_error = job.last_error
        if job.status in {job_model.Status.COMPLETED, job_model.Status.PARTIAL}:
            profile.last_success_at = timezone.now()
        profile.save()
    logger.info(
        "instagram_sync_done job_id=%s account_id=%s status=%s media=%s calls=%s",
        job.pk,
        job.account_id,
        job.status,
        job.media_upserted,
        job.api_calls,
    )
    return job


def _stage(exc):
    status = {
        "permission": "permission_denied",
        "token": "permission_denied",
        "unsupported": "unavailable",
        "retryable": "retryable",
    }.get(exc.kind, "failed")
    return {"status": status, "kind": exc.kind, "message": str(exc)[:300]}


def _scopes_omit(job, profile, scope):
    granted = (job.granted_scopes or profile.granted_scopes or "").replace(",", " ")
    if not granted.strip():
        return False
    return scope not in granted.split()


def _ask_reconnect(job, profile, scope):
    granted = (job.granted_scopes or profile.granted_scopes or "").replace(",", " ")
    if not granted.strip():
        return True
    return scope not in granted.split()


def _limit(job, full_name, incremental_name, full_default, incremental_default):
    if job.mode == "full":
        return int(getattr(settings, full_name, full_default) or full_default)
    return int(getattr(settings, incremental_name, incremental_default) or incremental_default)


def _unix(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return int(start.timestamp())
