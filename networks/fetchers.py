from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from networks.http import ProviderError, api_get, as_int, graph, ig_graph
from networks.oauth import ensure_access_token


@dataclass
class FetchedProfile:
    external_id: str
    display_name: str
    username: str = ""
    profile_url: str = ""
    avatar_url: str = ""


@dataclass
class AudienceSlice:
    dimension: str
    segment: str
    label: str
    count: int
    basis: str = "followers"


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
    breakdowns: list = field(default_factory=list)


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
            breakdowns=_youtube_demographics(headers, followers),
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
    note = ""
    try:
        posts = api_get(
            graph(f"{page['id']}/posts"),
            params={
                "fields": "id,message,created_time,permalink_url,full_picture,shares,likes.summary(true),comments.summary(true)",
                "limit": 15,
                "access_token": page_token,
            },
        )
    except ProviderError:
        posts = {}
        note = (
            "Page saved. Recent posts need the pages_read_engagement permission. "
            "Add it to the Awisee login configuration, then connect Facebook again."
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
    snapshots = _meta_audience_history(
        str(page.get("id") or ""),
        page_token,
        metrics=("page_follows", "page_impressions"),
        followers_key="page_follows",
        impressions_key="page_impressions",
        current_followers=followers,
        content_count=len(contents),
    )
    snapshots.append(
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=followers,
            content_count=len(contents),
            likes=sum(item.likes for item in contents),
            comments=sum(item.comments for item in contents),
            shares=sum(item.shares for item in contents),
            breakdowns=_facebook_demographics(str(page.get("id") or ""), page_token),
        )
    )
    return FetchResult(
        profile=FetchedProfile(
            external_id=str(page.get("id") or ""),
            display_name=page.get("name") or "Facebook Page",
            username=page.get("name") or "",
            profile_url=page.get("link") or "",
            avatar_url=picture,
        ),
        snapshots=snapshots,
        contents=contents,
        note=note,
    )


def fetch_instagram(account):
    token = ensure_access_token(account)
    profile = api_get(
        ig_graph("me"),
        params={
            "fields": "user_id,username,name,account_type,profile_picture_url,followers_count,follows_count,media_count",
            "access_token": token,
        },
    )
    ig_id = str(profile.get("user_id") or profile.get("id") or "")
    if not ig_id:
        raise ProviderError("Instagram did not return this account.")
    if (profile.get("account_type") or "").upper() == "PERSONAL":
        raise ProviderError(
            "This Instagram account is personal. Switch it to a professional account in the Instagram app, then connect again."
        )
    try:
        media = api_get(
            ig_graph(f"{ig_id}/media"),
            params={
                "fields": "id,caption,media_type,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count",
                "limit": 15,
                "access_token": token,
            },
        )
    except ProviderError:
        media = {}
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
    followers = as_int(profile.get("followers_count"))
    following = as_int(profile.get("follows_count"))
    content_count = as_int(profile.get("media_count")) or len(contents)
    snapshots = _meta_audience_history(
        str(ig_id),
        token,
        metrics=("follower_count", "reach"),
        followers_key="follower_count",
        reach_key="reach",
        current_followers=followers,
        following=following,
        content_count=content_count,
        endpoint=ig_graph,
    )
    snapshots.append(
        FetchedSnapshot(
            captured_at=timezone.now(),
            followers=followers,
            following=following,
            content_count=content_count,
            likes=sum(item.likes for item in contents),
            comments=sum(item.comments for item in contents),
            breakdowns=_instagram_demographics(str(ig_id), token),
        )
    )
    return FetchResult(
        profile=FetchedProfile(
            external_id=str(profile.get("user_id") or profile.get("id") or ig_id),
            display_name=profile.get("name") or username or "Instagram",
            username=username,
            profile_url=f"https://www.instagram.com/{username}/" if username else "",
            avatar_url=profile.get("profile_picture_url") or "",
        ),
        snapshots=snapshots,
        contents=contents,
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
                "views": as_int(record.get("views")),
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
                views=record["views"],
                likes=record["likes"],
                comments=record["comments"],
                shares=record["shares"],
            )
        )
    return snapshots


def _meta_audience_history(
    path,
    token,
    *,
    metrics,
    followers_key,
    current_followers,
    impressions_key="",
    reach_key="",
    following=0,
    content_count=0,
    endpoint=graph,
):
    days = _insight_days(path, token, metrics, endpoint=endpoint)
    if not days and len(metrics) > 1:
        days = _insight_days(path, token, metrics[:1], endpoint=endpoint)
    ordered = sorted(days.items())
    rows = []
    for _day, values in ordered:
        rows.append(
            {
                "captured_at": values["captured_at"],
                "followers": as_int(values.get(followers_key)),
                "impressions": as_int(values.get(impressions_key)) if impressions_key else 0,
                "reach": as_int(values.get(reach_key)) if reach_key else 0,
            }
        )
    if _series_is_daily_gain(rows, current_followers):
        running = current_followers
        for row in reversed(rows):
            gained = row["followers"]
            row["followers"] = running
            running = max(0, running - gained)
    return [
        FetchedSnapshot(
            captured_at=row["captured_at"],
            followers=row["followers"],
            following=following,
            content_count=content_count,
            impressions=row["impressions"],
            reach=row["reach"],
        )
        for row in rows
    ]


