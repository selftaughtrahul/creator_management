from functools import wraps

from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import is_creator
from networks.facebook.jobs import enqueue_facebook_sync, process_facebook_job
from networks.facebook_models import FacebookComment, FacebookPage, FacebookPost, FacebookSnapshot, FacebookSyncJob


def creator_json(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        if not is_creator(request.user):
            return JsonResponse({"error": "This Facebook data is available to the connected creator."}, status=403)
        return view(request, *args, **kwargs)

    return wrapped


def _pages(user):
    account = user.social_accounts.filter(platform="facebook").first()
    if account is None:
        return FacebookPage.objects.none()
    return account.facebook_pages.all()


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


def _page_payload(page):
    return {
        "page_id": page.page_id,
        "name": page.name,
        "username": page.username,
        "category": page.category,
        "followers_count": page.followers_count,
        "fan_count": page.fan_count,
        "is_primary": page.is_primary,
        "summed_reactions": page.summed_reactions,
        "summed_comments": page.summed_comments,
        "summed_shares": page.summed_shares,
        "summed_views": page.summed_views,
        "summed_reach": page.summed_reach,
        "hidden_reaction_count": page.hidden_reaction_count,
        "insights_authorized": page.insights_authorized,
        "reauthorization_required": page.reauthorization_required,
        "sync_status": page.sync_status,
        "last_success_at": page.last_success_at,
        "last_error": page.last_error,
    }


def _post_payload(post):
    return {
        "post_id": post.post_id,
        "message": post.message,
        "status_type": post.status_type,
        "permalink": post.permalink,
        "published_at": post.published_at,
        "reaction_count": post.reaction_count,
        "like_count": post.like_count,
        "comment_count": post.comment_count,
        "share_count": post.share_count,
        "views": post.views,
        "reach": post.reach,
        "clicks": post.clicks,
        "video_views": post.video_views,
        "insights_available": post.insights_available,
        "comments_sync_status": post.comments_sync_status,
    }


def _snapshot_payload(row):
    return {
        "snapshot_date": row.snapshot_date,
        "scope": row.scope,
        "post_key": row.post_key,
        "followers": row.followers,
        "fans": row.fans,
        "followers_gained": row.followers_gained,
        "followers_lost": row.followers_lost,
        "views": row.views,
        "reach": row.reach,
        "engagements": row.engagements,
        "reactions": row.reactions,
        "clicks": row.clicks,
        "video_views": row.video_views,
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
        "pages_seen": job.pages_seen,
        "posts_upserted": job.posts_upserted,
        "comments_upserted": job.comments_upserted,
        "api_calls": job.api_calls,
        "last_error": job.last_error,
        "finished_at": job.finished_at,
    }


@creator_json
@require_GET
def page_list(request):
    return _page(request, _pages(request.user), _page_payload)


@creator_json
@require_GET
def page_detail(request, page_id):
    page = _pages(request.user).filter(page_id=page_id).first()
    if page is None:
        return JsonResponse({"error": "Page was not found."}, status=404)
    return JsonResponse(_page_payload(page), encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def page_posts(request, page_id):
    page = _pages(request.user).filter(page_id=page_id).first()
    if page is None:
        return JsonResponse({"error": "Page was not found."}, status=404)
    return _page(request, page.posts.all(), _post_payload)


@creator_json
@require_GET
def page_insights(request, page_id):
    page = _pages(request.user).filter(page_id=page_id).first()
    if page is None:
        return JsonResponse({"error": "Page was not found."}, status=404)
    rows = page.snapshots.filter(post_key="").exclude(scope=FacebookSnapshot.Scope.CALCULATED_DELTA)
    return _page(request, rows.order_by("-snapshot_date", "scope"), _snapshot_payload)


@creator_json
@require_GET
def post_detail(request, post_id):
    post = FacebookPost.objects.filter(page__in=_pages(request.user), post_id=post_id).first()
    if post is None:
        return JsonResponse({"error": "Post was not found."}, status=404)
    return JsonResponse(_post_payload(post), encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def post_comments(request, post_id):
    post = FacebookPost.objects.filter(page__in=_pages(request.user), post_id=post_id).first()
    if post is None:
        return JsonResponse({"error": "Post was not found."}, status=404)

    def serialize(row: FacebookComment):
        return {
            "comment_id": row.comment_id,
            "parent_comment_id": row.parent_comment_id,
            "author_name": row.author_name,
            "message": row.message,
            "published_at": row.published_at,
            "like_count": row.like_count,
            "is_reply": row.is_reply,
        }

    return _page(request, post.comment_rows.all(), serialize)


@creator_json
@require_POST
def sync_now(request):
    account = request.user.social_accounts.filter(platform="facebook", is_sample=False).first()
    if account is None or not account.has_token:
        return JsonResponse({"error": "Connect a live Facebook Page first."}, status=400)
    mode = "full" if request.POST.get("mode") == "full" else "incremental"
    job = enqueue_facebook_sync(account, mode=mode, requested_by=request.user)
    process_facebook_job(job)
    status = 200 if job.status in {FacebookSyncJob.Status.COMPLETED, FacebookSyncJob.Status.PARTIAL} else 502
    return JsonResponse(_job_payload(job), status=status, encoder=DjangoJSONEncoder)


@creator_json
@require_GET
def sync_job_detail(request, job_id):
    account = request.user.social_accounts.filter(platform="facebook").first()
    job = FacebookSyncJob.objects.filter(pk=job_id, account=account).first() if account else None
    if job is None:
        return JsonResponse({"error": "Sync job was not found."}, status=404)
    return JsonResponse(_job_payload(job), encoder=DjangoJSONEncoder)
