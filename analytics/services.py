from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from networks.charts import bar, doughnut
from networks.demo import ensure_sample_demographics
from networks.models import ContentItem, reaction_rate
from networks.platforms import PLATFORMS


def build_dashboard(user):
    accounts = list(user.social_accounts.prefetch_related("contents"))
    for account in accounts:
        ensure_sample_demographics(account)
    snapshots = _snapshots(accounts)
    latest = _latest_by_account(snapshots)
    rows = []
    audience = views = likes = comments = shares = posts = 0
    for account in accounts:
        snap = latest.get(account.pk)
        content_count = account.contents.count()
        history = [snapshot for snapshot in snapshots if snapshot.account_id == account.pk]
        rows.append(
            {
                "account": account,
                "meta": PLATFORMS[account.platform],
                "snapshot": snap,
                "posts": content_count,
                "rate": _content_rate(account),
                "history": list(reversed(_with_deltas(history)[-14:])),
                "demographics": _demographics(history, PLATFORMS[account.platform]["color"]),
                "stats": _platform_stats(account, history, snap),
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
            "rate": reaction_rate(reactions, views),
        },
        "charts": {
            "audience": _audience_chart(accounts, snapshots),
            "mix": _mix_chart(rows),
        },
        "top_content": list(content[:6]),
        "recent_content": list(content.order_by("-published_at")[:6]),
    }


def build_platform(account):
    ensure_sample_demographics(account)
    snapshots = list(account.snapshots.prefetch_related("breakdowns").order_by("captured_at"))
    latest = snapshots[-1] if snapshots else None
    contents = account.contents.all()
    stats = _platform_stats(account, snapshots, latest)
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
    demographics = _demographics(snapshots, PLATFORMS[account.platform]["color"])
    return {
        "account": account,
        "meta": PLATFORMS[account.platform],
        "snapshot": latest,
        "snapshots": snapshots,
        "history": list(reversed(_with_deltas(snapshots)[-30:])),
        "demographics": demographics,
        "stats": stats,
        "stats_rows": [
            {
                "account": account,
                "meta": PLATFORMS[account.platform],
                "stats": stats,
            }
        ],
        "totals": {
            "views": view_total,
            "likes": totals.get("likes") or 0,
            "comments": totals.get("comments") or 0,
            "shares": totals.get("shares") or 0,
            "saves": totals.get("saves") or 0,
            "impressions": totals.get("impressions") or 0,
            "reactions": reactions,
            "rate": reaction_rate(
                reactions,
                view_total or (latest.views if latest else 0),
                totals.get("impressions") or 0,
            ),
        },
        "charts": {
            "audience": _single_audience_chart(account, snapshots),
            "posts": _posts_chart(account, list(contents.order_by("-views")[:6])),
            "countries": demographics["country_chart"],
            "genders": demographics["gender_chart"],
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


def _platform_stats(account, history, latest):
    cutoff = timezone.now() - timedelta(days=30)
    posts = [item for item in account.contents.all() if item.published_at and item.published_at >= cutoff]
    reactions = sum(item.reactions for item in posts)
    views = sum(item.views or item.impressions for item in posts)
    rate = reaction_rate(reactions, views)
    published = [item.published_at for item in account.contents.all() if item.published_at]
    count = len(posts)
    return {
        "followers": latest.followers if latest else 0,
        "growth": _growth(history, latest),
        "last_activity": _ago(max(published) if published else None),
        "activity": count,
        "rate": rate,
        "avg_engagement": round(reactions / count) if count else 0,
        "avg_views": round(views / count) if count else 0,
        "habit": _habit(count),
        "score": _score(rate, count),
    }


def _growth(history, latest):
    if not latest or len(history) < 2:
        return None
    target = timezone.localtime(latest.captured_at) - timedelta(days=30)
    baseline = history[0]
    for snapshot in history:
        if snapshot.pk == latest.pk:
            break
        if timezone.localtime(snapshot.captured_at) <= target:
            baseline = snapshot
    if baseline.pk == latest.pk:
        return None
    delta = latest.followers - baseline.followers
    percent = round(delta / baseline.followers * 100, 1) if baseline.followers else 0
    return {"absolute": delta, "percent": percent, "days": max(1, (latest.captured_at - baseline.captured_at).days)}


def _score(rate, posts):
    if posts <= 0:
        return None
    return int(round(min(70, rate * 10) + min(30, posts * 2)))


def _habit(count):
    if count <= 0:
        return "No posts"
    if count >= 26:
        return "Every day"
    if count == 1:
        return "Once this month"
    interval = max(1, round(30 / count))
    if interval <= 1:
        return "Every day"
    return f"Every {interval} days"


def _ago(moment):
    if moment is None:
        return "—"
    days = (timezone.now() - moment).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 30:
        return f"{days} days ago"
    return timezone.localtime(moment).strftime("%d %b %Y")


def _snapshots(accounts):
    if not accounts:
        return []
    start = timezone.now() - timedelta(days=45)
    from networks.models import MetricSnapshot

    return list(
        MetricSnapshot.objects.filter(account__in=accounts, captured_at__gte=start)
        .select_related("account")
        .prefetch_related("breakdowns")
        .order_by("captured_at")
    )


def _demographics(history, color="#0f766e"):
    empty = {
        "countries": [],
        "genders": [],
        "estimated": False,
        "has_previous": False,
        "country_chart": None,
        "gender_chart": None,
    }
    if not history:
        return empty
    latest = history[-1]
    previous = history[-2] if len(history) > 1 else None
    prior = {}
    if previous is not None:
        for row in previous.breakdowns.all():
            prior[(row.dimension, row.segment)] = row.count
    followers = latest.followers or 0
    countries = []
    genders = []
    estimated = False
    for row in latest.breakdowns.all():
        estimated = estimated or row.basis == "estimated"
        delta = None if previous is None else row.count - prior.get((row.dimension, row.segment), 0)
        item = {
            "label": row.label,
            "count": row.count,
            "delta": delta,
            "share": round(row.count / followers * 100, 1) if followers else 0,
        }
        if row.dimension == "country":
            countries.append(item)
        elif row.dimension == "gender":
            genders.append(item)
    countries.sort(key=lambda item: item["count"], reverse=True)
    genders.sort(key=lambda item: item["count"], reverse=True)
    countries = countries[:10]
    return {
        "countries": countries,
        "genders": genders,
        "estimated": estimated,
        "has_previous": previous is not None,
        "country_chart": bar([item["label"] for item in countries[:8]], [item["count"] for item in countries[:8]], color),
        "gender_chart": doughnut([item["label"] for item in genders], [item["count"] for item in genders]),
    }


def _with_deltas(snapshots):
    rows = []
    previous = None
    for snapshot in snapshots:
        delta = None if previous is None else snapshot.followers - previous.followers
        rows.append({"snapshot": snapshot, "delta": delta})
        previous = snapshot
    return rows


def _latest_by_account(snapshots):
    latest = {}
    for snapshot in snapshots:
        latest[snapshot.account_id] = snapshot
    return latest


def _content_rate(account):
    totals = account.contents.aggregate(
        views=Sum("views"),
        impressions=Sum("impressions"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        shares=Sum("shares"),
        saves=Sum("saves"),
    )
    reactions = sum(totals.get(key) or 0 for key in ("likes", "comments", "shares", "saves"))
    return reaction_rate(reactions, totals.get("views") or 0, totals.get("impressions") or 0)


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
