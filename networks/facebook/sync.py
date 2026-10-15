import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from networks.facebook.client import (
    ACCOUNT_FIELDS,
    COMMENT_FIELDS,
    PAGE_DAY_METRICS,
    PAGE_FIELDS,
    POST_FIELDS,
    POST_METRICS,
    VIDEO_METRICS,
    FacebookAPIError,
    FacebookClient,
)
from networks.facebook.parse import (
    LEGACY_SUBSTITUTES,
    apply_metric,
    as_optional_int,
    bucket_values,
    content_type,
    dict_rows,
    insight_day,
    is_video,
    metric_points,
    parse_dt,
    share_count,
    summary_count,
    title_from_message,
)
from networks.facebook.snapshots import refresh_follower_deltas, replace_breakdowns, save_snapshot
from networks.facebook_models import FacebookComment, FacebookPage, FacebookPost, FacebookSnapshot
from networks.fetchers import AudienceSlice, FetchedSnapshot
from networks.models import ContentItem
from networks.oauth import ensure_access_token
from networks.sync import _replace_breakdowns, _upsert_snapshot

logger = logging.getLogger("networks.facebook")

INSIGHTS_SCOPE = "pages_read_engagement"
DEMOGRAPHIC_METRICS = (
    ("page_follows_country", "country"),
    ("page_follows_city", "city"),
    ("page_fans_gender_age", "gender_age"),
)


def sync_facebook(account, job):
    from networks.facebook_models import FacebookSyncJob

    pages = []
    stages = {}
    notes = []
    calls = 0
    try:
        token = ensure_access_token(account)
        user_client = FacebookClient(token)
        calls += 0
        listed = user_client.paged("me/accounts", {"fields": ACCOUNT_FIELDS}, _setting("FACEBOOK_MAX_PAGES", 25))
        calls += user_client.calls
        if not listed:
            raise FacebookAPIError(
                "No Facebook Page was found. Connect a Page you manage. Personal profiles do not share these insights.",
                kind="permanent",
            )
        listed = _connected_rows(account, listed, notes)
        job.pages_seen = len(listed)
        stages["pages"] = {"status": "success", "count": len(listed)}
        for row in listed:
            page_token = row.get("access_token") or ""
            page_id = str(row.get("id") or "")
            if not page_id or not page_token:
                notes.append(f"page {page_id or '?'}: Facebook did not return a page token.")
                stages[f"{page_id}:page"] = {"status": "failed"}
                continue
            page_client = FacebookClient(page_token)
            page = _sync_page(account, job, page_client, row, stages, notes)
            calls += page_client.calls
            if page is not None:
                pages.append(page)
        if not pages:
            raise FacebookAPIError("Facebook did not return a usable Page.", kind="permanent")
        primary = max(pages, key=lambda item: item.followers_count or 0)
        for page in pages:
            page.is_primary = page.pk == primary.pk
        FacebookPage.objects.bulk_update(pages, ["is_primary"])
        _drop_other_pages(account, pages)
        _mirror_account(account, primary, len(pages))
    except FacebookAPIError as exc:
        stages["pages"] = stages.get("pages") or _stage(exc)
        notes.append(str(exc))
        logger.info("facebook_sync_error account_id=%s kind=%s", account.pk, exc.kind)
    except Exception as exc:
        stages["pages"] = {"status": "failed", "kind": "permanent"}
        notes.append(str(exc)[:500])
        logger.exception("facebook_sync_unexpected account_id=%s", account.pk)
    return _finish(job, stages, notes, pages, calls, job_model=FacebookSyncJob)


