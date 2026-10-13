from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from networks.models import ContentItem
from networks.platforms import PLATFORMS


def build_dashboard(user):
    accounts = list(user.social_accounts.all())
    snapshots = _snapshots(accounts)
    latest = _latest_by_account(snapshots)
    rows = []
    audience = views = likes = comments = shares = posts = 0
    for account in accounts:
        snap = latest.get(account.pk)
        content_count = account.contents.count()
        rows.append(
            {
                "account": account,
                "meta": PLATFORMS[account.platform],
                "snapshot": snap,
                "posts": content_count,
                "rate": _content_rate(account),
            }
        )
        if snap:
            audience += snap.followers
            views += snap.views
            likes += snap.likes
            comments += snap.comments
            shares += snap.shares
        posts += content_count
    reactions = likes + comments + shares
    content = (
        ContentItem.objects.filter(account__user=user)
        .select_related("account")
        .order_by("-views", "-likes")
    )
    return {
        "rows": rows,
        "has_sample": any(account.is_sample for account in accounts),
        "totals": {
            "accounts": len(accounts),
            "audience": audience,
            "views": views,
            "posts": posts,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "reactions": reactions,
            "rate": _rate(reactions, views),
        },
        "charts": {
            "audience": _audience_chart(accounts, snapshots),
            "mix": _mix_chart(rows),
        },
        "top_content": list(content[:6]),
        "recent_content": list(content.order_by("-published_at")[:6]),
    }


def build_platform(account):
    snapshots = list(account.snapshots.order_by("captured_at"))
    latest = snapshots[-1] if snapshots else None
    contents = account.contents.all()
    totals = contents.aggregate(
        views=Sum("views"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        shares=Sum("shares"),
        saves=Sum("saves"),
        impressions=Sum("impressions"),
    )
    reactions = sum(totals.get(key) or 0 for key in ("likes", "comments", "shares", "saves"))
    view_total = totals.get("views") or 0
    return {
        "account": account,
        "meta": PLATFORMS[account.platform],
        "snapshot": latest,
        "snapshots": snapshots,
        "totals": {
            "views": view_total,
            "likes": totals.get("likes") or 0,
            "comments": totals.get("comments") or 0,
            "shares": totals.get("shares") or 0,
            "saves": totals.get("saves") or 0,
            "impressions": totals.get("impressions") or 0,
            "reactions": reactions,
            "rate": _rate(reactions, view_total or (latest.views if latest else 0)),
        },
        "charts": {
            "audience": _single_audience_chart(account, snapshots),
            "posts": _posts_chart(account, list(contents.order_by("-views")[:6])),
        },
    }


def build_content(item):
    siblings = item.account.contents.all()
    view_total = siblings.aggregate(views=Sum("views")).get("views") or 0
    share = round(item.views / view_total * 100, 1) if view_total else 0
    parts = [
        ("Likes", item.likes, "#c2410c"),
        ("Comments", item.comments, "#0f766e"),
        ("Shares", item.shares, "#1d4ed8"),
        ("Saves", item.saves, "#a16207"),
    ]
    parts = [part for part in parts if part[1]]
    chart = None
    if parts:
        chart = {
            "type": "doughnut",
            "data": {
                "labels": [part[0] for part in parts],
                "datasets": [
                    {
                        "data": [part[1] for part in parts],
                        "backgroundColor": [part[2] for part in parts],
                        "borderWidth": 0,
                    }
                ],
            },
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "plugins": {"legend": {"position": "bottom"}},
            },
        }
    return {
        "item": item,
        "account": item.account,
        "meta": PLATFORMS[item.account.platform],
        "share": share,
        "watch_label": _watch_label(item.watch_time_seconds),
        "charts": {"mix": chart},
    }


def _snapshots(accounts):
    if not accounts:
        return []
    start = timezone.now() - timedelta(days=45)
    from networks.models import MetricSnapshot

    return list(
        MetricSnapshot.objects.filter(account__in=accounts, captured_at__gte=start)
        .select_related("account")
        .order_by("captured_at")
    )


def _latest_by_account(snapshots):
    latest = {}
    for snapshot in snapshots:
        latest[snapshot.account_id] = snapshot
    return latest


def _content_rate(account):
    totals = account.contents.aggregate(
        views=Sum("views"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        shares=Sum("shares"),
        saves=Sum("saves"),
    )
    reactions = sum(totals.get(key) or 0 for key in ("likes", "comments", "shares", "saves"))
    return _rate(reactions, totals.get("views") or 0)


def _rate(reactions, views):
    if not views:
        return 0
    return round(reactions / views * 100, 1)


def _audience_chart(accounts, snapshots):
    if not snapshots:
        return None
    days = []
    for snapshot in snapshots:
        day = timezone.localtime(snapshot.captured_at).date()
        if day not in days:
            days.append(day)
    days = days[-30:]
    datasets = []
    for account in accounts:
        points = {}
        for snapshot in snapshots:
            if snapshot.account_id != account.id:
                continue
            points[timezone.localtime(snapshot.captured_at).date()] = snapshot.followers
        data = [points.get(day) for day in days]
        if not any(value is not None for value in data):
            continue
        color = PLATFORMS[account.platform]["color"]
        datasets.append(
            {
                "label": PLATFORMS[account.platform]["label"],
                "data": data,
                "borderColor": color,
                "backgroundColor": color,
                "tension": 0.35,
                "pointRadius": 0,
                "borderWidth": 2,
                "spanGaps": True,
            }
        )
    if not datasets:
        return None
    return {
        "type": "line",
        "data": {"labels": [day.strftime("%d %b") for day in days], "datasets": datasets},
        "options": _line_options(),
    }


def _single_audience_chart(account, snapshots):
    if len(snapshots) < 2:
        return None
    color = PLATFORMS[account.platform]["color"]
    return {
        "type": "line",
        "data": {
            "labels": [timezone.localtime(snapshot.captured_at).strftime("%d %b") for snapshot in snapshots[-30:]],
            "datasets": [
                {
                    "label": PLATFORMS[account.platform]["audience_label"],
                    "data": [snapshot.followers for snapshot in snapshots[-30:]],
                    "borderColor": color,
                    "backgroundColor": color,
                    "fill": False,
                    "tension": 0.35,
                    "pointRadius": 0,
                    "borderWidth": 2,
                }
            ],
        },
        "options": _line_options(legend=False),
    }


def _posts_chart(account, items):
    items = [item for item in items if item.views]
    if not items:
        return None
    return {
        "type": "bar",
        "data": {
            "labels": [_short(item.title) for item in items],
            "datasets": [
                {
                    "label": "Views",
                    "data": [item.views for item in items],
                    "backgroundColor": PLATFORMS[account.platform]["color"],
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "indexAxis": "y",
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"x": {"beginAtZero": True, "ticks": {"precision": 0}}},
        },
    }


def _mix_chart(rows):
    labels = []
    data = []
    colors = []
    for row in rows:
        snap = row["snapshot"]
        if not snap or not snap.followers:
            continue
        labels.append(row["meta"]["label"])
        data.append(snap.followers)
        colors.append(row["meta"]["color"])
    if not data:
        return None
    return {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{"data": data, "backgroundColor": colors, "borderWidth": 0}],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"position": "bottom"}},
        },
    }


def _line_options(legend=True):
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {"legend": {"display": legend, "position": "bottom"}},
        "scales": {
            "y": {"beginAtZero": False, "ticks": {"precision": 0}},
            "x": {"grid": {"display": False}},
        },
    }


def _short(title):
    return title if len(title) <= 42 else title[:39] + "..."


def _watch_label(seconds):
    seconds = seconds or 0
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
