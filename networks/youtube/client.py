"""YouTube HTTP client.

Quota, per Google’s default Data API budget of 10,000 units per day:

- channels.list, playlistItems.list, videos.list, commentThreads.list,
  comments.list: 1 unit each. Video details are requested 50 ids at a time.
- captions.list: 50 units. captions.download: 200 units. Transcripts are the
  expensive part of a full sync, so the number of videos is capped.
- YouTube Analytics reports use a separate quota. This client counts those
  calls but does not pretend they share the Data API unit pool.

A channel with 500 videos is about 1 + 10 playlist pages + 10 video batches.
Comments add one call per page. Downloading 10 caption tracks is about
10 * 50 + 10 * 200 = 2,500 Data API units before comments.
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger("networks.youtube")

DATA_COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "commentThreads.list": 1,
    "comments.list": 1,
    "captions.list": 50,
    "captions.download": 200,
}

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
UNAVAILABLE_REASONS = {"commentsDisabled", "commentDisabled", "videoNotFound"}


class YouTubeAPIError(Exception):
    def __init__(self, message, *, kind="failed", status_code=None, reason=""):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.reason = reason


class QuotaMeter:
    def __init__(self):
        self.calls = 0
        self.units = 0
        self.analytics_calls = 0

    def add(self, cost, system):
        self.calls += 1
        if system == "analytics":
            self.analytics_calls += 1
        else:
            self.units += int(cost or 0)


def classify_error(status, payload):
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    reasons = []
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.append(str(item["reason"]))
    reason = reasons[0] if reasons else ""
    message = str(error.get("message") or reason or "YouTube request failed.")[:300]
    if reason in QUOTA_REASONS:
        kind = "quota"
    elif reason == "rateLimitExceeded" or status == 429:
        kind = "retryable"
    elif reason in UNAVAILABLE_REASONS or status == 404:
        kind = "unavailable"
    elif status in RETRYABLE_STATUS:
        kind = "retryable"
    elif status == 400:
        kind = "unsupported"
    elif status in {401, 403}:
        kind = "permission"
    else:
        kind = "failed"
    return YouTubeAPIError(message, kind=kind, status_code=status, reason=reason)


def request_json(method, url, *, headers=None, params=None, timeout=None):
    timeout = timeout or float(getattr(settings, "YOUTUBE_HTTP_TIMEOUT", 25) or 25)
    try:
        response = requests.request(method, url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise YouTubeAPIError("Could not reach YouTube.", kind="retryable") from exc
    if response.status_code == 204 or not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        if response.status_code >= 400:
            raise classify_error(response.status_code, {}) from exc
        raise YouTubeAPIError("YouTube returned a response that was not JSON.", kind="retryable") from exc
    if response.status_code >= 400:
        raise classify_error(response.status_code, payload if isinstance(payload, dict) else {})
    return payload


def request_text(url, *, headers=None, params=None, timeout=None):
    timeout = timeout or float(getattr(settings, "YOUTUBE_HTTP_TIMEOUT", 25) or 25)
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise YouTubeAPIError("Could not reach YouTube.", kind="retryable") from exc
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raise classify_error(response.status_code, payload if isinstance(payload, dict) else {})
    return response.text or ""


class YouTubeClient:
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.quota = QuotaMeter()

    def get_json(self, url, params, *, cost, system="data"):
        retries = int(getattr(settings, "YOUTUBE_MAX_RETRIES", 3) or 3)
        delay = 1
        last = None
        for attempt in range(1, retries + 1):
            try:
                payload = request_json("GET", url, headers=self.headers, params=params)
            except YouTubeAPIError as exc:
                self.quota.add(cost, system)
                last = exc
                if exc.kind != "retryable" or attempt >= retries:
                    logger.info(
                        "youtube_api_error system=%s status=%s kind=%s reason=%s",
                        system,
                        exc.status_code,
                        exc.kind,
                        exc.reason,
                    )
                    raise
                logger.info("youtube_retry attempt=%s kind=%s", attempt, exc.kind)
                time.sleep(delay)
                delay *= 2
                continue
            self.quota.add(cost, system)
            return payload
        raise last

    def get_text(self, url, params, *, cost):
        payload = request_text(url, headers=self.headers, params=params)
        self.quota.add(cost, "data")
        return payload

    def channels(self):
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet,statistics,contentDetails,status", "mine": "true", "maxResults": 50},
            cost=DATA_COSTS["channels.list"],
        )

    def playlist_page(self, playlist_id, page_token=""):
        params = {
            "part": "contentDetails,snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params,
            cost=DATA_COSTS["playlistItems.list"],
        )

    def videos(self, video_ids):
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/videos",
            {
                "part": "snippet,statistics,contentDetails,status",
                "id": ",".join(video_ids),
                "maxResults": 50,
            },
            cost=DATA_COSTS["videos.list"],
        )

    def comment_threads(self, video_id, page_token=""):
        params = {
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": 100,
            "order": "time",
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/commentThreads",
            params,
            cost=DATA_COSTS["commentThreads.list"],
        )

    def comment_replies(self, parent_id, page_token=""):
        params = {
            "part": "snippet",
            "parentId": parent_id,
            "maxResults": 100,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/comments",
            params,
            cost=DATA_COSTS["comments.list"],
        )

    def captions(self, video_id):
        return self.get_json(
            "https://www.googleapis.com/youtube/v3/captions",
            {"part": "snippet", "videoId": video_id},
            cost=DATA_COSTS["captions.list"],
        )

    def caption_download(self, caption_id):
        return self.get_text(
            "https://www.googleapis.com/youtube/v3/captions/" + caption_id,
            {"tfmt": "vtt"},
            cost=DATA_COSTS["captions.download"],
        )

    def analytics(self, params):
        return self.get_json(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params,
            cost=1,
            system="analytics",
        )
