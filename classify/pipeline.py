import logging
import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from creators.anthropic import AnthropicError, anthropic_configured, complete_json
from classify.retrieve import retrieve_chunks
from creators.models import IABCategory, IABClassification, IABMapping, Subcategory

logger = logging.getLogger("classify")

MODES = {choice.value for choice in IABClassification.Mode}


def classify(creator, *, mode="retrieved", method="rules", user=None, complete=None):
    """Classify one creator onto imported IAB ids, then attach custom niches from the mapping table."""
    if mode not in MODES:
        mode = IABClassification.Mode.RETRIEVED
    version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
    if not IABCategory.objects.filter(taxonomy_version=version).exists():
        logger.info("iab_classify_skipped reason=not_imported creator_id=%s", creator.pk)
        return []
    chunks = collect_chunks(creator, mode=mode)
    text = "\n".join(chunk["text"] for chunk in chunks if chunk.get("text"))
    if not text.strip():
        return []
    use_llm = method == "anthropic" and (complete is not None or anthropic_configured())
    if use_llm:
        labels = _labels_from_llm(creator, chunks, text, complete=complete or complete_json)
        chosen_method = IABClassification.Method.ANTHROPIC
        if labels is None:
            labels = _labels_from_rules(text)
            chosen_method = IABClassification.Method.RULES
    else:
        labels = _labels_from_rules(text)
        chosen_method = IABClassification.Method.RULES
    return _save_labels(creator, labels, text=text, mode=mode, method=chosen_method, user=user)


def collect_chunks(creator, *, mode="retrieved", video_limit=12, media_limit=12):
    profile = _profile_chunk(creator)
    if mode == IABClassification.Mode.PROFILE:
        return [profile] if profile["text"] else []
    chunks = [profile] if profile["text"] else []
    chunks.extend(_platform_chunks(creator, video_limit=video_limit, media_limit=media_limit))
    if mode == IABClassification.Mode.RETRIEVED:
        _score_chunks(chunks)
        return retrieve_chunks(creator, chunks)
    return chunks


def _profile_chunk(creator):
    parts = [creator.display_name or "", creator.bio or "", creator.location or "", creator.audience_notes or ""]
    accounts = creator.user.social_accounts.all()
    for account in accounts:
        parts.append(account.display_name or "")
        parts.append(account.username or "")
    return {"source": "profile", "ref": f"profile:{creator.pk}", "text": "\n".join(part for part in parts if part).strip(), "score": 0}


def _platform_chunks(creator, *, video_limit, media_limit):
    chunks = []
    accounts = creator.user.social_accounts.all()
    for account in accounts:
        for item in account.contents.all()[:media_limit]:
            text = "\n".join(part for part in (item.title, item.caption) if part).strip()
            if text:
                chunks.append({"source": "content", "ref": f"content:{item.pk}", "text": text[:2000], "score": 0})
        for channel in account.youtube_channels.all():
            if channel.description:
                chunks.append(
                    {
                        "source": "youtube_channel",
                        "ref": f"channel:{channel.pk}",
                        "text": "\n".join(part for part in (channel.title, channel.description) if part)[:2000],
                        "score": 0,
                    }
                )
            videos = channel.videos.all()[:video_limit]
            for video in videos:
                text = "\n".join(part for part in (video.title, video.description) if part).strip()
                if text:
                    chunks.append({"source": "youtube_video", "ref": f"video:{video.pk}", "text": text[:2000], "score": 0})
                transcript = video.transcripts.filter(status="available").exclude(text="").first()
                if transcript is not None:
                    chunks.append(
                        {
                            "source": "youtube_transcript",
                            "ref": f"transcript:{transcript.pk}",
                            "text": transcript.text[:4000],
                            "score": 0,
                        }
                    )
        try:
            profile = account.instagram_profile
        except ObjectDoesNotExist:
            profile = None
        if profile is not None:
            if profile.biography:
                chunks.append(
                    {
                        "source": "instagram_profile",
                        "ref": f"ig-profile:{profile.pk}",
                        "text": "\n".join(part for part in (profile.name, profile.biography) if part)[:2000],
                        "score": 0,
                    }
                )
            for media in profile.media.all()[:media_limit]:
                if media.caption:
                    chunks.append(
                        {
                            "source": "instagram_media",
                            "ref": f"ig-media:{media.pk}",
                            "text": media.caption[:2000],
                            "score": 0,
                        }
                    )
    return chunks


def _score_chunks(chunks):
    version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
    phrases = []
    for mapping in IABMapping.objects.filter(iab_category__taxonomy_version=version):
        phrases.extend(keyword for keyword in mapping.keyword_list() if len(keyword) >= 3)
    for name in IABCategory.objects.filter(taxonomy_version=version).values_list("name", flat=True):
        if len(name) >= 4:
            phrases.append(name.lower())
    for chunk in chunks:
        lowered = chunk["text"].lower()
        chunk["score"] = sum(1 for phrase in phrases if _contains(lowered, phrase))


