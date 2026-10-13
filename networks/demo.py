from datetime import datetime, time, timedelta

from django.utils import timezone

from networks.models import ContentItem, MetricSnapshot, SocialAccount
from networks.platforms import PLATFORMS

PROFILES = {
    "instagram": {
        "external_id": "sample-instagram",
        "display_name": "Northline Studio",
        "username": "northline.studio",
        "profile_url": "",
        "followers": 18420,
        "following": 318,
        "content_count": 146,
        "views": 902400,
        "likes": 48620,
        "comments": 3144,
        "shares": 1860,
        "impressions": 1204400,
        "reach": 640200,
    },
    "facebook": {
        "external_id": "sample-facebook",
        "display_name": "Northline Studio",
        "username": "northline.studio",
        "profile_url": "",
        "followers": 12680,
        "following": 40,
        "content_count": 98,
        "views": 410250,
        "likes": 22110,
        "comments": 1980,
        "shares": 2460,
        "impressions": 688000,
        "reach": 301400,
    },
    "youtube": {
        "external_id": "sample-youtube",
        "display_name": "Northline",
        "username": "@northline",
        "profile_url": "",
        "followers": 42800,
        "following": 0,
        "content_count": 64,
        "views": 2104800,
        "likes": 90440,
        "comments": 6120,
        "shares": 2380,
        "impressions": 2500000,
        "reach": 980000,
    },
    "twitter": {
        "external_id": "sample-twitter",
        "display_name": "Northline Studio",
        "username": "northline",
        "profile_url": "",
        "followers": 7340,
        "following": 512,
        "content_count": 52,
        "views": 186400,
        "likes": 9420,
        "comments": 860,
        "shares": 1540,
        "impressions": 240800,
        "reach": 112600,
    },
}

POSTS = {
    "instagram": [
        ("reel-light", "Reel: one light, three looks", "reel", 18, 84200, 6100, 240, 860, 120400),
        ("pack-order", "Packing a client order", "reel", 15, 56400, 3900, 180, 420, 80200),
        ("morning", "Morning light on the studio table", "image", 12, 22100, 1840, 66, 310, 41000),
        ("grades", "Carousel: three color grades", "carousel", 9, 19840, 1420, 90, 260, 36000),
        ("process", "A quiet note on process", "image", 6, 12400, 980, 40, 120, 25000),
        ("thursday", "Behind the shoot, Thursday", "image", 3, 9800, 760, 28, 90, 18400),
    ],
    "facebook": [
        ("workshop", "Workshop dates for November", "post", 16, 18400, 960, 140, 420, 42000),
        ("bakery", "Client story: the bakery rebrand", "post", 13, 15220, 1100, 96, 380, 30100),
        ("live-notes", "Notes from yesterday's shoot", "post", 10, 8640, 540, 70, 160, 19000),
        ("props", "New props on the shelf", "image", 7, 6420, 410, 22, 80, 14000),
        ("question", "What should we film next?", "post", 4, 12100, 380, 210, 90, 26000),
        ("hours", "Weekend studio hours", "post", 2, 4300, 190, 18, 40, 9000),
    ],
    "youtube": [
        ("plan-month", "How we plan a month of content", "video", 20, 186400, 9200, 640, 410, 0),
        ("tour", "Studio tour, ten minutes", "video", 17, 142800, 7100, 480, 260, 0),
        ("grade", "Color grading a product film", "video", 11, 98600, 5400, 390, 180, 0),
        ("short-light", "Short: one light, three looks", "short", 8, 240500, 14200, 310, 890, 0),
        ("fifty", "What we learned from 50 posts", "video", 5, 76400, 3600, 280, 140, 0),
        ("thumbs", "Thumbnail test, side by side", "video", 2, 41200, 1980, 150, 70, 0),
    ],
    "twitter": [
        ("hiring", "We are hiring a junior editor", "post", 19, 22400, 860, 74, 210, 31000),
        ("rebrand", "Notes on a rebrand that took six weeks", "post", 14, 18600, 1240, 96, 340, 28000),
        ("process-x", "The checklist we use before a shoot", "post", 11, 9800, 640, 40, 120, 16000),
        ("case", "Case study: a bakery's new film", "post", 8, 14220, 980, 88, 260, 21000),
        ("team", "Thursday in the studio", "post", 5, 6400, 420, 22, 60, 11000),
        ("asks", "The first question clients ask", "post", 1, 11200, 760, 130, 180, 19000),
    ],
}


def connect_sample(user, platform):
    if platform not in PLATFORMS:
        raise KeyError(platform)
    SocialAccount.objects.filter(user=user, platform=platform).delete()
    profile = PROFILES[platform]
    account = SocialAccount.objects.create(
        user=user,
        platform=platform,
        external_id=profile["external_id"],
        display_name=profile["display_name"],
        username=profile["username"],
        profile_url=profile["profile_url"],
        is_sample=True,
        status_note="Sample data stored locally so you can explore analytics before connecting a live account.",
        last_synced_at=timezone.now(),
        raw_profile={"source": "sample"},
    )
    today = timezone.localdate()
    snapshots = []
    for age in range(29, -1, -1):
        day = today - timedelta(days=age)
        index = 29 - age
        scale = 0.82 + (0.18 * index / 29)
        snapshots.append(
            MetricSnapshot(
                account=account,
                captured_at=timezone.make_aware(datetime.combine(day, time(12, 0))),
                followers=int(profile["followers"] * scale),
                following=profile["following"],
                content_count=profile["content_count"],
                views=int(profile["views"] * scale),
                likes=int(profile["likes"] * scale),
                comments=int(profile["comments"] * scale),
                shares=int(profile["shares"] * scale),
                impressions=int(profile["impressions"] * scale),
                reach=int(profile["reach"] * scale),
            )
        )
    MetricSnapshot.objects.bulk_create(snapshots)
    items = []
    for external_id, title, kind, days_ago, views, likes, comments, shares, impressions in POSTS[platform]:
        items.append(
            ContentItem(
                account=account,
                external_id=external_id,
                title=title,
                caption=title,
                content_type=kind,
                published_at=timezone.now() - timedelta(days=days_ago, hours=4),
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                impressions=impressions,
                saves=max(0, likes // 8),
                watch_time_seconds=views * 12 if kind in {"video", "short", "reel"} else 0,
            )
        )
    ContentItem.objects.bulk_create(items)
    return account


def tick_sample(account):
    last = account.snapshots.order_by("-captured_at").first()
    if last is None:
        return connect_sample(account.user, account.platform)
    MetricSnapshot.objects.create(
        account=account,
        captured_at=timezone.now(),
        followers=last.followers + max(1, last.followers // 400),
        following=last.following,
        content_count=last.content_count,
        views=last.views + max(20, last.views // 300),
        likes=last.likes + max(1, last.likes // 400),
        comments=last.comments + 2,
        shares=last.shares + 1,
        impressions=last.impressions + max(30, last.impressions // 300),
        reach=last.reach + max(10, last.reach // 300),
    )
    account.last_synced_at = timezone.now()
    account.last_error = ""
    account.save(update_fields=["last_synced_at", "last_error"])
    return account
