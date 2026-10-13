import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

from networks.http import ProviderError, api_get, api_post, graph
from networks.platforms import OAUTH, graph_version


def begin_authorize(request, platform, redirect_uri):
    spec = OAUTH[platform]
    state = secrets.token_urlsafe(32)
    request.session[f"oauth_{platform}"] = {"state": state, "redirect_uri": redirect_uri}
    request.session.modified = True
    params = {
        "client_id": spec["client_id"](),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(spec["scopes"]),
        "state": state,
        **spec["extra"],
    }
    auth_url = spec["auth_url"].format(version=graph_version())
    return f"{auth_url}?{urlencode(params)}"


def pop_state(request, platform, state):
    saved = request.session.pop(f"oauth_{platform}", None)
    request.session.modified = True
    if not saved or not state or not secrets.compare_digest(saved.get("state", ""), state):
        raise ProviderError("The connection request could not be verified. Start it again from Connections.")
    return saved["redirect_uri"]


def exchange_code(platform, code, redirect_uri):
    spec = OAUTH[platform]
    token_url = spec["token_url"].format(version=graph_version())
    params = {
        "client_id": spec["client_id"](),
        "client_secret": spec["client_secret"](),
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if platform in {"facebook", "instagram"}:
        payload = api_get(token_url, params=params)
    else:
        payload = api_post(token_url, data=params, headers={"Accept": "application/json"})
    if "access_token" not in payload:
        raise ProviderError("The network did not return an access token.")
    if platform in {"facebook", "instagram"}:
        payload = _extend_meta_token(payload)
    return payload


def ensure_access_token(account):
    if not account.access_token:
        raise ProviderError("This connection has no access token. Connect it again.")
    expired = account.token_expires_at and account.token_expires_at <= timezone.now() + timedelta(minutes=2)
    if expired:
        _refresh(account)
    return account.access_token


def _extend_meta_token(payload):
    try:
        extended = api_get(
            graph("oauth/access_token"),
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": payload["access_token"],
            },
        )
    except ProviderError:
        return payload
    if extended.get("access_token"):
        return extended
    return payload


def _refresh(account):
    if account.platform == "youtube":
        if not account.refresh_token:
            raise ProviderError("The YouTube permission expired. Connect YouTube again.")
        payload = api_post(
            OAUTH["youtube"]["token_url"],
            data={
                "client_id": settings.YOUTUBE_CLIENT_ID,
                "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
            },
        )
    elif account.platform == "linkedin" and account.refresh_token:
        payload = api_post(
            OAUTH["linkedin"]["token_url"],
            data={
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
            },
        )
    else:
        raise ProviderError("This connection expired. Connect it again to keep syncing.")
    account.access_token = payload["access_token"]
    if payload.get("refresh_token"):
        account.refresh_token = payload["refresh_token"]
    if payload.get("expires_in"):
        account.token_expires_at = timezone.now() + timedelta(seconds=int(payload["expires_in"]))
    account.save(update_fields=["access_token", "refresh_token", "token_expires_at"])
