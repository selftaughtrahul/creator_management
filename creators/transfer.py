from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from accounts.csvutil import SpreadsheetError, csv_response, parse_bool, parse_decimal, read_table
from accounts.models import StudioProfile
from accounts.utils import unique_username
from allauth.account.models import EmailAddress
from creators.models import CreatorProfile, Subcategory

CREATOR_COLUMNS = [
    "email",
    "display_name",
    "bio",
    "location",
    "audience_notes",
    "niches",
    "rate_amount",
    "rate_currency",
    "share_metrics",
    "discoverable",
]

COLUMN_HELP = (
    "The template includes one example row. Delete that row before you import your own creators. "
    "email identifies the creator. display_name is required. "
    "niches is a comma-separated list of subcategory slugs, such as mobile-gaming. "
    "share_metrics and discoverable are yes or no. discoverable requires share_metrics. "
    "password is optional and is never included in an export. A new row without a password cannot log in until a password is set."
)

SAMPLE_CREATOR = {
    "email": "example-delete-this-row@example.com",
    "display_name": "Example Creator — delete this row",
    "bio": "Short description of the creator",
    "location": "India",
    "audience_notes": "Who their audience is",
    "niches": "mobile-gaming",
    "rate_amount": "15000.00",
    "rate_currency": "INR",
    "share_metrics": "yes",
    "discoverable": "yes",
}


def export_creators(creators, *, filename="creators.csv", sample=False):
    rows = []
    if sample:
        rows.append([SAMPLE_CREATOR[column] for column in CREATOR_COLUMNS])
    for creator in creators:
        rows.append(
            [
                creator.user.email,
                creator.display_name,
                creator.bio,
                creator.location,
                creator.audience_notes,
                ",".join(creator.niches.values_list("slug", flat=True)),
                "" if creator.rate_amount is None else creator.rate_amount,
                creator.rate_currency,
                "yes" if creator.share_metrics else "no",
                "yes" if creator.discoverable else "no",
            ]
        )
    return csv_response(filename, CREATOR_COLUMNS, rows)


def import_creators(uploaded):
    table = [
        row
        for row in read_table(uploaded, CREATOR_COLUMNS, optional_columns=("password",))
        if not _is_sample_creator(row[1])
    ]
    if not table:
        raise SpreadsheetError(["Delete the example row and add your own creators before importing."])
    subcategories = list(Subcategory.objects.select_related("category"))
    by_slug = {}
    for item in subcategories:
        by_slug.setdefault(item.slug, []).append(item)
    errors = []
    parsed = []
    seen_emails = set()
    for row_number, row in table:
        try:
            item = _parse_creator_row(row_number, row, by_slug)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if item["email"] in seen_emails:
            errors.append(f"Row {row_number}: email {item['email']} is repeated in this file.")
        seen_emails.add(item["email"])
        parsed.append(item)
    if errors:
        raise SpreadsheetError(errors[:20] + ([f"{len(errors) - 20} more row errors."] if len(errors) > 20 else []))

    User = get_user_model()
    emails = [item["email"] for item in parsed]
    users = {user.email.lower(): user for user in User.objects.filter(email__in=emails)}
    user_ids = [user.pk for user in users.values()]
    profiles = {profile.user_id: profile for profile in CreatorProfile.objects.filter(user_id__in=user_ids)}
    studios = {studio.user_id: studio for studio in StudioProfile.objects.filter(user_id__in=user_ids)}
    for item in parsed:
        user = users.get(item["email"])
        if user is None:
            continue
        studio = studios.get(user.pk)
        if studio is None or studio.role != StudioProfile.Role.CREATOR or user.pk not in profiles:
            errors.append(f"{item['email']} already belongs to a staff account and was not imported.")
    if errors:
        raise SpreadsheetError(errors[:20])

    created = updated = without_password = 0
    with transaction.atomic():
        for item in parsed:
            user = users.get(item["email"])
            if user is None:
                user = User.objects.create_user(
                    username=unique_username(item["email"]),
                    email=item["email"],
                    password=item["password"] or None,
                )
                if not item["password"]:
                    without_password += 1
                EmailAddress.objects.create(user=user, email=item["email"], primary=True, verified=True)
                StudioProfile.objects.update_or_create(
                    user=user,
                    defaults={"role": StudioProfile.Role.CREATOR, "permissions": []},
                )
                creator = CreatorProfile(user=user)
                created += 1
            else:
                creator = profiles[user.pk]
                if item["password"]:
                    user.set_password(item["password"])
                    user.save(update_fields=["password"])
                updated += 1
            creator.display_name = item["display_name"]
            creator.bio = item["bio"]
            creator.location = item["location"]
            creator.audience_notes = item["audience_notes"]
            creator.rate_amount = item["rate_amount"]
            creator.rate_currency = item["rate_currency"]
            creator.share_metrics = item["share_metrics"]
            creator.discoverable = item["discoverable"]
            creator.save()
            creator.niches.set(item["niches"])
            creator.refresh_status()
    return {"created": created, "updated": updated, "without_password": without_password}


def _is_sample_creator(row):
    return all(row.get(column, "") == value for column, value in SAMPLE_CREATOR.items())


def _parse_creator_row(row_number, row, by_slug):
    email = row["email"].lower()
    if not email:
        raise ValueError(f"Row {row_number}: email is required.")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValueError(f"Row {row_number}: {email} is not a valid email address.") from exc
    display_name = row["display_name"]
    if not display_name:
        raise ValueError(f"Row {row_number}: display_name is required.")
    if len(display_name) > 150:
        raise ValueError(f"Row {row_number}: display_name must be 150 characters or fewer.")
    location = row["location"]
    if len(location) > 120:
        raise ValueError(f"Row {row_number}: location must be 120 characters or fewer.")
    currency = (row["rate_currency"] or "INR").upper()
    if not currency.isalpha() or not 3 <= len(currency) <= 8:
        raise ValueError(f"Row {row_number}: rate_currency must be 3 to 8 letters, such as INR.")
    share_metrics = parse_bool(row["share_metrics"], row_number=row_number, column="share_metrics")
    discoverable = parse_bool(row["discoverable"], row_number=row_number, column="discoverable")
    if discoverable and not share_metrics:
        raise ValueError(f"Row {row_number}: discoverable requires share_metrics to be yes.")
    niches = []
    if row["niches"]:
        for token in row["niches"].split(","):
            slug = token.strip()
            if not slug:
                continue
            matches = by_slug.get(slug) or []
            if not matches:
                raise ValueError(f"Row {row_number}: niche slug '{slug}' does not exist.")
            if len(matches) > 1:
                raise ValueError(f"Row {row_number}: niche slug '{slug}' matches more than one subcategory.")
            if matches[0] not in niches:
                niches.append(matches[0])
    password = row.get("password") or ""
    if password:
        try:
            validate_password(password)
        except ValidationError as exc:
            raise ValueError(f"Row {row_number}: password {exc.messages[0]}") from exc
    return {
        "email": email,
        "display_name": display_name,
        "bio": row["bio"],
        "location": location,
        "audience_notes": row["audience_notes"],
        "niches": niches,
        "rate_amount": parse_decimal(
            row["rate_amount"],
            row_number=row_number,
            column="rate_amount",
            max_digits=12,
            decimal_places=2,
        ),
        "rate_currency": currency,
        "share_metrics": share_metrics,
        "discoverable": discoverable,
        "password": password,
    }
