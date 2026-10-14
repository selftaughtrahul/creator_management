from django.db.models import Sum
from django.utils import timezone

from creators.metrics import STALE_AFTER, creator_metrics
from networks.models import ContentItem

BRAND_SAFETY_TERMS = (
    "hate",
    "scam",
    "fraud",
    "gambling",
    "casino",
    "weapon",
    "explicit",
    "nsfw",
)


def bot_risk_indicators(creator):
    totals = ContentItem.objects.filter(account__user=creator.user).aggregate(
        likes=Sum("likes"),
        comments=Sum("comments"),
        views=Sum("views"),
        impressions=Sum("impressions"),
    )
    likes = totals.get("likes") or 0
    comments = totals.get("comments") or 0
    views = totals.get("views") or totals.get("impressions") or 0
    metrics = creator_metrics(creator)
    followers = metrics["followers"]["value"]
    indicators = []
    if likes and comments / likes >= 2:
        indicators.append(
            {
                "code": "comment_like_ratio",
                "label": "Comments are at least twice likes across stored posts.",
                "evidence": f"{comments} comments / {likes} likes",
            }
        )
    engagement = metrics["engagement"]["value"]
    if engagement is not None and engagement >= 20:
        indicators.append(
            {
                "code": "high_engagement",
                "label": "Stored engagement rate is unusually high.",
                "evidence": f"{engagement}% from ContentItem aggregate",
            }
        )
    if followers and views and followers > 0 and views / followers < 0.01:
        indicators.append(
            {
                "code": "low_view_to_follower",
                "label": "Stored views are very low relative to followers.",
                "evidence": f"{views} views / {followers} followers",
            }
        )
    return {
        "kind": "indicator",
        "confirmed": False,
        "indicators": indicators,
        "note": "These are ratio checks on stored rows, not a confirmed bot verdict.",
    }


def brand_safety_hits(creator):
    hits = []
    items = ContentItem.objects.filter(account__user=creator.user).only("title", "caption", "pk")
    for item in items:
        blob = f"{item.title}\n{item.caption}".lower()
        for term in BRAND_SAFETY_TERMS:
            if term in blob:
                snippet = _snippet(item.caption or item.title, term)
                hits.append(
                    {
                        "term": term,
                        "content_id": item.pk,
                        "snippet": snippet,
                    }
                )
    return {
        "kind": "keyword_hit",
        "confirmed": False,
        "hits": hits,
        "note": "Keyword matches in stored captions. Not a legal or policy determination.",
    }


def data_quality(creator):
    metrics = creator_metrics(creator)
    checks = [
        {"field": "location", "ok": bool(creator.location), "source": "CreatorProfile"},
        {"field": "bio", "ok": bool(creator.bio), "source": "CreatorProfile"},
        {"field": "niche", "ok": metrics["niche"]["value"] is not None, "source": metrics["niche"]["source"]},
        {
            "field": "followers",
            "ok": metrics["followers"]["value"] is not None,
            "source": metrics["followers"]["source"],
        },
        {
            "field": "fresh_snapshot",
            "ok": bool(metrics["last_synced_at"] and timezone.now() - metrics["last_synced_at"] <= STALE_AFTER),
            "source": metrics["last_synced_at"].isoformat() if metrics["last_synced_at"] else "unavailable",
        },
        {"field": "discoverable_consent", "ok": creator.discoverable and creator.share_metrics, "source": "CreatorProfile"},
    ]
    return {"checks": checks, "missing": [row["field"] for row in checks if not row["ok"]]}


def _snippet(text, term, radius=40):
    lowered = text.lower()
    index = lowered.find(term)
    if index < 0:
        return text[:80]
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    return text[start:end]