def _connected_rows(account, listed, notes):
    """Sync only the Page this connection already stores.

    A first sync, before a Page id is saved, keeps the same single Page the
    light sync would connect: the one with the most followers.
    """
    connected = (account.external_id or "").strip()
    if connected.startswith("sample"):
        connected = ""
    if connected:
        chosen = [row for row in listed if str(row.get("id") or "") == connected]
        if not chosen:
            raise FacebookAPIError(
                "The connected Facebook Page was not returned for this login. Connect that Page again.",
                kind="permanent",
            )
        skipped = [str(row.get("id") or "") for row in listed if str(row.get("id") or "") != connected]
        if skipped:
            notes.append("Skipped Pages that are not connected: " + ", ".join(skipped) + ".")
            logger.info("facebook_sync_skipped_pages account_id=%s pages=%s", account.pk, ",".join(skipped))
        return chosen
    chosen = max(listed, key=lambda row: as_optional_int(row.get("followers_count") or row.get("fan_count")) or 0)
    return [chosen]


def _drop_other_pages(account, pages):
    kept = {page.page_id for page in pages}
    stale = FacebookPage.objects.filter(account=account).exclude(page_id__in=kept)
    stale_post_ids = list(FacebookPost.objects.filter(page__in=stale).values_list("post_id", flat=True))
    if stale_post_ids:
        ContentItem.objects.filter(account=account, external_id__in=stale_post_ids).delete()
    stale.delete()


def _sync_page(account, job, client, row, stages, notes):
    page_id = str(row.get("id") or "")
    try:
        detail = client.get(page_id, {"fields": PAGE_FIELDS})
    except FacebookAPIError as exc:
        stages[f"{page_id}:page"] = _stage(exc)
        notes.append(f"page {page_id}: {exc}")
        return None
    followers = as_optional_int(detail.get("followers_count"))
    if followers is None and "followers_count" not in detail:
        followers = as_optional_int(row.get("followers_count"))
    fans = as_optional_int(detail.get("fan_count"))
    if fans is None and "fan_count" not in detail:
        fans = as_optional_int(row.get("fan_count"))
    picture = ((detail.get("picture") or row.get("picture") or {}).get("data") or {}).get("url") or ""
    page, _created = FacebookPage.objects.update_or_create(
        account=account,
        page_id=page_id,
        defaults={
            "name": detail.get("name") or row.get("name") or "",
            "username": detail.get("username") or "",
            "about": detail.get("about") or "",
            "category": detail.get("category") or "",
            "link": detail.get("link") or row.get("link") or "",
            "picture_url": picture,
            "followers_count": followers,
            "fan_count": fans,
            "sync_status": FacebookPage.SyncStatus.RUNNING,
            "granted_scopes": job.granted_scopes or "",
            "last_error": "",
        },
    )
    save_snapshot(
        page,
        snapshot_date=timezone.localdate(),
        scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE,
        source="facebook_page",
        values={"followers": followers, "fans": fans},
    )
    stages[f"{page_id}:page"] = {"status": "success"}
    _sync_posts(page, job, client, stages, notes)
    _sync_comments(page, job, client, stages, notes)
    _sync_insights(page, job, client, stages, notes)
    _sync_post_insights(page, job, client, stages, notes)
    _sync_demographics(page, client, stages, notes)
    refresh_follower_deltas(page)
    _aggregates(page)
    return page


def _sync_posts(page, job, client, stages, notes):
    limit = _limit(job, "FACEBOOK_MAX_POSTS", "FACEBOOK_INCREMENTAL_POSTS", 500, 50)
    try:
        rows = client.paged(f"{page.page_id}/posts", {"fields": POST_FIELDS}, limit)
    except FacebookAPIError as exc:
        stages[f"{page.page_id}:posts"] = _stage(exc)
        notes.append(f"posts {page.page_id}: {exc}")
        return
    now = timezone.now()
    upserted = 0
    for item in rows:
        post_id = str(item.get("id") or "")
        if not post_id:
            continue
        post, _created = FacebookPost.objects.update_or_create(
            page=page,
            post_id=post_id,
            defaults={
                "message": item.get("message") or "",
                "status_type": item.get("status_type") or "",
                "permalink": item.get("permalink_url") or "",
                "thumbnail_url": item.get("full_picture") or "",
                "published_at": parse_dt(item.get("created_time")),
                "reaction_count": summary_count(item, "reactions"),
                "like_count": summary_count(item, "likes"),
                "comment_count": summary_count(item, "comments"),
                "share_count": share_count(item),
                "synced_at": now,
            },
        )
        _mirror_content(page.account, post)
        upserted += 1
    job.posts_seen += len(rows)
    job.posts_upserted += upserted
    stages[f"{page.page_id}:posts"] = {"status": "success", "count": upserted}


