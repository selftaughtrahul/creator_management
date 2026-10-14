from django.conf import settings

PLATFORMS = {
    "instagram": {
        "label": "Instagram",
        "icon": "bi-instagram",
        "color": "#c13584",
        "audience_label": "Followers",
        "blurb": "Followers and recent posts from a professional account linked to a Facebook Page.",
    },
    "facebook": {
        "label": "Facebook",
        "icon": "bi-facebook",
        "color": "#1877f2",
        "audience_label": "Followers",
        "blurb": "Page followers and recent posts from a Facebook Page you manage.",
    },
    "youtube": {
        "label": "YouTube",
        "icon": "bi-youtube",
        "color": "#e11d48",
        "audience_label": "Subscribers",
        "blurb": "Channel subscribers, lifetime views, and recent video performance.",
    },
    "twitter": {
        "label": "X",
        "icon": "bi-twitter-x",
        "color": "#1d9bf0",
        "audience_label": "Followers",
        "blurb": "Followers and recent posts from the X account you authorize.",
    },
}

OAUTH = {
    "youtube": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": (
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ),
        "extra": {
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
        "client_id": lambda: settings.YOUTUBE_CLIENT_ID,
        "client_secret": lambda: settings.YOUTUBE_CLIENT_SECRET,
    },
    "facebook": {
        "auth_url": "https://www.facebook.com/{version}/dialog/oauth",
        "token_url": "https://graph.facebook.com/{version}/oauth/access_token",
        "scopes": (
            "pages_show_list",
            "pages_read_engagement",
            "pages_read_user_content",
            "public_profile",
        ),
        "extra": {},
        "client_id": lambda: settings.META_APP_ID,
        "client_secret": lambda: settings.META_APP_SECRET,
    },
    "instagram": {
        "auth_url": "https://www.facebook.com/{version}/dialog/oauth",
        "token_url": "https://graph.facebook.com/{version}/oauth/access_token",
        "scopes": (
            "instagram_basic",
            "instagram_manage_insights",
            "pages_show_list",
            "pages_read_engagement",
            "business_management",
        ),
        "extra": {},
        "client_id": lambda: settings.META_APP_ID,
        "client_secret": lambda: settings.META_APP_SECRET,
    },
    "twitter": {
        "auth_url": "https://twitter.com/i/oauth2/authorize",
        "token_url": "https://api.twitter.com/2/oauth2/token",
        "scopes": ("tweet.read", "users.read", "offline.access"),
        "extra": {},
        "client_id": lambda: settings.TWITTER_CLIENT_ID,
        "client_secret": lambda: settings.TWITTER_CLIENT_SECRET,
    },
}


def oauth_ready(platform):
    spec = OAUTH[platform]
    return bool(spec["client_id"]() and spec["client_secret"]())


def graph_version():
    version = settings.META_GRAPH_VERSION or "v23.0"
    if not version.startswith("v"):
        version = f"v{version}"
    return version
