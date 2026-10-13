from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from networks.http import ProviderError, api_get, as_int, graph
from networks.oauth import ensure_access_token


@dataclass
class FetchedProfile:
    external_id: str
    display_name: str
    username: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class FetchedSnapshot:
    captured_at: datetime
    followers: int = 0
    following: int = 0
    content_count: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    reach: int = 0


@dataclass
class FetchedContent:
    external_id: str
    title: str
    caption: str = ""
    content_type: str = "post"
    url: str = ""
    thumbnail_url: str = ""
    published_at: datetime | None = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    impressions: int = 0
    watch_time_seconds: int = 0


@dataclass
class FetchResult:
    profile: FetchedProfile
    snapshots: list[FetchedSnapshot]
    contents: list[FetchedContent]
    note: str = ""
    resource_token: str = ""


def fetch_account(account):
    fetcher = {
        "youtube": fetch_youtube,
        "facebook": fetch_facebook,
        "instagram": fetch_instagram,
        "twitter": fetch_twitter,
    }[account.platform]
    return fetcher(account)


def fetch_youtube(account):
    token = ensure_access_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    payload = api_get(
        "https://www.googleapis.com/youtube/v3/channels",
        headers=headers,
        params={"part": "snippet,statistics,contentDetails", "mine": "true"},
    )
    items = payload.get("items") or []
    if not items:
        raise ProviderError("No YouTube channel was found for this Google account.")
    channel = items[0]
    snippet = channel.get("snippet") or {}
    stats = channel.get("statistics") or {}
    uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    contents = _youtube_videos(headers, uploads) if uploads else []
    followers = as_int(stats.get("subscriberCount"))
    snapshots = _youtube_history(headers, followers, as_int(stats.get("videoCount")))
    snapshots.append(
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=followers,
            content_count=as_int(stats.get("videoCount")),
            views=as_int(stats.get("viewCount")),
            likes=sum(item.likes for item in contents),
            comments=sum(item.comments for item in contents),
            shares=sum(item.shares for item in contents),
        )
    )
    thumb = ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url") or ""
    custom = snippet.get("customUrl") or ""
    return FetchResult(
        profile=FetchedProfile(
            external_id=channel.get("id") or "",
            display_name=snippet.get("title") or "YouTube channel",
            username=custom,
            profile_url=f"https://www.youtube.com/{custom.lstrip('/')}" if custom else f"https://www.youtube.com/channel/{channel.get('id')}",
            avatar_url=thumb,
            raw={"statistics": stats},
        ),
        snapshots=snapshots,
        contents=contents,
    )


def fetch_facebook(account):
    token = ensure_access_token(account)
    pages = api_get(
        graph("me/accounts"),
        params={
            "fields": "id,name,access_token,fan_count,followers_count,link,picture{url}",
            "access_token": token,
        },
    )
    rows = pages.get("data") or []
    if not rows:
        raise ProviderError(
            "No Facebook Page was found. Connect a Page you manage. Personal profiles do not share these insights."
        )
    page = max(rows, key=lambda row: as_int(row.get("followers_count") or row.get("fan_count")))
    page_token = page.get("access_token") or token
    posts = api_get(
        graph(f"{page['id']}/posts"),
        params={
            "fields": "id,message,created_time,permalink_url,full_picture,shares,likes.summary(true),comments.summary(true)",
            "limit": 15,
            "access_token": page_token,
        },
    )
    contents = []
    for post in posts.get("data") or []:
        message = (post.get("message") or "Facebook post").strip()
        contents.append(
            FetchedContent(
                external_id=post.get("id") or "",
                title=_title(message),
                caption=message,
                content_type="post",
                url=post.get("permalink_url") or "",
                thumbnail_url=post.get("full_picture") or "",
                published_at=_parse_dt(post.get("created_time")),
                likes=_summary_count(post.get("likes")),
                comments=_summary_count(post.get("comments")),
                shares=as_int((post.get("shares") or {}).get("count")),
            )
        )
    picture = ((page.get("picture") or {}).get("data") or {}).get("url") or ""
    followers = as_int(page.get("followers_count") or page.get("fan_count"))
    return FetchResult(
        profile=FetchedProfile(
            external_id=str(page.get("id") or ""),
            display_name=page.get("name") or "Facebook Page",
            username=page.get("name") or "",
            profile_url=page.get("link") or "",
            avatar_url=picture,
            raw={"fan_count": page.get("fan_count"), "followers_count": page.get("followers_count")},
        ),
        snapshots=[
            FetchedSnapshot(
                captured_at=timezone.now(),
                followers=followers,
                content_count=len(contents),
                likes=sum(item.likes for item in contents),
                comments=sum(item.comments for item in contents),
                shares=sum(item.shares for item in contents),
            )
        ],
        contents=contents,
        resource_token=page_token,
    )


