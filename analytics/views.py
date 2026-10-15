from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from accounts.permissions import creator_required
from analytics.services import build_content, build_dashboard, build_platform
from networks.models import ContentItem, SocialAccount
from networks.platforms import PLATFORMS
from networks.views import consume_oauth_notices
from networks.facebook.present import platform_context as facebook_platform_context
from networks.facebook.present import post_context as facebook_post_context
from networks.instagram.present import media_context as instagram_media_context
from networks.instagram.present import platform_context as instagram_platform_context
from networks.youtube.present import platform_context, video_context


@creator_required
def dashboard(request):
    return render(request, "app/dashboard.html", build_dashboard(request.user))


@creator_required
def platform(request, platform):
    consume_oauth_notices(request)
    if platform not in PLATFORMS:
        raise Http404
    account = SocialAccount.objects.filter(user=request.user, platform=platform).first()
    context = {"platform": platform, "meta": PLATFORMS[platform], "account": account}
    if account:
        context.update(build_platform(account))
        context["page"] = Paginator(account.contents.all(), 8).get_page(request.GET.get("page"))
        if platform == "youtube":
            context["youtube"] = platform_context(account, request.GET.get("yt_page"))
        if platform == "instagram":
            context["instagram"] = instagram_platform_context(account, request.GET.get("ig_page"))
        if platform == "facebook":
            context["facebook"] = facebook_platform_context(account, request.GET.get("fb_page"))
    return render(request, "app/platform.html", context)


@creator_required
def content(request, pk):
    item = get_object_or_404(
        ContentItem.objects.select_related("account"),
        pk=pk,
        account__user=request.user,
    )
    context = build_content(item)
    if item.account.platform == "youtube":
        context["youtube"] = video_context(item)
    if item.account.platform == "instagram":
        context["instagram"] = instagram_media_context(item)
    if item.account.platform == "facebook":
        context["facebook"] = facebook_post_context(item)
    return render(request, "app/content.html", context)