def _insight_days(path, token, metrics, endpoint=graph):
    end = timezone.now()
    start = end - timedelta(days=28)
    try:
        report = api_get(
            endpoint(f"{path}/insights"),
            params={
                "metric": ",".join(metrics),
                "period": "day",
                "since": int(start.timestamp()),
                "until": int(end.timestamp()),
                "access_token": token,
            },
        )
    except ProviderError:
        return {}
    days = {}
    for metric in report.get("data") or []:
        name = metric.get("name")
        if not name:
            continue
        for point in metric.get("values") or []:
            captured = _parse_dt(point.get("end_time"))
            value = point.get("value")
            if captured is None or isinstance(value, (dict, list)):
                continue
            day = timezone.localtime(captured).date()
            days.setdefault(day, {"captured_at": captured})
            days[day][name] = value
    return days


def _series_is_daily_gain(rows, current_followers):
    if not rows or not current_followers:
        return False
    peak = max(row["followers"] for row in rows)
    return peak < current_followers / 2


def _instagram_demographics(ig_id, token):
    countries = _demographic_breakdown(ig_id, token, "country", endpoint=ig_graph)
    genders = _demographic_breakdown(ig_id, token, "gender", endpoint=ig_graph)
    return country_slices(countries) + gender_slices(genders)


def _facebook_demographics(page_id, token):
    countries = {}
    for metric in ("page_follows_country", "page_fans_country"):
        countries = _lifetime_dict(page_id, token, metric)
        if countries:
            break
    genders = _lifetime_dict(page_id, token, "page_fans_gender_age")
    return country_slices(countries) + gender_slices(genders)


def _youtube_demographics(headers, followers):
    end = timezone.localdate()
    start = end - timedelta(days=28)
    countries = _youtube_share(headers, "country", start, end, followers)
    genders = _youtube_share(headers, "gender", start, end, followers)
    return country_slices(countries, basis="estimated") + gender_slices(genders, basis="estimated")


def _demographic_breakdown(path, token, breakdown, endpoint=graph):
    try:
        report = api_get(
            endpoint(f"{path}/insights"),
            params={
                "metric": "follower_demographics",
                "period": "lifetime",
                "metric_type": "total_value",
                "breakdown": breakdown,
                "access_token": token,
            },
        )
    except ProviderError:
        return {}
    counts = {}
    for metric in report.get("data") or []:
        total = metric.get("total_value") or {}
        groups = total.get("breakdowns") or []
        if not groups and isinstance(metric.get("values"), list):
            groups = metric.get("values") or []
        for group in groups:
            results = group.get("results") or []
            if not results and isinstance(group.get("value"), dict):
                for key, value in group["value"].items():
                    counts[str(key)] = counts.get(str(key), 0) + as_int(value)
            for result in results:
                values = result.get("dimension_values") or []
                key = str(values[0]) if values else ""
                if key:
                    counts[key] = counts.get(key, 0) + as_int(result.get("value"))
    return counts


def _lifetime_dict(path, token, metric):
    try:
        report = api_get(
            graph(f"{path}/insights"),
            params={"metric": metric, "period": "lifetime", "access_token": token},
        )
    except ProviderError:
        return {}
    latest = None
    for row in report.get("data") or []:
        for point in row.get("values") or []:
            if isinstance(point.get("value"), dict):
                latest = point["value"]
    return latest or {}


def _youtube_share(headers, dimension, start, end, followers):
    try:
        report = api_get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            headers=headers,
            params={
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": "viewerPercentage",
                "dimensions": dimension,
            },
        )
    except ProviderError:
        return {}
    headers_row = [column.get("name") for column in report.get("columnHeaders") or []]
    if dimension not in headers_row or "viewerPercentage" not in headers_row:
        return {}
    counts = {}
    for row in report.get("rows") or []:
        record = dict(zip(headers_row, row))
        key = str(record.get(dimension) or "")
        if not key:
            continue
        try:
            share = float(record.get("viewerPercentage") or 0)
        except (TypeError, ValueError):
            share = 0
        counts[key] = int(round(share / 100 * followers))
    return {key: count for key, count in counts.items() if count}


def country_slices(counts, basis="followers"):
    from networks.countries import country_label

    slices = []
    for code, count in counts.items():
        amount = as_int(count)
        if not amount:
            continue
        segment = str(code).strip().upper()
        slices.append(AudienceSlice("country", segment, country_label(segment), amount, basis))
    return slices


def gender_slices(counts, basis="followers"):
    labels = {"female": "Female", "male": "Male", "unknown": "Unknown"}
    buckets = {"female": 0, "male": 0, "unknown": 0}
    for key, count in counts.items():
        buckets[gender_bucket(key)] += as_int(count)
    return [
        AudienceSlice("gender", key, labels[key], amount, basis)
        for key, amount in buckets.items()
        if amount
    ]


def gender_bucket(key):
    text = str(key or "").strip().lower().replace("_", "").replace(" ", "")
    if text.startswith("f"):
        return "female"
    if text.startswith("m"):
        return "male"
    return "unknown"


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