def fetch_instagram(account):
    token = ensure_access_token(account)
    pages = api_get(
        graph("me/accounts"),
        params={
            "fields": "id,name,access_token,instagram_business_account",
            "access_token": token,
        },
    )
    page = next((row for row in pages.get("data") or [] if row.get("instagram_business_account")), None)
    if page is None:
        raise ProviderError(
            "No professional Instagram account is linked to a Facebook Page you manage."
        )
    ig_id = page["instagram_business_account"]["id"]
    page_token = page.get("access_token") or token
    profile = api_get(
        graph(ig_id),
        params={
            "fields": "id,username,name,profile_picture_url,followers_count,follows_count,media_count",
            "access_token": page_token,
        },
    )
    media = api_get(
        graph(f"{ig_id}/media"),
        params={
            "fields": "id,caption,media_type,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count",
            "limit": 15,
            "access_token": page_token,
        },
    )
    kind_map = {"IMAGE": "image", "VIDEO": "reel", "CAROUSEL_ALBUM": "carousel"}
    contents = []
    for item in media.get("data") or []:
        caption = (item.get("caption") or "Instagram post").strip()
        contents.append(
            FetchedContent(
                external_id=item.get("id") or "",
                title=_title(caption),
                caption=caption,
                content_type=kind_map.get(item.get("media_type"), "post"),
                url=item.get("permalink") or "",
                thumbnail_url=item.get("thumbnail_url") or item.get("media_url") or "",
                published_at=_parse_dt(item.get("timestamp")),
                likes=as_int(item.get("like_count")),
                comments=as_int(item.get("comments_count")),
            )
        )
    username = profile.get("username") or ""
    return FetchResult(
        profile=FetchedProfile(
            external_id=str(profile.get("id") or ig_id),
            display_name=profile.get("name") or username or "Instagram",
            username=username,
            profile_url=f"https://www.instagram.com/{username}/" if username else "",
            avatar_url=profile.get("profile_picture_url") or "",
            raw=profile,
        ),
        snapshots=[
            FetchedSnapshot(
                captured_at=timezone.now(),
                followers=as_int(profile.get("followers_count")),
                following=as_int(profile.get("follows_count")),
                content_count=as_int(profile.get("media_count")) or len(contents),
                likes=sum(item.likes for item in contents),
                comments=sum(item.comments for item in contents),
            )
        ],
        contents=contents,
        resource_token=page_token,
    )


def fetch_twitter(account):
    token = ensure_access_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    profile = api_get(
        "https://api.twitter.com/2/users/me",
        headers=headers,
        params={
            "user.fields": "id,name,username,description,profile_image_url,public_metrics,url",
        },
    )
    data = profile.get("data") or {}
    if not data.get("id"):
        raise ProviderError("X did not return a profile for this account.")
    metrics = data.get("public_metrics") or {}
    username = data.get("username") or ""
    note = ""
    try:
        tweets = api_get(
            f"https://api.twitter.com/2/users/{data['id']}/tweets",
            headers=headers,
            params={
                "max_results": 10,
                "exclude": "retweets,replies",
                "tweet.fields": "created_at,public_metrics,text",
            },
        )
    except ProviderError as exc:
        tweets = {}
        note = f"Profile saved. Recent posts could not be read: {exc}"
    contents = []
    for tweet in tweets.get("data") or []:
        text = (tweet.get("text") or "Post").strip()
        stats = tweet.get("public_metrics") or {}
        contents.append(
            FetchedContent(
                external_id=str(tweet.get("id") or ""),
                title=_title(text),
                caption=text,
                content_type="post",
                url=f"https://x.com/{username}/status/{tweet.get('id')}" if username and tweet.get("id") else "",
                published_at=_parse_dt(tweet.get("created_at")),
                views=as_int(stats.get("impression_count")),
                likes=as_int(stats.get("like_count")),
                comments=as_int(stats.get("reply_count")),
                shares=as_int(stats.get("retweet_count")) + as_int(stats.get("quote_count")),
                saves=as_int(stats.get("bookmark_count")),
            )
        )
    return FetchResult(
        profile=FetchedProfile(
            external_id=str(data.get("id") or ""),
            display_name=data.get("name") or username or "X account",
            username=username,
            profile_url=f"https://x.com/{username}" if username else "",
            avatar_url=data.get("profile_image_url") or "",
            raw={
                "description": data.get("description") or "",
                "public_metrics": metrics,
            },
        ),
        snapshots=[
            FetchedSnapshot(
                captured_at=timezone.now(),
                followers=as_int(metrics.get("followers_count")),
                following=as_int(metrics.get("following_count")),
                content_count=as_int(metrics.get("tweet_count")),
                views=sum(item.views for item in contents),
                likes=sum(item.likes for item in contents),
                comments=sum(item.comments for item in contents),
                shares=sum(item.shares for item in contents),
            )
        ],
        contents=contents,
        note=note,
    )


