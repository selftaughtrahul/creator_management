import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from networks.http import ProviderError, api_get, api_post, graph
from networks.platforms import OAUTH, graph_version


def begin_authorize(request, platform, redirect_uri):
    spec = OAUTH[platform]
    state = secrets.token_urlsafe(32)
    saved = {"state": state, "redirect_uri": redirect_uri}
    params = {
        "client_id": spec["client_id"](),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(spec["scopes"]),
        "state": state,
        **spec["extra"],
    }
    if platform == "instagram":
        params["scope"] = ",".join(spec["scopes"])
    if platform == "facebook" and settings.META_LOGIN_CONFIG_ID:
        params.pop("scope", None)
        params["config_id"] = settings.META_LOGIN_CONFIG_ID
        params["override_default_response_type"] = "true"
    if platform == "twitter":
        verifier, challenge = _pkce_pair()
        saved["code_verifier"] = verifier
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    saved["user_id"] = request.user.pk
    saved["platform"] = platform
    saved["origin"] = f"{request.scheme}://{request.get_host()}"
    cache.set(f"oauth-state-{state}", saved, timeout=600)
    request.session[f"oauth_{platform}"] = saved
    request.session.modified = True
    auth_url = spec["auth_url"].format(version=graph_version())
    return f"{auth_url}?{urlencode(params)}"


def pop_state(request, platform, state):
    saved = cache.get(f"oauth-state-{state}") if state else None
    if saved and saved.get("platform") == platform:
        cache.delete(f"oauth-state-{state}")
        request.session.pop(f"oauth_{platform}", None)
        return saved
    session_saved = request.session.pop(f"oauth_{platform}", None)
    request.session.modified = True
    if (
        not session_saved
        or not state
        or not secrets.compare_digest(session_saved.get("state", ""), state)
    ):
        raise ProviderError("The connection request could not be verified. Start it again from Connections.")
    return session_saved


def exchange_code(platform, code, redirect_uri, code_verifier=""):
    spec = OAUTH[platform]
    token_url = spec["token_url"].format(version=graph_version())
    params = {
        "client_id": spec["client_id"](),
        "client_secret": spec["client_secret"](),
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    headers = {"Accept": "application/json"}
    if platform == "instagram":
        if not spec["client_secret"]():
            raise ProviderError(
                "Add INSTAGRAM_APP_SECRET from Instagram business login settings in the Meta app, then connect again."
            )
        payload = _instagram_token(api_post(token_url, data=params))
    elif platform == "facebook":
        payload = api_get(token_url, params=params)
    else:
        if platform == "twitter":
            if not code_verifier:
                raise ProviderError("The X connection expired before it finished. Start it again.")
            params["code_verifier"] = code_verifier
            headers = _twitter_basic_headers()
        payload = api_post(token_url, data=params, headers=headers)
    if "access_token" not in payload:
        raise ProviderError("The network did not return an access token.")
    if platform == "facebook":
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


def _instagram_token(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(rows, list) and rows:
        payload = rows[0]
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise ProviderError("The network did not return an access token.")
    try:
        extended = api_get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "access_token": payload["access_token"],
            },
        )
    except ProviderError:
        return payload
    if extended.get("access_token"):
        return extended
    return payload


def _refresh(account):
    if account.platform == "instagram":
        payload = api_get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": account.access_token},
        )
    elif account.platform == "youtube":
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
    elif account.platform == "twitter" and account.refresh_token:
        payload = api_post(
            OAUTH["twitter"]["token_url"],
            data={
                "client_id": settings.TWITTER_CLIENT_ID,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
            },
            headers=_twitter_basic_headers(),
        )
    else:
        raise ProviderError("This connection expired. Connect it again to keep syncing.")
    account.access_token = payload["access_token"]
    if payload.get("refresh_token"):
        account.refresh_token = payload["refresh_token"]
    if payload.get("expires_in"):
        account.token_expires_at = timezone.now() + timedelta(seconds=int(payload["expires_in"]))
    account.save(update_fields=["access_token", "refresh_token", "token_expires_at"])


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _twitter_basic_headers():
    raw = f"{settings.TWITTER_CLIENT_ID}:{settings.TWITTER_CLIENT_SECRET}".encode()
    token = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}
