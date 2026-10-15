from django.core.paginator import Paginator
from django.urls import reverse

from networks.charts import charts_from_breakdowns
from networks.facebook_models import FacebookSnapshot
from networks.platforms import PLATFORMS


def platform_context(account, page_number):
    pages = list(account.facebook_pages.all())
    if not pages:
        return None
    links = dict(account.contents.values_list("external_id", "pk"))
    primary = next((page for page in pages if page.is_primary), pages[0])
    posts = primary.posts.all()
    paginator = Paginator(posts, 12)
    page = paginator.get_page(page_number or 1)
    for item in page.object_list:
        content_id = links.get(item.post_id)
        item.studio_url = reverse("analytics:content", args=[content_id]) if content_id else ""
    daily = list(
        primary.snapshots.filter(post_key="", scope=FacebookSnapshot.Scope.INSIGHTS_DAILY).order_by("-snapshot_date")[:14]
    )
    delta = (
        primary.snapshots.filter(post_key="", scope=FacebookSnapshot.Scope.CALCULATED_DELTA)
        .order_by("-snapshot_date")
        .first()
    )
    demographics = (
        primary.snapshots.filter(post_key="demographics").prefetch_related("breakdowns").order_by("-snapshot_date").first()
    )
    job = account.facebook_sync_jobs.order_by("-created_at").first()
    return {
        "pages": pages,
        "primary": primary,
        "page": page,
        "daily": daily,
        "delta": delta,
        "demographic_charts": charts_from_breakdowns(
            list(demographics.breakdowns.all()) if demographics else [],
            "fb",
            PLATFORMS["facebook"]["color"],
        ),
        "job": job,
    }


def post_context(item):
    post = None
    for page in item.account.facebook_pages.all():
        post = page.posts.filter(post_id=item.external_id).first()
        if post:
            break
    if post is None:
        return None
    return {
        "post": post,
        "page": post.page,
        "comments": list(post.comment_rows.all()[:30]),
    }