def _sync_comments(page, job, client, stages, notes):
    limit_posts = _limit(job, "FACEBOOK_FULL_COMMENT_POSTS", "FACEBOOK_COMMENT_POSTS", 40, 15)
    per_post = int(getattr(settings, "FACEBOOK_COMMENT_LIMIT", 100) or 100)
    saved = 0
    withheld = 0
    denied = False
    for post in page.posts.order_by("-published_at")[:limit_posts]:
        try:
            rows = client.paged(
                f"{post.post_id}/comments",
                {"fields": COMMENT_FIELDS, "filter": "stream"},
                per_post,
            )
        except FacebookAPIError as exc:
            if exc.kind == "unsupported":
                try:
                    rows = client.paged(f"{post.post_id}/comments", {"fields": COMMENT_FIELDS}, per_post)
                except FacebookAPIError as retry_exc:
                    exc = retry_exc
                    rows = None
            else:
                rows = None
            if rows is None:
                post.comments_sync_status = exc.kind
                post.comments_sync_error = str(exc)[:500]
                post.comments_synced_at = timezone.now()
                post.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
                if exc.kind == "permission":
                    denied = True
                    break
                if exc.kind == "retryable":
                    stages[f"{page.page_id}:comments"] = _stage(exc)
                    notes.append(f"comments {page.page_id}: {exc}")
                    return
                notes.append(f"comments {post.post_id}: {exc}")
                continue
        now = timezone.now()
        written = 0
        for row in rows:
            comment_id = str(row.get("id") or "")
            if not comment_id:
                continue
            parent = row.get("parent") or {}
            FacebookComment.objects.update_or_create(
                comment_id=comment_id,
                defaults={
                    "post": post,
                    "parent_comment_id": str(parent.get("id") or ""),
                    "author_name": ((row.get("from") or {}).get("name") or ""),
                    "message": row.get("message") or "",
                    "published_at": parse_dt(row.get("created_time")),
                    "like_count": as_optional_int(row.get("like_count")) if "like_count" in row else None,
                    "reply_count": as_optional_int(row.get("comment_count")) if "comment_count" in row else None,
                    "is_reply": bool(parent.get("id")),
                    "synced_at": now,
                },
            )
            written += 1
            saved += 1
        if (post.comment_count or 0) > 0 and written == 0:
            withheld += 1
            post.comments_sync_status = "unavailable"
            post.comments_sync_error = "Facebook reported comments but did not return the text."
        else:
            post.comments_sync_status = "available"
            post.comments_sync_error = ""
        post.comments_synced_at = now
        post.save(update_fields=["comments_sync_status", "comments_sync_error", "comments_synced_at"])
    job.comments_upserted += saved
    key = f"{page.page_id}:comments"
    if denied:
        stages[key] = {"status": "permission_denied"}
        if _ask_reconnect(job, page, INSIGHTS_SCOPE):
            page.reauthorization_required = True
        notes.append(f"comments {page.page_id}: Facebook refused comment access. Public counts are unchanged.")
        return
    if withheld:
        stages[key] = {"status": "unavailable", "count": saved, "withheld": withheld}
        notes.append(f"comments {page.page_id}: Facebook reported comments on some posts but did not return the text.")
        return
    stages[key] = {"status": "success", "count": saved}


