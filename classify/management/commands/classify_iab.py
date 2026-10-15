from django.core.management.base import BaseCommand

from classify import classify
from creators.models import CreatorProfile, IABClassification


class Command(BaseCommand):
    help = "Compare profile, content, and retrieved IAB classification for existing creators."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--creator-id", type=int, default=None)

    def handle(self, *args, **options):
        creators = CreatorProfile.objects.select_related("user").order_by("id")
        if options["creator_id"]:
            creators = creators.filter(pk=options["creator_id"])
        else:
            creators = creators.filter(user__social_accounts__platform__in=["instagram", "youtube"]).distinct()
        creators = creators[: max(options["limit"], 0)]
        if not creators:
            self.stdout.write("No Instagram or YouTube creators to classify. Import the taxonomy first if you have not.")
            return
        modes = [
            IABClassification.Mode.PROFILE,
            IABClassification.Mode.CONTENT,
            IABClassification.Mode.RETRIEVED,
        ]
        for creator in creators:
            self.stdout.write(f"{creator.pk} {creator.display_name}")
            for mode in modes:
                rows = classify(creator, mode=mode, method="rules")
                if not rows:
                    self.stdout.write(f"  {mode}: (no imported match)")
                    continue
                for row in rows:
                    custom = " / ".join(
                        part for part in (row.custom_niche, row.custom_sub_niche, row.custom_micro_niche) if part
                    )
                    label = f"{row.iab_category.unique_id} {row.iab_category.name}"
                    if custom:
                        label = f"{label} -> {custom}"
                    self.stdout.write(f"  {mode}: {label}")
