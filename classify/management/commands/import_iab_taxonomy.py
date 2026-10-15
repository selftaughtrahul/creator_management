from django.core.management.base import BaseCommand, CommandError

from classify import IABTaxonomyError, import_taxonomy


class Command(BaseCommand):
    help = "Import the vendored IAB Content Taxonomy 3.1 TSV and the Awisee niche mappings."

    def add_arguments(self, parser):
        parser.add_argument("--path", default="", help="TSV path. Defaults to the vendored 3.1 file.")
        parser.add_argument("--skip-mappings", action="store_true")

    def handle(self, *args, **options):
        try:
            rows, mappings = import_taxonomy(
                options["path"] or None,
                load_mappings=not options["skip_mappings"],
            )
        except IABTaxonomyError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Imported Content Taxonomy 3.1: {rows} categories, {mappings} mappings.")