def _youtube_videos(headers, uploads_id):
    listing = api_get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        headers=headers,
        params={"part": "contentDetails", "playlistId": uploads_id, "maxResults": 15},
    )
    ids = [
        (item.get("contentDetails") or {}).get("videoId")
        for item in listing.get("items") or []
    ]
    ids = [video_id for video_id in ids if video_id]
    if not ids:
        return []
    details = api_get(
        "https://www.googleapis.com/youtube/v3/videos",
        headers=headers,
        params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids)},
    )
    contents = []
    for video in details.get("items") or []:
        snippet = video.get("snippet") or {}
        statistics = video.get("statistics") or {}
        title = snippet.get("title") or "Untitled video"
        duration = (video.get("contentDetails") or {}).get("duration") or ""
        kind = "short" if _iso_seconds(duration) and _iso_seconds(duration) <= 60 else "video"
        thumb = ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url") or ""
        contents.append(
            FetchedContent(
                external_id=video.get("id") or "",
                title=_title(title),
                caption=(snippet.get("description") or "")[:4000],
                content_type=kind,
                url=f"https://www.youtube.com/watch?v={video.get('id')}",
                thumbnail_url=thumb,
                published_at=_parse_dt(snippet.get("publishedAt")),
                views=as_int(statistics.get("viewCount")),
                likes=as_int(statistics.get("likeCount")),
                comments=as_int(statistics.get("commentCount")),
            )
        )
    return contents


def _youtube_history(headers, current_followers, video_count):
    end = timezone.localdate()
    start = end - timedelta(days=28)
    try:
        report = api_get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            headers=headers,
            params={
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": "views,likes,comments,shares,subscribersGained",
                "dimensions": "day",
            },
        )
    except ProviderError:
        return []
    rows = report.get("rows") or []
    headers_row = [column.get("name") for column in report.get("columnHeaders") or []]
    if "day" not in headers_row:
        return []
    parsed = []
    for row in rows:
        record = dict(zip(headers_row, row))
        day = parse_datetime(f"{record.get('day')}T12:00:00")
        if day is None:
            continue
        if timezone.is_naive(day):
            day = timezone.make_aware(day)
        parsed.append(
            {
                "captured_at": day,
                "likes": as_int(record.get("likes")),
                "comments": as_int(record.get("comments")),
                "shares": as_int(record.get("shares")),
                "gained": as_int(record.get("subscribersGained")),
            }
        )
    followers = current_followers
    built = []
    for record in reversed(parsed):
        built.append((record, followers))
        followers = max(0, followers - record["gained"])
    snapshots = []
    for record, audience in reversed(built):
        snapshots.append(
            FetchedSnapshot(
                captured_at=record["captured_at"],
                followers=audience,
                content_count=video_count,
                likes=record["likes"],
                comments=record["comments"],
                shares=record["shares"],
            )
        )
    return snapshots


def _summary_count(payload):
    return as_int(((payload or {}).get("summary") or {}).get("total_count"))


def _title(text):
    clean = " ".join(text.split())
    return (clean[:297] + "...") if len(clean) > 300 else clean or "Untitled"


def _parse_dt(value):
    if not value:
        return None
    parsed = parse_datetime(str(value).replace("Z", "+00:00"))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _iso_seconds(duration):
    if not duration or not duration.startswith("PT"):
        return 0
    hours = minutes = seconds = 0
    number = ""
    for char in duration[2:]:
        if char.isdigit():
            number += char
            continue
        amount = int(number or 0)
        number = ""
        if char == "H":
            hours = amount
        elif char == "M":
            minutes = amount
        elif char == "S":
            seconds = amount
    return hours * 3600 + minutes * 60 + seconds