def _sync_insights(page, job, client, stages, notes):
    if _scopes_omit(job, page, INSIGHTS_SCOPE):
        stages[f"{page.page_id}:insights"] = {"status": "skipped", "reason": "scope_missing"}
        page.insights_authorized = False
        page.reauthorization_required = True
        notes.append(f"insights {page.page_id}: reconnect Facebook and allow Page insights.")
        return
    days = int(getattr(settings, "FACEBOOK_INSIGHTS_DAYS", 28) or 28)
    until = timezone.localdate()
    since = until - timedelta(days=days)
    params = {"period": "day", "since": _unix(since), "until": _unix(until + timedelta(days=1))}
    try:
        payload = client.insights(f"{page.page_id}/insights", PAGE_DAY_METRICS, params)
        unsupported = list(payload.get("unsupported") or [])
        legacy = [LEGACY_SUBSTITUTES[name] for name in unsupported if name in LEGACY_SUBSTITUTES]
        if legacy:
            extra = client.insights(f"{page.page_id}/insights", legacy, params)
            payload["data"] = (payload.get("data") or []) + (extra.get("data") or [])
            unsupported.extend(extra.get("unsupported") or [])
    except FacebookAPIError as exc:
        stages[f"{page.page_id}:insights"] = _stage(exc)
        if exc.kind == "permission":
            page.insights_authorized = False
            if _ask_reconnect(job, page, INSIGHTS_SCOPE):
                page.reauthorization_required = True
            notes.append(f"insights {page.page_id}: Facebook refused Page insights. Follower totals stay on the Page.")
        else:
            notes.append(f"insights {page.page_id}: {exc}")
        return
    by_day = {}
    for point in metric_points(payload):
        day = insight_day(point["end_time"])
        if day is None or isinstance(point["value"], (dict, list)):
            continue
        apply_metric(by_day.setdefault(day, {}), point["name"], point["value"])
    for day, bucket in by_day.items():
        save_snapshot(
            page,
            snapshot_date=day,
            scope=FacebookSnapshot.Scope.INSIGHTS_DAILY,
            source="facebook_insights",
            values=bucket_values(bucket),
        )
    page.insights_authorized = True
    stages[f"{page.page_id}:insights"] = {"status": "success", "unsupported": unsupported}
    if unsupported:
        notes.append(f"insights {page.page_id}: Facebook does not provide " + ", ".join(unsupported) + ".")


def _sync_post_insights(page, job, client, stages, notes):
    if stages.get(f"{page.page_id}:insights", {}).get("status") in {"permission_denied", "skipped"}:
        stages[f"{page.page_id}:post_insights"] = {"status": "skipped"}
        return
    limit = _limit(job, "FACEBOOK_FULL_POST_INSIGHTS", "FACEBOOK_POST_INSIGHTS", 40, 25)
    denied = False
    unsupported = []
    for post in page.posts.order_by("-published_at")[:limit]:
        metrics = VIDEO_METRICS if is_video(post.status_type) else POST_METRICS
        try:
            payload = client.insights(f"{post.post_id}/insights", metrics, {"period": "lifetime"})
        except FacebookAPIError as exc:
            if exc.kind == "permission":
                denied = True
                break
            notes.append(f"post insights {post.post_id}: {exc}")
            continue
        dropped = list(payload.get("unsupported") or [])
        legacy = [LEGACY_SUBSTITUTES[name] for name in dropped if name in LEGACY_SUBSTITUTES]
        if legacy:
            try:
                extra = client.insights(f"{post.post_id}/insights", legacy, {"period": "lifetime"})
            except FacebookAPIError:
                extra = {"data": [], "unsupported": legacy}
            payload["data"] = (payload.get("data") or []) + (extra.get("data") or [])
            dropped.extend(extra.get("unsupported") or [])
        bucket = {}
        reaction_rows = []
        available = {}
        for point in metric_points(payload):
            available[point["name"]] = not isinstance(point["value"], (dict, list)) or bool(point["value"])
            if point["name"] == "post_reactions_by_type_total" and isinstance(point["value"], dict):
                reaction_rows = dict_rows(point["value"], "reaction")
                total = sum(amount for _dimension, _segment, amount in reaction_rows)
                if post.reaction_count is None:
                    bucket["reactions"] = (2, total)
                continue
            apply_metric(bucket, point["name"], point["value"])
        for name in dropped:
            available[name] = False
            if name not in unsupported:
                unsupported.append(name)
        values = bucket_values(bucket)
        if values:
            post_fields = {field.name for field in post._meta.concrete_fields}
            stored = {key: value for key, value in values.items() if key in post_fields}
            for key, value in stored.items():
                setattr(post, key, value)
            post.insights_available = available
            post.save(update_fields=[*stored.keys(), "insights_available"])
            _mirror_content(page.account, post)
            snapshot, writable = save_snapshot(
                page,
                post=post,
                post_key=post.post_id,
                snapshot_date=timezone.localdate(),
                scope=FacebookSnapshot.Scope.INSIGHTS_RANGE,
                source="facebook_post_insights",
                values=values,
            )
            replace_breakdowns(snapshot, reaction_rows, writable=writable)
    if denied:
        stages[f"{page.page_id}:post_insights"] = {"status": "permission_denied"}
        page.insights_authorized = False
        notes.append(f"post insights {page.page_id}: Facebook refused insights on posts.")
        return
    stages[f"{page.page_id}:post_insights"] = {"status": "success", "unsupported": unsupported}


