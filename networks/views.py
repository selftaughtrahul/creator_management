from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from networks.demo import connect_sample
from networks.http import ProviderError
from networks.models import SocialAccount
from networks.oauth import begin_authorize, exchange_code, pop_state
from networks.platforms import PLATFORMS, oauth_ready
from networks.sync import sync_account


def _platform(key):
    if key not in PLATFORMS:
        raise Http404
    return key


def _resume_user(request, user_id):
    if request.user.is_authenticated:
        if user_id and request.user.pk != user_id:
            raise ProviderError("This connection was started from a different account.")
        return
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        raise ProviderError("Sign in on this site, then connect the account again.")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


@login_required
def connections(request):
    accounts = {account.platform: account for account in request.user.social_accounts.all()}
    cards = []
    for key, meta in PLATFORMS.items():
        cards.append(
            {
                "key": key,
                "meta": meta,
                "account": accounts.get(key),
                "ready": oauth_ready(key),
            }
        )
    return render(request, "app/connections.html", {"cards": cards})


@login_required
def start_oauth(request, platform):
    platform = _platform(platform)
    if not oauth_ready(platform):
        messages.error(
            request,
            f"Add the {PLATFORMS[platform]['label']} API keys in .env before connecting a live account.",
        )
        return redirect("networks:connections")
    redirect_uri = request.build_absolute_uri(reverse("networks:oauth_callback", args=[platform]))
    return redirect(begin_authorize(request, platform, redirect_uri))


def oauth_callback(request, platform):
    platform = _platform(platform)
    label = PLATFORMS[platform]["label"]
    if request.GET.get("error"):
        description = request.GET.get("error_description") or ""
        if platform in {"facebook", "instagram"} and "permission" in description.lower():
            messages.error(
                request,
                f"{label} refused the permissions. Add a Facebook Login for Business configuration that includes the Page and Instagram permissions, then set META_LOGIN_CONFIG_ID in .env.",
            )
        else:
            messages.error(request, description or f"{label} did not finish connecting.")
        return redirect("networks:connections")
    try:
        saved = pop_state(request, platform, request.GET.get("state", ""))
        _resume_user(request, saved.get("user_id"))
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
        return redirect("networks:connections" if request.user.is_authenticated else "account_login")

    account, _created = SocialAccount.objects.get_or_create(
        user=request.user,
        platform=platform,
    )
    if account.is_sample:
        account.snapshots.all().delete()
        account.contents.all().delete()
    account.is_sample = False
    account.access_token = token.get("access_token", "")
    if token.get("refresh_token"):
        account.refresh_token = token["refresh_token"]
    expires_in = token.get("expires_in")
    if expires_in:
        account.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    scope = token.get("scope")
    account.scopes = scope if isinstance(scope, str) else " ".join(scope or [])
    account.last_error = ""
    account.save()
    try:
        sync_account(account)
    except ProviderError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error"])
        messages.error(request, f"{label} is connected, but the first sync failed: {exc}")
        return redirect("networks:connections")
    messages.success(request, f"{label} is connected and the latest data is saved.")
    return redirect("analytics:platform", platform=platform)


@login_required
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


@login_required
@require_POST
def sync(request, platform):
    platform = _platform(platform)
    account = get_object_or_404(SocialAccount, user=request.user, platform=platform)
    try:
        sync_account(account)
    except ProviderError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error"])
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{PLATFORMS[platform]['label']} updated.")
    return redirect("networks:connections")


@login_required
@require_POST
def sync_all(request):
    failures = []
    synced = 0
    for account in request.user.social_accounts.all():
        try:
            sync_account(account)
            synced += 1
        except ProviderError as exc:
            account.last_error = str(exc)
            account.save(update_fields=["last_error"])
            failures.append(f"{account.get_platform_display()}: {exc}")
    if synced:
        messages.success(request, f"Updated {synced} connected account{'s' if synced != 1 else ''}.")
    if failures:
        messages.error(request, " ".join(failures))
    if not synced and not failures:
        messages.info(request, "Connect a network before syncing.")
    return redirect("analytics:dashboard")


@login_required
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
