from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Prefetch

from creators.metrics import creator_metrics
from creators.models import Category, Classification, CreatorProfile, Subcategory
from creators.parser import parse_query


def search_creators(query_text=None, criteria=None, *, page=1, per_page=10, sort="relevance", filters=None, use_anthropic=True):
    parsed = None
    if criteria is None:
        parsed = parse_query(query_text or "", use_anthropic=use_anthropic)
        criteria = parsed["criteria"]
    filters = filters or {}
    merged = dict(criteria)
    for key in ("location", "niche", "subcategory"):
        if filters.get(key):
            merged[key] = filters[key]
    if filters.get("platform"):
        merged["platforms"] = [filters["platform"]]

    queryset = CreatorProfile.objects.filter(discoverable=True, share_metrics=True)
    if filters.get("name"):
        queryset = queryset.filter(display_name__icontains=filters["name"])
    queryset = queryset.select_related("user").prefetch_related(
        "niches",
        "user__social_accounts__snapshots__breakdowns",
        "user__social_accounts__contents",
        Prefetch(
            "classifications",
            queryset=Classification.objects.select_related("category", "subcategory").order_by("-created_at"),
        ),
    )
    rows = []
    for creator in queryset:
        metrics = creator_metrics(creator)
        match = score_creator(metrics, merged)
        if not match["eligible"]:
            continue
        if filters.get("min_followers") and (metrics["followers"]["value"] or 0) < int(filters["min_followers"]):
            continue
        rows.append({"creator": creator, "metrics": metrics, **match})

    rows.sort(key=lambda row: _sort_key(row, sort), reverse=sort != "name")
    if sort == "name":
        rows.sort(key=lambda row: row["creator"].display_name.lower())

    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(page)
    return {
        "parsed": parsed,
        "criteria": merged,
        "page": page_obj,
        "total": paginator.count,
        "sort": sort,
    }


def score_creator(metrics, criteria):
    reasons = []
    missing = list(metrics["missing"])
    score = 0
    eligible = True
    creator = metrics["creator"]

    location = (criteria.get("location") or "").strip()
    if location:
        actual = (metrics["location"]["value"] or "").lower()
        if actual and location.lower() in actual:
            score += 3
            reasons.append(f"Location {metrics['location']['value']} matches {location} (CreatorProfile).")
        elif not actual:
            reasons.append("Location is unavailable on the creator profile.")
        else:
            eligible = False
            reasons.append(f"Location {metrics['location']['value']} does not match {location}.")

    follower_min = criteria.get("follower_min")
    follower_max = criteria.get("follower_max")
    followers = metrics["followers"]["value"]
    if follower_min or follower_max:
        if followers is None:
            reasons.append("Follower count is unavailable; no MetricSnapshot.")
        else:
            low = follower_min or 0
            high = follower_max or followers
            if low <= followers <= high:
                score += 3
                reasons.append(
                    f"{followers:,} followers within {low:,}–{high:,} ({metrics['followers']['source']})."
                )
            else:
                eligible = False
                reasons.append(
                    f"{followers:,} followers outside {low:,}–{high:,} ({metrics['followers']['source']})."
                )

    platforms = criteria.get("platforms") or []
    if platforms:
        overlap = [item for item in platforms if item in metrics["platforms"]]
        if overlap:
            score += 2
            reasons.append(f"Connected platforms include {', '.join(overlap)}.")
        elif metrics["platforms"]:
            eligible = False
            reasons.append("No connected account on the requested platforms.")
        else:
            reasons.append("No connected account on the requested platforms.")

    niche = criteria.get("niche")
    subcategory = criteria.get("subcategory")
    classification = metrics["classification"]
    niche_ok = False
    if subcategory:
        slugs = {item.slug for item in creator.niches.all()}
        if subcategory in slugs:
            niche_ok = True
        elif classification and classification.subcategory and classification.subcategory.slug == subcategory:
            niche_ok = True
        if niche_ok:
            score += 3
            reasons.append(f"Subcategory {subcategory} matched ({metrics['niche']['source']}).")
        elif metrics["niche"]["value"]:
            eligible = False
            reasons.append(f"Subcategory {subcategory} was not matched.")
        else:
            reasons.append(f"Subcategory {subcategory} was not matched.")
    elif niche:
        slugs = {item.category.slug for item in creator.niches.select_related("category")}
        if niche in slugs:
            niche_ok = True
        elif classification and classification.category and classification.category.slug == niche:
            niche_ok = True
        if niche_ok:
            score += 2
            reasons.append(f"Category {niche} matched ({metrics['niche']['source']}).")
        elif metrics["niche"]["value"]:
            eligible = False
            reasons.append(f"Category {niche} was not matched.")
        else:
            reasons.append(f"Category {niche} was not matched.")

    min_engagement = criteria.get("min_engagement")
    engagement = metrics["engagement"]["value"]
    if min_engagement:
        threshold = Decimal(str(min_engagement))
        if engagement is None:
            reasons.append("Engagement is unavailable; no stored ContentItem views.")
        elif Decimal(str(engagement)) >= threshold:
            score += 2
            reasons.append(
                f"{engagement}% engagement is at least {threshold}% ({metrics['engagement']['source']})."
            )
        else:
            eligible = False
            reasons.append(
                f"{engagement}% engagement is below {threshold}% ({metrics['engagement']['source']})."
            )

    audience = (criteria.get("audience") or "").strip()
    if audience:
        blob = f"{creator.bio} {creator.audience_notes}".lower()
        tokens = [part for part in audience.lower().replace(",", " ").split() if len(part) > 3]
        hits = [token for token in tokens if token in blob]
        if hits:
            score += 1
            reasons.append(f"Audience notes mention {', '.join(hits[:6])} (CreatorProfile).")
        elif not blob.strip():
            reasons.append("Audience notes are unavailable.")
        else:
            reasons.append("Audience notes do not mention the requested audience terms.")

    return {"score": score, "reasons": reasons, "missing": missing, "eligible": eligible}


def niche_choices():
    return list(Category.objects.all())


def subcategory_choices():
    return list(Subcategory.objects.select_related("category"))


def _sort_key(row, sort):
    metrics = row["metrics"]
    if sort == "followers":
        return metrics["followers"]["value"] or -1
    if sort == "engagement":
        return metrics["engagement"]["value"] or -1
    if sort == "synced":
        stamp = metrics["last_synced_at"]
        return stamp.timestamp() if stamp else -1
    return (row["score"], metrics["engagement"]["value"] or 0, metrics["followers"]["value"] or 0)
