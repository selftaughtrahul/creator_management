import json
import re
from decimal import Decimal

from creators.anthropic import AnthropicError, anthropic_configured, complete_json
from creators.models import Category, Subcategory
from networks.models import SocialAccount

LOCATION_ALIASES = {
    "india": "India",
    "indian": "India",
    "usa": "United States",
    "us": "United States",
    "united states": "United States",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "uae": "United Arab Emirates",
    "canada": "Canada",
    "australia": "Australia",
    "germany": "Germany",
    "brazil": "Brazil",
    "france": "France",
    "indonesia": "Indonesia",
}

PLATFORM_ALIASES = {
    "instagram": "instagram",
    "insta": "instagram",
    "ig": "instagram",
    "youtube": "youtube",
    "yt": "youtube",
    "twitter": "twitter",
    "x": "twitter",
    "facebook": "facebook",
    "fb": "facebook",
}

RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*(?P<min_suffix>[kKmM])?\s*(?:[–\-]|to)\s*(?P<max>\d+(?:\.\d+)?)\s*(?P<max_suffix>[kKmM])?\s*(?:followers|subs|subscribers)?",
    re.I,
)
SINGLE_MIN_RE = re.compile(
    r"(?:over|at least|min(?:imum)?|around|about)?\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmM])?\s*(?:followers|subs|subscribers)",
    re.I,
)
SINGLE_MAX_RE = re.compile(
    r"(?:under|below|up to|less than)\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmM])?",
    re.I,
)
ENGAGEMENT_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*%\s*(?:engagement)?", re.I)


def parse_query(text, *, use_anthropic=True):
    rules = parse_with_rules(text)
    if use_anthropic and anthropic_configured():
        try:
            llm = parse_with_anthropic(text)
        except AnthropicError:
            llm = None
        if llm:
            return {
                "criteria": _merge(rules, llm),
                "parser": "anthropic",
                "raw_query": text,
            }
    return {"criteria": rules, "parser": "rules", "raw_query": text}


