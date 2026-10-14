import logging
from decimal import Decimal

from creators.anthropic import AnthropicError, anthropic_configured, complete_json
from creators.models import Category, Classification, Subcategory

logger = logging.getLogger("creators.classify")


def profile_text(creator, caption_limit=8):
    parts = [creator.display_name or "", creator.bio or "", creator.audience_notes or ""]
    accounts = creator.user.social_accounts.all()
    for account in accounts:
        parts.append(account.display_name or "")
        parts.append(account.username or "")
        for item in account.contents.all()[:caption_limit]:
            parts.append(item.title or "")
            parts.append(item.caption or "")
    return "\n".join(part for part in parts if part).strip()


def classify_creator(creator, *, method="rules", user=None):
    if method == "anthropic":
        return classify_with_anthropic(creator, user=user)
    return classify_with_rules(creator, user=user)


def classify_with_rules(creator, user=None):
    text = profile_text(creator).lower()
    if not text:
        return _save(
            creator,
            category=None,
            subcategory=None,
            method=Classification.Method.RULES,
            confidence=None,
            evidence=[],
            provenance=Classification.Provenance.UNAVAILABLE,
            needs_review=True,
            explanation="No bio or captions were available to classify.",
            user=user,
        )
    best = None
    best_hits = []
    best_score = 0
    for subcategory in Subcategory.objects.select_related("category"):
        hits = [kw for kw in subcategory.keyword_list() if kw and kw in text]
        if len(hits) > best_score:
            best = subcategory
            best_hits = hits
            best_score = len(hits)
    if best_score == 0:
        for category in Category.objects.all():
            hits = [kw for kw in category.keyword_list() if kw and kw in text]
            if len(hits) > best_score:
                best = category
                best_hits = hits
                best_score = len(hits)
        category = best if isinstance(best, Category) else None
        subcategory = None
    else:
        category = best.category
        subcategory = best
    if best_score == 0:
        return _save(
            creator,
            category=None,
            subcategory=None,
            method=Classification.Method.RULES,
            confidence=None,
            evidence=[],
            provenance=Classification.Provenance.UNAVAILABLE,
            needs_review=True,
            explanation="Keyword rules found no matching category.",
            user=user,
        )
    confidence = min(Decimal("1.000"), Decimal(best_score) / Decimal("3"))
    return _save(
        creator,
        category=category,
        subcategory=subcategory,
        method=Classification.Method.RULES,
        confidence=confidence,
        evidence=[{"type": "keyword", "value": hit} for hit in best_hits[:8]],
        provenance=Classification.Provenance.ESTIMATE,
        needs_review=confidence < Decimal("0.670"),
        explanation=f"Matched keywords: {', '.join(best_hits[:8])}.",
        user=user,
    )


def classify_with_anthropic(creator, user=None):
    if not anthropic_configured():
        logger.info("anthropic_skipped reason=missing_key creator_id=%s", creator.pk)
        result = classify_with_rules(creator, user=user)
        result.needs_review = True
        result.explanation = (result.explanation + " Claude was not called because ANTHROPIC_API_KEY is unset.").strip()
        result.save(update_fields=["needs_review", "explanation"])
        return result
    categories = list(Category.objects.prefetch_related("subcategories"))
    catalog = []
    slugs = set()
    sub_slugs = {}
    for category in categories:
        slugs.add(category.slug)
        children = []
        for sub in category.subcategories.all():
            children.append({"slug": sub.slug, "name": sub.name})
            sub_slugs[sub.slug] = sub
        catalog.append({"slug": category.slug, "name": category.name, "subcategories": children})
    text = profile_text(creator)
    prompt = (
        "Classify this creator into one category slug from the catalog. "
        "Use only the profile text. Do not invent follower counts, views, engagement, "
        "or any numeric statistics. Return JSON only with keys: "
        "category_slug, subcategory_slug, confidence (0-1), evidence (list of short quotes), explanation.\n\n"
        f"Catalog: {catalog}\n\nProfile text:\n{text[:6000]}"
    )
    try:
        data = complete_json(prompt)
    except AnthropicError as exc:
        logger.warning("anthropic_classify_failed creator_id=%s", creator.pk)
        result = classify_with_rules(creator, user=user)
        result.needs_review = True
        result.explanation = f"{result.explanation} Claude call failed: {exc}"
        result.save(update_fields=["needs_review", "explanation"])
        return result
    category = Category.objects.filter(slug=data.get("category_slug")).first()
    subcategory = None
    sub_slug = data.get("subcategory_slug")
    if sub_slug and sub_slug in sub_slugs:
        candidate = sub_slugs[sub_slug]
        if category is None or candidate.category_id == category.id:
            subcategory = candidate
            category = candidate.category
    evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    quotes = []
    lowered = text.lower()
    for item in evidence[:8]:
        quote = str(item)[:180]
        if quote.lower() in lowered or quote.lower() in [part.lower() for part in text.split("\n")]:
            quotes.append({"type": "quote", "value": quote})
        else:
            quotes.append({"type": "model_claim", "value": quote})
    confidence = _confidence(data.get("confidence"))
    needs_review = category is None or (confidence is not None and confidence < Decimal("0.600"))
    if category is None:
        provenance = Classification.Provenance.UNAVAILABLE
        explanation = "Claude returned a category that is not in the catalog."
        needs_review = True
    else:
        provenance = Classification.Provenance.AI_ASSESSMENT
        explanation = str(data.get("explanation") or "Claude suggested this category from profile text.")
    return _save(
        creator,
        category=category,
        subcategory=subcategory,
        method=Classification.Method.ANTHROPIC,
        confidence=confidence,
        evidence=quotes,
        provenance=provenance,
        needs_review=needs_review,
        explanation=explanation[:1000],
        user=user,
    )


def _confidence(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if number < 0 or number > 1:
        return None
    return number.quantize(Decimal("0.001"))


def _save(creator, **fields):
    user = fields.pop("user", None)
    return Classification.objects.create(creator=creator, created_by=user, **fields)
