import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from campaigns.intake import import_requirement_records, import_requirement_text


class Command(BaseCommand):
    help = "Import the Campaign Requirements sheet into Campaign rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="",
            help="Optional CSV or JSON export. Without this flag, the saved Campaign Requirements sheet is imported.",
        )

    def handle(self, *args, **options):
        owner = get_user_model().objects.order_by("id").first()
        if owner is None:
            raise CommandError("Create a staff user before importing campaigns.")
        if options["file"]:
            path = Path(options["file"])
            if not path.is_file():
                raise CommandError(f"Campaign requirements file not found: {path}")
            text = path.read_text(encoding="utf-8-sig")
            if path.suffix.lower() == ".json":
                records = json.loads(text)
                created, updated = import_requirement_records(records, owner)
            else:
                created, updated = import_requirement_text(text, owner)
        else:
            from campaigns.requirement_sheet import RECORDS

            created, updated = import_requirement_records(RECORDS, owner)
        self.stdout.write(f"Imported campaigns created={created} updated={updated} owner={owner.email}")
