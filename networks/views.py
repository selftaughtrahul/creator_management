from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import creator_required, is_creator
from networks.background import run_in_background
from networks.demo import connect_sample
from networks.http import ProviderError
from networks.models import SocialAccount
from networks.oauth import begin_authorize, exchange_code, pop_state
from networks.tokenbox import seal
from networks.platforms import PLATFORMS, oauth_ready
from networks.sync import sync_account


def _platform(key):
    if key not in PLATFORMS:
        raise Http404
    return key


def _redirect_uri(request, platform):
    redirect_uri = request.build_absolute_uri(reverse("networks:oauth_callback", args=[platform]))
    if platform == "youtube" and settings.YOUTUBE_REDIRECT_URI:
        return settings.YOUTUBE_REDIRECT_URI
    if platform == "instagram" and settings.INSTAGRAM_REDIRECT_URI:
        return settings.INSTAGRAM_REDIRECT_URI
    if platform == "facebook" and settings.FACEBOOK_REDIRECT_URI:
        return settings.FACEBOOK_REDIRECT_URI
    if settings.DEBUG and platform == "facebook" and "://127.0.0.1" in redirect_uri:
        redirect_uri = redirect_uri.replace("://127.0.0.1", "://localhost", 1)
    return redirect_uri


def _here(request):
    return f"{request.scheme}://{request.get_host()}"


def _same_origin(request, origin):
    return not origin or _here(request).rstrip("/") == origin.rstrip("/")


def consume_oauth_notices(request):
    if not request.user.is_authenticated:
        return
    pending = cache.get(f"oauth-notices-{request.user.pk}")
    if not pending:
        return
    cache.delete(f"oauth-notices-{request.user.pk}")
    for item in pending:
        messages.add_message(request, item["level"], item["message"])


def _move_messages(request, user_id):
    pending = [
        {"level": message.level, "message": str(message)}
        for message in messages.get_messages(request)
    ]
    if pending and user_id:
        cache.set(f"oauth-notices-{user_id}", pending, timeout=180)


def _leave_callback(request, saved, viewname, kwargs=None):
    origin = (saved or {}).get("origin") or ""
    path = reverse(viewname, kwargs=kwargs or {})
    if not _same_origin(request, origin):
        _move_messages(request, (saved or {}).get("user_id"))
        return redirect(f"{origin.rstrip('/')}{path}")
    if request.user.is_authenticated and not is_creator(request.user):
        return redirect("account_login")
    return redirect(path)


def _pop_saved(request, platform, quiet=False):
    try:
        return pop_state(request, platform, request.GET.get("state", ""))
    except ProviderError as exc:
        if not quiet:
            messages.error(request, str(exc))
        return None


def _connect_user(request, saved):
    user_id = saved.get("user_id")
    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    if user is None:
        raise ProviderError("Sign in on this site, then connect the account again.")
    if not is_creator(user):
        raise ProviderError("Only a creator account can connect a social network.")
    origin = saved.get("origin") or ""
    if request.user.is_authenticated and request.user.pk == user.pk:
        return user
    if request.user.is_authenticated and not _same_origin(request, origin):
        return user
    if request.user.is_authenticated:
        raise ProviderError("This connection was started from a different account.")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return user


@creator_required
def connections(request):
    consume_oauth_notices(request)
    accounts = {
        account.platform: account
        for account in request.user.social_accounts.prefetch_related("youtube_channels", "youtube_sync_jobs")
    }
    cards = []
    for key, meta in PLATFORMS.items():
        account = accounts.get(key)
        jobs = list(account.youtube_sync_jobs.all()) if account and key == "youtube" else []
        cards.append(
            {
                "key": key,
                "meta": meta,
                "account": account,
                "ready": oauth_ready(key),
                "youtube_job": jobs[0] if jobs else None,
            }
        )
    return render(request, "app/connections.html", {"cards": cards})


@creator_required
def start_oauth(request, platform):
    platform = _platform(platform)
    if not oauth_ready(platform):
        messages.error(
            request,
            f"Add the {PLATFORMS[platform]['label']} API keys in .env before connecting a live account.",
        )
        return redirect("networks:connections")
    redirect_uri = _redirect_uri(request, platform)
    return redirect(begin_authorize(request, platform, redirect_uri))


