import re
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso8601_duration(value):
    if not value or not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value.strip())
    if not match:
        return None
    if not any(match.groupdict().values()):
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_dt(value):
    if not value:
        return None
    parsed = parse_datetime(str(value).replace("Z", "+00:00"))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def optional_int(mapping, key):
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    value = mapping.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def thumbnail_map(thumbnails):
    if not isinstance(thumbnails, dict):
        return {}
    cleaned = {}
    for name, item in thumbnails.items():
        if isinstance(item, dict) and item.get("url"):
            cleaned[str(name)] = {
                "url": item.get("url"),
                "width": item.get("width"),
                "height": item.get("height"),
            }
    return cleaned


def preferred_thumbnail(thumbnails):
    mapping = thumbnail_map(thumbnails)
    for key in ("medium", "high", "default", "standard", "maxres"):
        if mapping.get(key, {}).get("url"):
            return mapping[key]["url"]
    return ""


def clip(value, limit):
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def aware_utc(moment):
    if timezone.is_naive(moment):
        return timezone.make_aware(moment, dt_timezone.utc)
    return moment


def date_from_string(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