def _sync_demographics(page, client, stages, notes):
    if stages.get(f"{page.page_id}:insights", {}).get("status") in {"permission_denied", "skipped"}:
        stages[f"{page.page_id}:demographics"] = {"status": "skipped"}
        return
    rows = []
    for metric, dimension in DEMOGRAPHIC_METRICS:
        try:
            payload = client.get(f"{page.page_id}/insights", {"metric": metric, "period": "day"})
        except FacebookAPIError as exc:
            if exc.kind in {"unsupported", "permission"}:
                notes.append(f"demographics {metric}: {exc}")
                continue
            stages[f"{page.page_id}:demographics"] = _stage(exc)
            notes.append(f"demographics {page.page_id}: {exc}")
            return
        latest = None
        for point in metric_points(payload):
            if isinstance(point["value"], dict):
                latest = point["value"]
        rows.extend(dict_rows(latest, dimension))
    if not rows:
        stages[f"{page.page_id}:demographics"] = {"status": "unavailable"}
        return
    snapshot, writable = save_snapshot(
        page,
        post_key="demographics",
        snapshot_date=timezone.localdate(),
        scope=FacebookSnapshot.Scope.INSIGHTS_RANGE,
        source="facebook_demographics",
        values={},
        period_start=timezone.localdate().replace(day=1),
    )
    replace_breakdowns(snapshot, rows, writable=writable or snapshot.snapshot_date == timezone.localdate())
    stages[f"{page.page_id}:demographics"] = {"status": "success", "segments": len(rows)}


def _aggregates(page):
    posts = page.posts.all()
    known = posts.filter(reaction_count__isnull=False)
    page.summed_reactions = known.aggregate(total=Sum("reaction_count"))["total"]
    page.hidden_reaction_count = posts.filter(reaction_count__isnull=True).count()
    page.summed_comments = posts.aggregate(total=Sum("comment_count"))["total"]
    page.summed_shares = posts.filter(share_count__isnull=False).aggregate(total=Sum("share_count"))["total"]
    viewed = posts.filter(views__isnull=False)
    page.summed_views = viewed.aggregate(total=Sum("views"))["total"] if viewed.exists() else None
    reached = posts.filter(reach__isnull=False)
    page.summed_reach = reached.aggregate(total=Sum("reach"))["total"] if reached.exists() else None
    now = timezone.now()
    for field, days in (
        ("posts_last_7_days", 7),
        ("posts_last_30_days", 30),
        ("posts_last_90_days", 90),
        ("posts_last_365_days", 365),
    ):
        setattr(page, field, posts.filter(published_at__gte=now - timedelta(days=days)).count())