def oauth_callback(request, platform):
    platform = _platform(platform)
    label = PLATFORMS[platform]["label"]
    provider_error = request.GET.get("error")
    saved = _pop_saved(request, platform, quiet=bool(provider_error))
    if provider_error:
        description = request.GET.get("error_description") or ""
        if platform == "instagram" and "developer role" in description.lower():
            messages.error(
                request,
                "Instagram only allows app testers while the Meta app is in Development. "
                "In the Meta app, open Instagram, API setup with Instagram login, and add this Instagram username under Instagram testers. "
                "Accept the invite in the Instagram app under Settings, Apps and websites, Tester invites, then connect again.",
            )
        elif platform == "instagram" and "permission" in description.lower():
            messages.error(
                request,
                "Instagram refused the permissions. Authorize with the Instagram account, then connect again.",
            )
        elif platform == "facebook" and "permission" in description.lower():
            messages.error(
                request,
                "Facebook refused the permissions. Add a Facebook Login for Business configuration that includes the Page permissions, then set META_LOGIN_CONFIG_ID in .env.",
            )
        else:
            messages.error(request, description or f"{label} did not finish connecting.")
        return _leave_callback(request, saved, "networks:connections")
    if saved is None:
        return _leave_callback(request, None, "networks:connections")
    try:
        user = _connect_user(request, saved)
        code = request.GET.get("code")
        if not code:
            raise ProviderError(f"{label} did not return an authorization code.")
        token = exchange_code(
            platform,
            code,
            saved["redirect_uri"],
            code_verifier=saved.get("code_verifier", ""),
        )
    except ProviderError as exc:
        messages.error(request, str(exc))
        return _leave_callback(request, saved, "networks:connections")

    account, _created = SocialAccount.objects.get_or_create(
        user=user,
        platform=platform,
    )
    if account.is_sample:
        account.snapshots.all().delete()
        account.contents.all().delete()
    account.is_sample = False
    account.access_token = seal(token.get("access_token", ""))
    if token.get("refresh_token"):
        account.refresh_token = seal(token["refresh_token"])
    expires_in = token.get("expires_in")
    if expires_in:
        account.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    account.last_error = ""
    account.save()
    if platform in {"youtube", "instagram", "facebook"}:
        _start_deep_sync(account, user, "full", scopes=token.get("scope") or "")
        messages.info(request, f"{label} is connected. The sync is running. Refresh this page in a moment.")
        return _leave_callback(request, saved, "analytics:platform", kwargs={"platform": platform})
    account_id = account.pk
    run_in_background(
        f"sync:{account_id}",
        lambda: sync_account(SocialAccount.objects.get(pk=account_id)),
    )
    messages.info(request, f"{label} is connected. The sync is running. Refresh this page in a moment.")
    return _leave_callback(request, saved, "analytics:platform", kwargs={"platform": platform})


@creator_required
@require_POST
def load_sample(request, platform):
    platform = _platform(platform)
    existing = SocialAccount.objects.filter(user=request.user, platform=platform).first()
    if existing and not existing.is_sample:
        messages.error(request, "Disconnect the live account before loading sample data.")
        return redirect("networks:connections")
    connect_sample(request.user, platform)
    messages.success(
        request,
        f"Sample {PLATFORMS[platform]['label']} data is saved. It stays labeled Sample until you connect the live account.",
    )
    return redirect("analytics:platform", platform=platform)