def parse_with_rules(text):
    lowered = (text or "").lower()
    location = None
    for alias, name in sorted(LOCATION_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            location = name
            break
    platforms = []
    for alias, key in PLATFORM_ALIASES.items():
        if alias == "x":
            if re.search(r"\bx\b", lowered) or "twitter" in lowered:
                platforms.append(key)
        elif re.search(rf"\b{re.escape(alias)}\b", lowered):
            platforms.append(key)
    platforms = list(dict.fromkeys(platforms))
    follower_min = follower_max = None
    match = RANGE_RE.search(text or "")
    if match:
        follower_min = _expand(match.group("min"), match.group("min_suffix"))
        follower_max = _expand(match.group("max"), match.group("max_suffix"))
    else:
        ceiling = SINGLE_MAX_RE.search(text or "")
        single = SINGLE_MIN_RE.search(text or "")
        if ceiling:
            follower_max = _expand(ceiling.group("n"), ceiling.group("suffix"))
        elif single:
            follower_min = _expand(single.group("n"), single.group("suffix"))
    min_engagement = None
    strong = bool(re.search(r"\b(strong|high|good)\s+engagement\b", lowered))
    eng = ENGAGEMENT_RE.search(text or "")
    if eng:
        min_engagement = Decimal(eng.group("n"))
    elif strong:
        min_engagement = Decimal("3.0")
    niche, subcategory = _match_niche(lowered)
    audience = None
    audience_match = re.search(r"audience relevant to ([^.]+)", lowered)
    if audience_match:
        audience = audience_match.group(1).strip()
    return {
        "location": location,
        "follower_min": follower_min,
        "follower_max": follower_max,
        "platforms": platforms,
        "niche": niche,
        "subcategory": subcategory,
        "min_engagement": str(min_engagement) if min_engagement is not None else None,
        "require_strong_engagement": strong,
        "audience": audience,
    }


def parse_with_anthropic(text):
    categories = list(Category.objects.values("slug", "name"))
    subcategories = list(Subcategory.objects.values("slug", "name", "category__slug"))
    platforms = [choice[0] for choice in SocialAccount.Platform.choices]
    prompt = (
        "Convert this creator search into JSON. Use only these keys: "
        "location, follower_min, follower_max, platforms, niche, subcategory, "
        "min_engagement, require_strong_engagement, audience. "
        "platforms must be a subset of "
        f"{platforms}. niche must be a category slug from {categories} or null. "
        f"subcategory must be a subcategory slug from {subcategories} or null. "
        "Do not invent creator statistics or names. Numbers refer only to search filters. "
        "Return JSON only.\n\nQuery:\n"
        f"{text}"
    )
    data = complete_json(prompt)
    return _validate_llm(data)


def _taxonomy_slug(text, model):
    text = (text or "").strip()
    if not text:
        return None
    row = model.objects.filter(slug=text).first() or model.objects.filter(name__iexact=text).first()
    return row.slug if row else None


def criteria_from_campaign(campaign):
    return {
        "location": campaign.location or None,
        "follower_min": campaign.follower_min,
        "follower_max": campaign.follower_max,
        "platforms": list(campaign.platforms or []),
        "niche": _taxonomy_slug(campaign.primary_niche, Category),
        "subcategory": _taxonomy_slug(campaign.secondary_niche, Subcategory),
        "min_engagement": str(campaign.min_engagement) if campaign.min_engagement is not None else None,
        "require_strong_engagement": False,
        "audience": campaign.audience_notes or None,
    }


def _match_niche(lowered):
    for sub in Subcategory.objects.select_related("category"):
        names = [sub.name.lower(), sub.slug.replace("-", " ")] + sub.keyword_list()
        if any(name and name in lowered for name in names):
            return sub.category.slug, sub.slug
    for category in Category.objects.all():
        names = [category.name.lower(), category.slug.replace("-", " ")] + category.keyword_list()
        if any(name and name in lowered for name in names):
            return category.slug, None
    return None, None


def _expand(number, suffix):
    value = float(number)
    if suffix:
        letter = suffix.lower()
        if letter == "k":
            value *= 1000
        elif letter == "m":
            value *= 1_000_000
    return int(value)


def _validate_llm(data):
    if not isinstance(data, dict):
        return None
    platforms = []
    valid = {choice[0] for choice in SocialAccount.Platform.choices}
    for item in data.get("platforms") or []:
        key = PLATFORM_ALIASES.get(str(item).lower())
        if key in valid and key not in platforms:
            platforms.append(key)
    niche = data.get("niche")
    if niche and not Category.objects.filter(slug=niche).exists():
        category = Category.objects.filter(name__iexact=str(niche)).first()
        niche = category.slug if category else None
    subcategory = data.get("subcategory")
    if subcategory and not Subcategory.objects.filter(slug=subcategory).exists():
        sub = Subcategory.objects.filter(name__iexact=str(subcategory)).first()
        subcategory = sub.slug if sub else None
    location = data.get("location")
    if location:
        location = LOCATION_ALIASES.get(str(location).lower(), str(location))
    follower_min = _as_int(data.get("follower_min"))
    follower_max = _as_int(data.get("follower_max"))
    min_engagement = data.get("min_engagement")
    if min_engagement is not None:
        try:
            min_engagement = str(Decimal(str(min_engagement)))
        except Exception:
            min_engagement = None
    return {
        "location": location or None,
        "follower_min": follower_min,
        "follower_max": follower_max,
        "platforms": platforms,
        "niche": niche,
        "subcategory": subcategory,
        "min_engagement": min_engagement,
        "require_strong_engagement": bool(data.get("require_strong_engagement")),
        "audience": data.get("audience") or None,
    }


def _merge(rules, llm):
    merged = dict(rules)
    for key, value in llm.items():
        if value in (None, "", [], False) and merged.get(key) not in (None, "", [], False):
            continue
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _as_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def empty_criteria():
    return {
        "location": None,
        "follower_min": None,
        "follower_max": None,
        "platforms": [],
        "niche": None,
        "subcategory": None,
        "min_engagement": None,
        "require_strong_engagement": False,
        "audience": None,
    }


def criteria_active(criteria):
    if not criteria:
        return False
    for key, value in criteria.items():
        if key == "require_strong_engagement":
            continue
        if value not in (None, "", [], False):
            return True
    return False


def chat_turn(messages, criteria, message, *, use_anthropic=True):
    """Update search filters from the next chat message and reply in prose."""
    criteria = {**empty_criteria(), **(criteria or {})}
    text = (message or "").strip()
    if not text:
        return {"criteria": criteria, "reply": _rules_reply(criteria), "parser": "rules"}
    if re.search(r"\b(start over|reset|clear (the )?search)\b", text.lower()):
        return {
            "criteria": empty_criteria(),
            "reply": "Cleared the search. Tell me the niche, location, platform, or follower range you want.",
            "parser": "rules",
        }
    if use_anthropic and anthropic_configured():
        try:
            data = complete_json(
                "",
                system=_chat_system(),
                messages=_chat_messages(messages, criteria, text),
            )
        except AnthropicError:
            data = None
        validated = _validate_llm((data or {}).get("criteria")) if isinstance(data, dict) else None
        if validated:
            reply = str((data or {}).get("reply") or "").strip() or _rules_reply(validated)
            return {"criteria": validated, "reply": reply[:600], "parser": "anthropic"}
    updated = _apply_rules_turn(criteria, text)
    return {"criteria": updated, "reply": _rules_reply(updated), "parser": "rules"}


def _chat_system():
    categories = list(Category.objects.values("slug", "name"))
    subcategories = list(Subcategory.objects.values("slug", "name", "category__slug"))
    platforms = [choice[0] for choice in SocialAccount.Platform.choices]
    return (
        "You are a creator-search assistant. The user is refining one search across turns. "
        "Keep earlier filters unless the new message changes or clears them. "
        "Never invent creator names, follower counts, or profiles. "
        "Do not say how many creators match. "
        "Return one JSON object with keys reply and criteria. "
        "reply is one or two conversational sentences about the filters you are using. "
        "criteria uses only these keys: location, follower_min, follower_max, platforms, "
        "niche, subcategory, min_engagement, require_strong_engagement, audience. "
        "Use null or [] for a filter the user dropped. "
        f"platforms must be a subset of {platforms}. "
        f"niche must be a category slug from {categories} or null. "
        f"subcategory must be a subcategory slug from {subcategories} or null."
    )


def _chat_messages(history, criteria, message):
    turns = []
    for item in history[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        text = (item.get("text") or "").strip()
        if text:
            turns.append({"role": role, "content": text})
    if turns and turns[0]["role"] != "user":
        turns = turns[1:]
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]
    turns.append(
        {
            "role": "user",
            "content": (
                "Current filters:\n"
                f"{json.dumps(criteria, default=str)}\n\n"
                "Latest message:\n"
                f"{message}\n\n"
                "Return JSON only."
            ),
        }
    )
    return turns


def _apply_rules_turn(criteria, message):
    lowered = message.lower()
    parsed = parse_with_rules(message)
    updated = dict(criteria)
    if re.search(r"\b(anywhere|any location|no location|drop location)\b", lowered):
        updated["location"] = None
        parsed["location"] = None
    if re.search(r"\b(any niche|any category|drop niche)\b", lowered):
        updated["niche"] = None
        updated["subcategory"] = None
        parsed["niche"] = None
        parsed["subcategory"] = None
    if re.search(r"\b(any platform|all platforms|drop platforms?)\b", lowered):
        updated["platforms"] = []
        parsed["platforms"] = []
    if re.search(r"\b(any followers|any size|drop followers|no follower)\b", lowered):
        updated["follower_min"] = None
        updated["follower_max"] = None
        parsed["follower_min"] = None
        parsed["follower_max"] = None
    parsed_platforms = parsed.get("platforms") or []
    if parsed_platforms and re.search(r"\b(also|too|as well)\b", lowered):
        updated["platforms"] = list(dict.fromkeys((updated.get("platforms") or []) + parsed_platforms))
        parsed["platforms"] = []
    for key, value in parsed.items():
        if value in (None, "", [], False):
            continue
        updated[key] = value
    return updated


def _rules_reply(criteria):
    if not criteria_active(criteria):
        return (
            "Tell me who to find, for example gaming creators in India on YouTube. "
            "I only filter profiles already stored here, and I will keep the filters as we talk."
        )
    sentence = "I'll keep filtering"
    if criteria.get("niche"):
        sentence += f" for {str(criteria['niche']).replace('-', ' ')} creators"
    else:
        sentence += " for creators"
    if criteria.get("subcategory"):
        sentence += f" ({str(criteria['subcategory']).replace('-', ' ')})"
    if criteria.get("location"):
        sentence += f" in {criteria['location']}"
    if criteria.get("platforms"):
        sentence += f" on {', '.join(criteria['platforms'])}"
    if criteria.get("follower_min") or criteria.get("follower_max"):
        low = criteria.get("follower_min") or "any"
        high = criteria.get("follower_max") or "any"
        sentence += f", {low}–{high} followers"
    if criteria.get("min_engagement"):
        sentence += f", at least {criteria['min_engagement']}% engagement"
    sentence += ". Tell me what to change, or say start over."
    return sentence
