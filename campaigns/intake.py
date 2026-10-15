import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from campaigns.models import Campaign

SHEET_COLUMNS = (
    "Client",
    "Deadline to scout the profiles",
    "Primary Market",
    "Additional Markets",
    "# Creators Needed",
    "Platform",
    "Primary Niche / Vertical",
    "Secondary Niche / Sub Niche",
    "Content Format",
    "Gender",
    "Audience Location",
    "Audience Language",
    "Min Followers",
    "Max Followers/Tiers",
    "Min Avg Views",
    "Min Engagement Rate (%)",
    "Min View Consistency",
    "Max Rate per Deliverable (USD)",
    "Delivery Deadline",
    "Notes",
    "Brief",
    "COMMENTS",
)


def parse_count(value):
    text = (value or "").strip().replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+", text):
        return None
    number = int(text)
    if number > 2_147_483_647:
        return None
    return number


def parse_money(value):
    text = (value or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_engagement(value):
    text = (value or "").strip().replace("%", "")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    number = Decimal(text)
    if number >= 1000:
        return None
    return number.quantize(Decimal("0.01"))


def parse_date(value):
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def known_platforms(text):
    lowered = (text or "").lower()
    found = []
    if re.search(r"instagram|\big\b", lowered):
        found.append("instagram")
    if re.search(r"youtube|\byt\b", lowered):
        found.append("youtube")
    if "facebook" in lowered:
        found.append("facebook")
    if re.search(r"twitter|(?<![a-z])x(?![a-z])", lowered):
        found.append("twitter")
    return found


def iter_sheet_rows(text):
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    rows = list(reader)
    header_index = None
    for index, row in enumerate(rows):
        if row and row[0].strip() == "Client":
            header_index = index
            break
    if header_index is None:
        raise ValueError("Campaign requirements sheet has no Client header.")
    headers = [cell.strip() for cell in rows[header_index]]
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        data = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            data[header] = row[index].strip() if index < len(row) else ""
        client = data.get("Client", "")
        if not client or "just an example" in client.lower():
            continue
        yield data


def apply_row(campaign, data):
    client = data.get("Client", "").strip()
    campaign.name = client[:160]
    campaign.client = client[:160]
    campaign.scout_deadline = parse_date(data.get("Deadline to scout the profiles"))
    market = data.get("Primary Market", "")
    campaign.primary_market = market[:255]
    campaign.location = market[:120]
    campaign.additional_markets = data.get("Additional Markets", "")[:255]
    campaign.creators_needed = data.get("# Creators Needed", "")[:160]
    campaign.platforms = known_platforms(data.get("Platform", ""))
    campaign.primary_niche = data.get("Primary Niche / Vertical", "")
    campaign.secondary_niche = data.get("Secondary Niche / Sub Niche", "")
    campaign.content_format = data.get("Content Format", "")
    campaign.gender = data.get("Gender", "")[:80]
    campaign.audience_location = data.get("Audience Location", "")[:255]
    campaign.audience_language = data.get("Audience Language", "")[:160]
    min_followers = data.get("Min Followers", "")
    max_followers = data.get("Max Followers/Tiers", "")
    campaign.follower_min = parse_count(min_followers)
    campaign.follower_max = parse_count(max_followers)
    campaign.min_avg_views = data.get("Min Avg Views", "")[:80]
    campaign.min_engagement = parse_engagement(data.get("Min Engagement Rate (%)"))
    campaign.view_consistency = data.get("Min View Consistency", "")[:80]
    campaign.max_rate_usd = parse_money(data.get("Max Rate per Deliverable (USD)"))
    campaign.delivery_deadline = parse_date(data.get("Delivery Deadline"))
    campaign.audience_notes = data.get("Notes", "")
    campaign.objective = data.get("Brief", "")
    campaign.comments = data.get("COMMENTS", "")


def import_requirement_records(records, owner):
    created = updated = 0
    with transaction.atomic():
        for data in records:
            client = (data.get("Client") or "").strip()
            if not client or "just an example" in client.lower():
                continue
            name = client[:160]
            campaign = Campaign.objects.filter(name=name).first()
            if campaign is None:
                campaign = Campaign(name=name, owner=owner)
                created += 1
            else:
                updated += 1
            apply_row(campaign, data)
            if not campaign.owner_id:
                campaign.owner = owner
            campaign.save()
    return created, updated


def import_requirement_text(text, owner):
    return import_requirement_records(iter_sheet_rows(text), owner)
