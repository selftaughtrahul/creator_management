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
    r"(?:over|at least|min(?:imum)?)\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmM])?\s*(?:followers|subs)?",
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
        single = SINGLE_MIN_RE.search(text or "")
        if single:
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


def criteria_from_campaign(campaign):
    return {
        "location": campaign.location or None,
        "follower_min": campaign.follower_min,
        "follower_max": campaign.follower_max,
        "platforms": list(campaign.platforms or []),
        "niche": campaign.niche.slug if campaign.niche else None,
        "subcategory": campaign.subcategory.slug if campaign.subcategory else None,
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