def _mirror_account(account, page, page_count):
    account.external_id = page.page_id
    account.display_name = page.name or "Facebook Page"
    account.username = page.username or page.name
    account.profile_url = page.link
    account.avatar_url = page.picture_url
    account.last_error = ""
    account.last_synced_at = timezone.now()
    account.status_note = (
        f"{page_count} Page{'s' if page_count != 1 else ''}. "
        "Followers and Page likes are stored separately. Insight views are media views."
    )
    account.save()
    window = (
        page.snapshots.filter(post_key="", scope=FacebookSnapshot.Scope.INSIGHTS_DAILY)
        .order_by("-snapshot_date")
        .first()
    )
    demo = (
        page.snapshots.filter(post_key="demographics").prefetch_related("breakdowns").order_by("-snapshot_date").first()
    )
    slices = []
    if demo:
        for row in demo.breakdowns.all():
            if row.dimension != "country" or row.value is None:
                continue
            slices.append(AudienceSlice(dimension="country", segment=row.segment, label=row.segment, count=int(row.value)))
    saved = _upsert_snapshot(
        account,
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=page.followers_count or 0,
            content_count=page.posts.count(),
            views=(window.views if window and window.views is not None else page.summed_views or 0),
            likes=page.summed_reactions or 0,
            comments=page.summed_comments or 0,
            shares=page.summed_shares or 0,
            reach=(window.reach if window and window.reach is not None else 0),
            breakdowns=slices,
        ),
    )
    _replace_breakdowns(saved, FetchedSnapshot(captured_at=timezone.now(), breakdowns=slices))


def _mirror_content(account, post):
    defaults = {
        "title": title_from_message(post.message),
        "caption": post.message,
        "content_type": content_type(post.status_type),
        "url": post.permalink,
        "thumbnail_url": post.thumbnail_url,
        "published_at": post.published_at,
    }
    reactions = post.reaction_count if post.reaction_count is not None else post.like_count
    if reactions is not None:
        defaults["likes"] = reactions
    if post.comment_count is not None:
        defaults["comments"] = post.comment_count
    if post.share_count is not None:
        defaults["shares"] = post.share_count
    if post.views is not None:
        defaults["views"] = post.views
    ContentItem.objects.update_or_create(account=account, external_id=post.post_id, defaults=defaults)


def _finish(job, stages, notes, pages, calls, job_model=None):
    from networks.facebook_models import FacebookSyncJob

    job_model = job_model or FacebookSyncJob
    statuses = [item.get("status") for item in stages.values()]
    pages_stage = stages.get("pages") or {}
    if pages_stage.get("status") != "success":
        job.status = job_model.Status.FAILED
        kind = pages_stage.get("kind")
        job.outcome = {
            "retryable": job_model.Outcome.RETRYABLE,
            "permission": job_model.Outcome.PERMISSION_DENIED,
            "token": job_model.Outcome.PERMISSION_DENIED,
        }.get(kind, job_model.Outcome.PERMANENT)
    elif any(item.get("status") == "retryable" or item.get("kind") == "retryable" for item in stages.values()):
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
    for page in pages:
        if job.status == job_model.Status.COMPLETED:
            page.sync_status = FacebookPage.SyncStatus.SUCCEEDED
        elif job.status == job_model.Status.PARTIAL:
            page.sync_status = FacebookPage.SyncStatus.PARTIAL
        else:
            page.sync_status = FacebookPage.SyncStatus.FAILED
        page.last_error = job.last_error
        if job.status in {job_model.Status.COMPLETED, job_model.Status.PARTIAL}:
            page.last_success_at = timezone.now()
        page.save()
    logger.info(
        "facebook_sync_done job_id=%s account_id=%s status=%s posts=%s calls=%s",
        job.pk,
        job.account_id,
        job.status,
        job.posts_upserted,
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


def _scopes_omit(job, page, scope):
    granted = (job.granted_scopes or page.granted_scopes or "").replace(",", " ")
    if not granted.strip():
        return False
    return scope not in granted.split()


def _ask_reconnect(job, page, scope):
    granted = (job.granted_scopes or page.granted_scopes or "").replace(",", " ")
    if not granted.strip():
        return True
    return scope not in granted.split()


def _limit(job, full_name, incremental_name, full_default, incremental_default):
    if job.mode == "full":
        return int(getattr(settings, full_name, full_default) or full_default)
    return int(getattr(settings, incremental_name, incremental_default) or incremental_default)


def _setting(name, default):
    return int(getattr(settings, name, default) or default)


def _unix(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return int(start.timestamp())
