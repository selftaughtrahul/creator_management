from django.db import transaction

from accounts.csvutil import SpreadsheetError, csv_response, parse_decimal, parse_int, read_table
from campaigns.models import Campaign
from networks.models import SocialAccount

CAMPAIGN_COLUMNS = [
    "id",
    "name",
    "objective",
    "budget",
    "niche",
    "subcategory",
    "location",
    "platforms",
    "follower_min",
    "follower_max",
    "min_engagement",
    "audience_notes",
    "status",
]

COLUMN_HELP = (
    "The template includes one example row. Delete that row before you import your own campaigns. "
    "id blank creates a campaign; an existing id updates that campaign. "
    "niche and subcategory are free text for the primary and secondary niche. platforms is a comma-separated list of instagram, facebook, youtube, twitter. "
    "status is draft, active, paused, or completed. Leave budget, follower limits, and min_engagement blank when they are not set."
)

SAMPLE_CAMPAIGN = {
    "id": "",
    "name": "Example campaign — delete this row",
    "objective": "Describe what this campaign should achieve",
    "budget": "10000.00",
    "niche": "gaming",
    "subcategory": "mobile-gaming",
    "location": "India",
    "platforms": "youtube,instagram",
    "follower_min": "10000",
    "follower_max": "100000",
    "min_engagement": "2.50",
    "audience_notes": "Who the audience is",
    "status": "draft",
}


def export_campaigns(campaigns, *, filename="campaigns.csv", sample=False):
    rows = []
    if sample:
        rows.append([SAMPLE_CAMPAIGN[column] for column in CAMPAIGN_COLUMNS])
    for campaign in campaigns:
        rows.append(
            [
                campaign.pk,
                campaign.name,
                campaign.objective,
                "" if campaign.budget is None else campaign.budget,
                campaign.primary_niche,
                campaign.secondary_niche,
                campaign.location,
                ",".join(campaign.platforms or []),
                "" if campaign.follower_min is None else campaign.follower_min,
                "" if campaign.follower_max is None else campaign.follower_max,
                "" if campaign.min_engagement is None else campaign.min_engagement,
                campaign.audience_notes,
                campaign.status,
            ]
        )
    return csv_response(filename, CAMPAIGN_COLUMNS, rows)


def import_campaigns(uploaded, owner):
    table = [row for row in read_table(uploaded, CAMPAIGN_COLUMNS) if not _is_sample_campaign(row[1])]
    if not table:
        raise SpreadsheetError(["Delete the example row and add your own campaigns before importing."])
    errors = []
    parsed = []
    seen_ids = set()
    for row_number, row in table:
        try:
            item = _parse_campaign_row(row_number, row)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if item["id"] is not None:
            if item["id"] in seen_ids:
                errors.append(f"Row {row_number}: id {item['id']} is repeated in this file.")
            seen_ids.add(item["id"])
        parsed.append(item)
    if errors:
        raise SpreadsheetError(errors[:20] + ([f"{len(errors) - 20} more row errors."] if len(errors) > 20 else []))

    existing = {campaign.pk: campaign for campaign in Campaign.objects.filter(pk__in=seen_ids)}
    missing = sorted(seen_ids - set(existing))
    if missing:
        listed = ", ".join(str(pk) for pk in missing[:10])
        raise SpreadsheetError([f"These campaign ids do not exist: {listed}."])

    created = updated = 0
    with transaction.atomic():
        for item in parsed:
            campaign = existing.get(item["id"]) if item["id"] is not None else Campaign(owner=owner)
            if campaign is None:
                raise SpreadsheetError([f"Campaign {item['id']} does not exist."])
            for field in (
                "name",
                "objective",
                "budget",
                "primary_niche",
                "secondary_niche",
                "location",
                "platforms",
                "follower_min",
                "follower_max",
                "min_engagement",
                "audience_notes",
                "status",
            ):
                setattr(campaign, field, item[field])
            campaign.save()
            if item["id"] is None:
                created += 1
            else:
                updated += 1
    return {"created": created, "updated": updated}


def _is_sample_campaign(row):
    return all(row.get(column, "") == value for column, value in SAMPLE_CAMPAIGN.items())


def _parse_campaign_row(row_number, row):
    name = row["name"]
    if not name:
        raise ValueError(f"Row {row_number}: name is required.")
    if len(name) > 160:
        raise ValueError(f"Row {row_number}: name must be 160 characters or fewer.")
    location = row["location"]
    if len(location) > 120:
        raise ValueError(f"Row {row_number}: location must be 120 characters or fewer.")
    status = row["status"] or Campaign.Status.DRAFT
    if status not in Campaign.Status.values:
        raise ValueError(
            f"Row {row_number}: status must be one of {', '.join(Campaign.Status.values)}."
        )
    platforms = []
    if row["platforms"]:
        allowed = set(SocialAccount.Platform.values)
        for token in row["platforms"].split(","):
            key = token.strip().lower()
            if not key:
                continue
            if key not in allowed:
                raise ValueError(
                    f"Row {row_number}: platform '{key}' is not one of {', '.join(sorted(allowed))}."
                )
            if key not in platforms:
                platforms.append(key)
    follower_min = parse_int(row["follower_min"], row_number=row_number, column="follower_min")
    follower_max = parse_int(row["follower_max"], row_number=row_number, column="follower_max")
    if follower_min is not None and follower_max is not None and follower_min > follower_max:
        raise ValueError(f"Row {row_number}: follower_max must be at least follower_min.")
    campaign_id = parse_int(row["id"], row_number=row_number, column="id")
    return {
        "id": campaign_id,
        "name": name,
        "objective": row["objective"],
        "budget": parse_decimal(row["budget"], row_number=row_number, column="budget", max_digits=12, decimal_places=2),
        "primary_niche": row["niche"],
        "secondary_niche": row["subcategory"],
        "location": location,
        "platforms": platforms,
        "follower_min": follower_min,
        "follower_max": follower_max,
        "min_engagement": parse_decimal(
            row["min_engagement"],
            row_number=row_number,
            column="min_engagement",
            max_digits=5,
            decimal_places=2,
            maximum=100,
        ),
        "audience_notes": row["audience_notes"],
        "status": status,
    }
