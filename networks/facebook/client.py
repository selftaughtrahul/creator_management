"""Facebook Page sync on graph.facebook.com.

Page insights use the current media-view metrics. Deprecated impression and
fan-add metrics are requested only when Facebook rejects the current name.
Reaction and comment summaries use limit(0) so the response is a count, not
a list of people. Video file URLs are never requested.

https://developers.facebook.com/docs/graph-api/reference/page
https://developers.facebook.com/docs/graph-api/reference/page/insights
https://developers.facebook.com/docs/graph-api/reference/insights
"""

import logging
import time

import requests
from django.conf import settings

from networks.http import graph

logger = logging.getLogger("networks.facebook")

RETRYABLE_CODES = {4, 17, 32, 613}
PERMISSION_CODES = {10, 200}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

PAGE_DAY_METRICS = (
    "page_follows",
    "page_daily_follows_unique",
    "page_daily_unfollows_unique",
    "page_media_view",
    "page_total_media_view_unique",
    "page_post_engagements",
    "page_video_views",
    "page_video_view_time",
)
POST_METRICS = ("post_media_view", "post_total_media_view_unique", "post_clicks", "post_reactions_by_type_total")
VIDEO_METRICS = POST_METRICS + ("post_video_views", "post_video_avg_time_watched", "post_video_view_time")
PAGE_FIELDS = "id,name,about,category,username,fan_count,followers_count,link,picture{url}"
ACCOUNT_FIELDS = "id,name,access_token,fan_count,followers_count,link,picture{url}"
POST_FIELDS = (
    "id,message,created_time,permalink_url,full_picture,status_type,shares,"
    "reactions.summary(true).limit(0),comments.summary(true).limit(0),likes.summary(true).limit(0)"
)
COMMENT_FIELDS = "id,message,created_time,like_count,comment_count,parent{id},from{name}"


class FacebookAPIError(Exception):
    def __init__(self, message, *, kind="permanent", code=None, status=None):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.status = status


def classify_error(status, payload):
    error = (payload or {}).get("error") or {}
    code = error.get("code")
    message = error.get("message") or "Facebook request failed."
    lowered = message.lower()
    if status in RETRYABLE_STATUS or code in RETRYABLE_CODES:
        return "retryable", message, code
    if code == 190 or "access token" in lowered or "session has expired" in lowered:
        return "token", message, code
    if code in PERMISSION_CODES or "permission" in lowered or "not authorized" in lowered:
        return "permission", message, code
    if code == 100 and ("metric" in lowered or "invalid" in lowered):
        return "unsupported", message, code
    if status == 400 and "metric" in lowered:
        return "unsupported", message, code
    return "permanent", message, code


class FacebookClient:
    def __init__(self, token):
        self.token = token
        self.calls = 0
        self.timeout = float(getattr(settings, "FACEBOOK_HTTP_TIMEOUT", 25) or 25)
        self.retries = int(getattr(settings, "FACEBOOK_MAX_RETRIES", 3) or 3)

    def get(self, path, params=None):
        query = dict(params or {})
        query["access_token"] = self.token
        url = graph(path)
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
                raise FacebookAPIError(str(exc), kind="retryable") from exc
            payload = _json(response)
            if response.status_code < 400:
                return payload
            kind, message, code = classify_error(response.status_code, payload)
            logger.info(
                "facebook_http status=%s kind=%s code=%s path=%s",
                response.status_code,
                kind,
                code,
                path,
            )
            if kind == "retryable" and attempt <= self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            raise FacebookAPIError(message, kind=kind, code=code, status=response.status_code)

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
        except FacebookAPIError as exc:
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