def _start_deep_sync(account, requested_by, mode, scopes=""):
    if account.platform == "youtube":
        from networks.youtube.jobs import enqueue_youtube_sync, process_youtube_job
        from networks.youtube_models import YouTubeSyncJob

        job = enqueue_youtube_sync(account, mode=mode, requested_by=requested_by, scopes=scopes)
        model = YouTubeSyncJob
        process = process_youtube_job
    elif account.platform == "instagram":
        from networks.instagram.jobs import enqueue_instagram_sync, process_instagram_job
        from networks.instagram_models import InstagramSyncJob

        job = enqueue_instagram_sync(account, mode=mode, requested_by=requested_by, scopes=scopes)
        model = InstagramSyncJob
        process = process_instagram_job
    elif account.platform == "facebook":
        from networks.facebook.jobs import enqueue_facebook_sync, process_facebook_job
        from networks.facebook_models import FacebookSyncJob

        job = enqueue_facebook_sync(account, mode=mode, requested_by=requested_by, scopes=scopes)
        model = FacebookSyncJob
        process = process_facebook_job
    else:
        return None
    job_id = job.pk

    def work():
        process(model.objects.get(pk=job_id))

    run_in_background(f"{account.platform}:{account.pk}", work)
    return job


def _queue_youtube(request, account, mode):
    _start_deep_sync(account, request.user, mode)
    messages.info(request, "YouTube sync is running. Refresh this page in a moment.")
    return True


def _queue_instagram(request, account, mode):
    _start_deep_sync(account, request.user, mode)
    messages.info(request, "Instagram sync is running. Refresh this page in a moment.")
    return True


def _queue_facebook(request, account, mode):
    _start_deep_sync(account, request.user, mode)
    messages.info(request, "Facebook sync is running. Refresh this page in a moment.")
    return True


@creator_required
@require_POST
def sync(request, platform):
    platform = _platform(platform)
    account = get_object_or_404(SocialAccount, user=request.user, platform=platform)
    if platform == "youtube" and not account.is_sample:
        mode = "full" if request.POST.get("mode") == "full" else "incremental"
        _queue_youtube(request, account, mode)
        return redirect("analytics:platform", platform="youtube")
    if platform == "instagram" and not account.is_sample:
        mode = "full" if request.POST.get("mode") == "full" else "incremental"
        _queue_instagram(request, account, mode)
        return redirect("analytics:platform", platform="instagram")
    if platform == "facebook" and not account.is_sample:
        mode = "full" if request.POST.get("mode") == "full" else "incremental"
        _queue_facebook(request, account, mode)
        return redirect("analytics:platform", platform="facebook")
    if account.is_sample:
        try:
            sync_account(account)
        except ProviderError as exc:
            account.last_error = str(exc)
            account.save(update_fields=["last_error"])
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{PLATFORMS[platform]['label']} updated.")
        return redirect("networks:connections")
    account_id = account.pk
    label = PLATFORMS[platform]["label"]
    run_in_background(
        f"sync:{account_id}",
        lambda: sync_account(SocialAccount.objects.get(pk=account_id)),
    )
    messages.info(request, f"{label} sync is running. Refresh this page in a moment.")
    return redirect("networks:connections")


@creator_required
@require_POST
def sync_all(request):
    failures = []
    started = 0
    for account in request.user.social_accounts.all():
        try:
            if account.is_sample:
                sync_account(account)
            elif account.platform in {"youtube", "instagram", "facebook"}:
                _start_deep_sync(account, request.user, "incremental")
            else:
                account_id = account.pk
                run_in_background(
                    f"sync:{account_id}",
                    lambda account_id=account_id: sync_account(SocialAccount.objects.get(pk=account_id)),
                )
            started += 1
        except ProviderError as exc:
            account.last_error = str(exc)
            account.save(update_fields=["last_error"])
            failures.append(f"{account.get_platform_display()}: {exc}")
    if started:
        messages.info(request, "Sync is running. Refresh this page in a moment.")
    if failures:
        messages.error(request, " ".join(failures))
    if not started and not failures:
        messages.info(request, "Connect a network before syncing.")
    return redirect("analytics:dashboard")


@creator_required
def disconnect(request, platform):
    platform = _platform(platform)
    account = get_object_or_404(SocialAccount, user=request.user, platform=platform)
    if request.method == "POST":
        account.delete()
        messages.success(
            request,
            f"{PLATFORMS[platform]['label']} was disconnected and its saved analytics were removed.",
        )
        return redirect("networks:connections")
    return render(
        request,
        "app/disconnect.html",
        {"account": account, "meta": PLATFORMS[platform]},
    )
