"""Instagram API with Instagram Login.

Calls stay on graph.instagram.com. This client never downloads media bytes.
Profile and media metadata need instagram_business_basic. Insights need
instagram_business_manage_insights. Comments need
instagram_business_manage_comments.

User insight follower_count is the number of new followers on that day, not
the lifetime total. Lifetime followers come from the user node's
followers_count field. Impressions, plays, and video_views are retired on
current Graph versions; views is the replacement and is requested with a
fallback when a metric is rejected.

https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-user/insights
https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
"""

import logging
import time

import requests
from django.conf import settings

from networks.http import ig_graph

logger = logging.getLogger("networks.instagram")

RETRYABLE_CODES = {4, 17, 32, 613}
PERMISSION_CODES = {10, 200}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

USER_DAY_METRICS = ("reach", "follower_count")
USER_TOTAL_METRICS = (
    "views",
    "reach",
    "accounts_engaged",
    "total_interactions",
    "likes",
    "comments",
    "shares",
    "saves",
    "replies",
)
MEDIA_METRICS = ("reach", "views", "saved", "shares", "total_interactions", "likes", "comments")
REEL_METRICS = MEDIA_METRICS + ("ig_reels_avg_watch_time", "ig_reels_video_view_total_time")
PROFILE_FIELDS = (
    "user_id,username,name,biography,website,account_type,profile_picture_url,"
    "followers_count,follows_count,media_count"
)
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,permalink,thumbnail_url,timestamp,"
    "like_count,comments_count,is_comment_enabled"
)
COMMENT_FIELDS = "id,text,username,timestamp,like_count,parent_id,replies{id,text,username,timestamp,like_count}"


class InstagramAPIError(Exception):
    def __init__(self, message, *, kind="permanent", code=None, status=None):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.status = status


def classify_error(status, payload):
    error = (payload or {}).get("error") or {}
    code = error.get("code")
    message = error.get("message") or "Instagram request failed."
    lowered = message.lower()
    if status in RETRYABLE_STATUS or code in RETRYABLE_CODES:
        return "retryable", message, code
    if code == 190 or "access token" in lowered or "session has expired" in lowered:
        return "token", message, code
    if code in PERMISSION_CODES or "permission" in lowered or "not authorized" in lowered:
        return "permission", message, code
    if code == 100 and "metric" in lowered:
        return "unsupported", message, code
    if status == 400 and "metric" in lowered:
        return "unsupported", message, code
    return "permanent", message, code


class InstagramClient:
    def __init__(self, token):
        self.token = token
        self.calls = 0
        self.timeout = float(getattr(settings, "INSTAGRAM_HTTP_TIMEOUT", 25) or 25)
        self.retries = int(getattr(settings, "INSTAGRAM_MAX_RETRIES", 3) or 3)

    def get(self, path, params=None):
        query = dict(params or {})
        query["access_token"] = self.token
        url = ig_graph(path)
        attempt = 0
        while True:
            attempt += 1
            self.calls += 1
            try:
                response = requests.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt <= self.retries:
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                raise InstagramAPIError(str(exc), kind="retryable") from exc
            payload = _json(response)
            if response.status_code < 400:
                return payload
            kind, message, code = classify_error(response.status_code, payload)
            logger.info(
                "instagram_http status=%s kind=%s code=%s path=%s",
                response.status_code,
                kind,
                code,
                path,
            )
            if kind == "retryable" and attempt <= self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            raise InstagramAPIError(message, kind=kind, code=code, status=response.status_code)

    def paged(self, path, params, limit):
        rows = []
        after = ""
        while len(rows) < limit:
            page = dict(params)
            page["limit"] = min(50, limit - len(rows))
            if after:
                page["after"] = after
            payload = self.get(path, page)
            batch = payload.get("data") or []
            rows.extend(batch)
            after = ((payload.get("paging") or {}).get("cursors") or {}).get("after") or ""
            if not after or not batch:
                break
        return rows[:limit]

    def insights(self, path, metrics, params):
        metrics = list(metrics)
        if not metrics:
            return {"data": [], "unsupported": []}
        try:
            payload = self.get(path, {**params, "metric": ",".join(metrics)})
        except InstagramAPIError as exc:
            if exc.kind != "unsupported":
                raise
            if len(metrics) == 1:
                return {"data": [], "unsupported": metrics}
            mid = max(1, len(metrics) // 2)
            left = self.insights(path, metrics[:mid], params)
            right = self.insights(path, metrics[mid:], params)
            return {
                "data": (left.get("data") or []) + (right.get("data") or []),
                "unsupported": (left.get("unsupported") or []) + (right.get("unsupported") or []),
            }
        payload["unsupported"] = []
        return payload


def _json(response):
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
