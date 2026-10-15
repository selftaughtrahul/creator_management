import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction

from classify.mappings import STARTER_MAPPINGS
from creators.models import IABCategory, IABMapping, IABTaxonomyImport

logger = logging.getLogger("classify")

TAXONOMY_VERSION = "3.1"
EXPECTED_COLUMNS = ["Unique ID", "Parent", "Name", "Tier 1", "Tier 2", "Tier 3", "Tier 4"]


class IABTaxonomyError(Exception):
    pass


def taxonomy_path():
    return Path(getattr(settings, "IAB_TAXONOMY_FILE"))


def parse_taxonomy_tsv(text):
    """Parse the official TSV. Raises when the version or columns do not match 3.1."""
    lines = text.lstrip("\ufeff").splitlines()
    if len(lines) < 3:
        raise IABTaxonomyError("The taxonomy file has no data rows.")
    if "Content Taxonomy v3.1" not in lines[0]:
        raise IABTaxonomyError("Expected the Content Taxonomy v3.1 header. Refusing to import another version.")
    columns = [cell.strip() for cell in lines[1].split("\t")]
    if columns[: len(EXPECTED_COLUMNS)] != EXPECTED_COLUMNS:
        raise IABTaxonomyError("Unexpected columns. Expected Unique ID, Parent, Name, Tier 1, Tier 2, Tier 3, Tier 4.")
    rows = []
    seen = set()
    for line_number, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        cells = line.split("\t")
        cells += [""] * (7 - len(cells))
        unique_id, parent_id, name, tier_1, tier_2, tier_3, tier_4 = [cell.strip() for cell in cells[:7]]
        if not unique_id or not name:
            raise IABTaxonomyError(f"Row {line_number} is missing Unique ID or Name.")
        if unique_id in seen:
            raise IABTaxonomyError(f"Duplicate Unique ID {unique_id} at row {line_number}.")
        seen.add(unique_id)
        rows.append(
            {
                "unique_id": unique_id,
                "parent_id": parent_id,
                "name": name,
                "tier_1": tier_1,
                "tier_2": tier_2,
                "tier_3": tier_3,
                "tier_4": tier_4,
            }
        )
    if not rows:
        raise IABTaxonomyError("The taxonomy file has no categories.")
    return rows


def import_taxonomy(path=None, *, load_mappings=True):
    path = Path(path or taxonomy_path())
    if not path.is_file():
        raise IABTaxonomyError(f"Taxonomy file not found: {path}")
    rows = parse_taxonomy_tsv(path.read_text(encoding="utf-8-sig"))
    version = getattr(settings, "IAB_TAXONOMY_VERSION", TAXONOMY_VERSION)
    with transaction.atomic():
        for row in rows:
            IABCategory.objects.update_or_create(
                taxonomy_version=version,
                unique_id=row["unique_id"],
                defaults={
                    "parent_id": row["parent_id"],
                    "name": row["name"],
                    "tier_1": row["tier_1"],
                    "tier_2": row["tier_2"],
                    "tier_3": row["tier_3"],
                    "tier_4": row["tier_4"],
                },
            )
        IABTaxonomyImport.objects.create(
            taxonomy_version=version,
            row_count=len(rows),
            source_name=path.name,
        )
        mapped = load_starter_mappings() if load_mappings else 0
    logger.info("iab_imported version=%s rows=%s mappings=%s", version, len(rows), mapped)
    return len(rows), mapped


def load_starter_mappings():
    version = getattr(settings, "IAB_TAXONOMY_VERSION", TAXONOMY_VERSION)
    loaded = 0
    for item in STARTER_MAPPINGS:
        category = IABCategory.objects.filter(
            taxonomy_version=version,
            unique_id=item["iab_category_id"],
        ).first()
        if category is None or category.name != item["expected_name"]:
            logger.warning(
                "iab_mapping_skipped iab_category_id=%s expected_name=%s",
                item["iab_category_id"],
                item["expected_name"],
            )
            continue
        IABMapping.objects.update_or_create(
            iab_category=category,
            custom_niche=item["custom_niche"],
            custom_sub_niche=item["custom_sub_niche"],
            custom_micro_niche=item["custom_micro_niche"],
            defaults={"keywords": item["keywords"]},
        )
        loaded += 1
    return loaded