def _labels_from_rules(text):
    version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
    lowered = text.lower()
    scored = []
    categories = IABCategory.objects.filter(taxonomy_version=version).prefetch_related("mappings")
    for category in categories:
        hits = []
        if len(category.name) >= 4 and _contains(lowered, category.name.lower()):
            hits.append(category.name.lower())
        for mapping in category.mappings.all():
            for keyword in mapping.keyword_list():
                if len(keyword) >= 3 and _contains(lowered, keyword):
                    hits.append(keyword)
        if not hits:
            continue
        score = sum(max(len(hit.split()), 1) for hit in hits) * 10 + category.depth
        scored.append((score, category, hits))
    scored.sort(key=lambda item: (-item[0], -item[1].depth, item[1].unique_id))
    if not scored:
        return []
    best = scored[0][0]
    labels = []
    for score, category, hits in scored[:3]:
        if score < best / 2:
            break
        labels.append(
            {
                "category": category,
                "confidence": min(Decimal("1.000"), (Decimal(score) / Decimal(best)).quantize(Decimal("0.001"))),
                "hits": hits[:8],
                "explanation": f"Matched: {', '.join(hits[:8])}.",
            }
        )
    return labels


def _labels_from_llm(creator, chunks, text, *, complete):
    candidates = _labels_from_rules(text)
    catalog = []
    seen = set()
    for label in candidates:
        category = label["category"]
        seen.add(category.unique_id)
        catalog.append({"id": category.unique_id, "name": category.name, "parent_id": category.parent_id})
    if len(catalog) < 12:
        version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
        extras = IABCategory.objects.filter(taxonomy_version=version, parent_id="").exclude(unique_id__in=seen)[:40]
        for category in extras:
            catalog.append({"id": category.unique_id, "name": category.name, "parent_id": category.parent_id})
    excerpt = text[:6000]
    prompt = (
        "Choose the closest IAB Content Taxonomy 3.1 categories for this creator. "
        "Use only ids from the catalog. Do not invent ids, names, follower counts, or statistics. "
        "Return JSON with key labels, a list of objects with keys "
        "iab_category_id, confidence (0-1), evidence (short quotes from the text), explanation. "
        "Return up to 3 labels when the creator covers more than one subject.\n\n"
        f"Catalog: {catalog}\n\nContent:\n{excerpt}"
    )
    try:
        data = complete(prompt)
    except AnthropicError:
        logger.warning("iab_llm_failed creator_id=%s", creator.pk)
        return None
    raw_labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(raw_labels, list):
        return None
    version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
    allowed = {
        row.unique_id: row
        for row in IABCategory.objects.filter(taxonomy_version=version, unique_id__in=[str(item.get("iab_category_id")) for item in raw_labels if isinstance(item, dict)])
    }
    labels = []
    for item in raw_labels[:3]:
        if not isinstance(item, dict):
            continue
        category = allowed.get(str(item.get("iab_category_id") or "").strip())
        if category is None:
            logger.info("iab_id_rejected creator_id=%s iab_category_id=%s", creator.pk, item.get("iab_category_id"))
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        labels.append(
            {
                "category": category,
                "confidence": _confidence(item.get("confidence")),
                "hits": [str(quote)[:180] for quote in evidence[:8]],
                "explanation": str(item.get("explanation") or "Claude chose this imported IAB category.")[:1000],
            }
        )
    return labels


def _save_labels(creator, labels, *, text, mode, method, user):
    version = getattr(settings, "IAB_TAXONOMY_VERSION", "3.1")
    rows = []
    with transaction.atomic():
        IABClassification.objects.filter(creator=creator, mode=mode).delete()
        for label in labels:
            category = label["category"]
            if category.taxonomy_version != version:
                continue
            mappings = list(category.mappings.all())
            targets = mappings or [None]
            for mapping in targets:
                rows.append(
                    IABClassification.objects.create(
                        creator=creator,
                        iab_category=category,
                        taxonomy_version=category.taxonomy_version,
                        custom_niche=mapping.custom_niche if mapping else "",
                        custom_sub_niche=mapping.custom_sub_niche if mapping else "",
                        custom_micro_niche=mapping.custom_micro_niche if mapping else "",
                        mode=mode,
                        method=method,
                        confidence=label["confidence"],
                        evidence=_evidence(text, label["hits"]),
                        needs_review=mapping is None or (label["confidence"] is not None and label["confidence"] < Decimal("0.600")),
                        explanation=label["explanation"],
                        created_by=user,
                    )
                )
        _apply_discovery_niches(creator, rows)
    logger.info("iab_classified creator_id=%s mode=%s labels=%s", creator.pk, mode, len(rows))
    return rows


def _apply_discovery_niches(creator, rows):
    names = {row.custom_sub_niche.strip().lower() for row in rows if row.custom_sub_niche.strip()}
    if not names:
        return
    matches = [sub for sub in Subcategory.objects.all() if sub.name.lower() in names]
    if matches:
        creator.niches.add(*matches)


def _evidence(text, hits):
    lowered = text.lower()
    evidence = []
    for hit in hits:
        quote = str(hit)[:180]
        kind = "quote" if quote.lower() in lowered else "keyword"
        evidence.append({"type": kind, "value": quote})
    return evidence


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


def _contains(text, phrase):
    if not phrase:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None
