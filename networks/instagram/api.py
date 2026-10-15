from functools import wraps

from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import is_creator
from networks.background import run_in_background
from networks.instagram.jobs import enqueue_instagram_sync, process_instagram_job
from networks.instagram.present import attached_profile
from networks.instagram_models import InstagramComment, InstagramMedia, InstagramSnapshot, InstagramSyncJob


def creator_json(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        if not is_creator(request.user):
            return JsonResponse({"error": "This Instagram data is available to the connected creator."}, status=403)
        return view(request, *args, **kwargs)

    return wrapped


def _profile(user):
    account = user.social_accounts.filter(platform="instagram").first()
    if account is None:
        return None
    return attached_profile(account)


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


def _profile_payload(profile):
    return {
        "ig_user_id": profile.ig_user_id,
        "username": profile.username,
        "name": profile.name,
        "account_type": profile.account_type,
        "followers_count": profile.followers_count,
        "follows_count": profile.follows_count,
        "media_count": profile.media_count,
        "summed_likes": profile.summed_likes,
        "summed_comments": profile.summed_comments,
        "summed_views": profile.summed_views,
        "summed_reach": profile.summed_reach,
        "hidden_like_count": profile.hidden_like_count,
        "insights_authorized": profile.insights_authorized,
        "comments_authorized": profile.comments_authorized,
        "reauthorization_required": profile.reauthorization_required,
        "sync_status": profile.sync_status,
        "last_success_at": profile.last_success_at,
        "last_error": profile.last_error,
    }


def _media_payload(media):
    return {
        "media_id": media.media_id,
        "caption": media.caption,
        "media_type": media.media_type,
        "media_product_type": media.media_product_type,
        "permalink": media.permalink,
        "published_at": media.published_at,
        "like_count": media.like_count,
        "comments_count": media.comments_count,
        "views": media.views,
        "reach": media.reach,
        "saved": media.saved,
        "shares": media.shares,
        "total_interactions": media.total_interactions,
        "avg_watch_time_ms": media.avg_watch_time_ms,
        "total_watch_time_ms": media.total_watch_time_ms,
        "insights_available": media.insights_available,
        "comments_sync_status": media.comments_sync_status,
    }


def _snapshot_payload(row):
    return {
        "snapshot_date": row.snapshot_date,
        "scope": row.scope,
        "period_start": row.period_start,
        "media_key": row.media_key,
        "followers": row.followers,
        "follows": row.follows,
        "media_count": row.media_count,
        "reach": row.reach,
        "views": row.views,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "saves": row.saves,
        "replies": row.replies,
        "accounts_engaged": row.accounts_engaged,
        "total_interactions": row.total_interactions,
        "followers_gained": row.followers_gained,
        "counter_reset": row.counter_reset,
        "source": row.source,
    }


def _job_payload(job):
    return {
        "id": job.pk,
        "mode": job.mode,
        "status": job.status,
        "outcome": job.outcome,
        "stages": job.stages,
        "media_seen": job.media_seen,
        "media_upserted": job.media_upserted,
        "comments_upserted": job.comments_upserted,
        "api_calls": job.api_calls,
        "last_error": job.last_error,
        "finished_at": job.finished_at,
    }


@creator_json
@require_GET
def profile_detail(request):
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"error": "Instagram is not synced yet."}, status=404)
    return JsonResponse(_profile_payload(profile), encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def media_list(request):
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"error": "Instagram is not synced yet."}, status=404)
    return _page(request, profile.media.all(), _media_payload)


@creator_json
@require_GET
def media_detail(request, media_id):
    profile = _profile(request.user)
    media = InstagramMedia.objects.filter(profile=profile, media_id=media_id).first() if profile else None
    if media is None:
        return JsonResponse({"error": "Media was not found."}, status=404)
    return JsonResponse(_media_payload(media), encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def media_comments(request, media_id):
    profile = _profile(request.user)
    media = InstagramMedia.objects.filter(profile=profile, media_id=media_id).first() if profile else None
    if media is None:
        return JsonResponse({"error": "Media was not found."}, status=404)

    def serialize(row: InstagramComment):
        return {
            "comment_id": row.comment_id,
            "parent_comment_id": row.parent_comment_id,
            "username": row.username,
            "text": row.text,
            "published_at": row.published_at,
            "like_count": row.like_count,
            "is_reply": row.is_reply,
        }

    return _page(request, media.comment_rows.all(), serialize)


@creator_json
@require_GET
def insights(request):
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"error": "Instagram is not synced yet."}, status=404)
    rows = profile.snapshots.filter(media_key="").exclude(scope=InstagramSnapshot.Scope.CALCULATED_DELTA)
    return _page(request, rows.order_by("-snapshot_date", "scope"), _snapshot_payload)


@creator_json
@require_POST
def sync_now(request):
    account = request.user.social_accounts.filter(platform="instagram", is_sample=False).first()
    if account is None or not account.has_token:
        return JsonResponse({"error": "Connect a live Instagram account first."}, status=400)
    mode = "full" if request.POST.get("mode") == "full" else "incremental"
    job = enqueue_instagram_sync(account, mode=mode, requested_by=request.user)
    job_id = job.pk
    run_in_background(
        f"instagram:{account.pk}",
        lambda: process_instagram_job(InstagramSyncJob.objects.get(pk=job_id)),
    )
    job.refresh_from_db()
    return JsonResponse(_job_payload(job), status=202, encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def sync_job_detail(request, job_id):
    account = request.user.social_accounts.filter(platform="instagram").first()
    job = InstagramSyncJob.objects.filter(pk=job_id, account=account).first() if account else None
    if job is None:
        return JsonResponse({"error": "Sync job was not found."}, status=404)
    return JsonResponse(_job_payload(job), encoder=DjangoJSONEncoder)
