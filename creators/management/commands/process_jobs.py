from django.core.management.base import BaseCommand

from creators.jobs import process_pending_jobs


class Command(BaseCommand):
    help = "Process a limited batch of pending creator enrichment jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        processed = process_pending_jobs(limit=options["limit"])
        if not processed:
            self.stdout.write("No pending jobs.")
            return
        for job in processed:
            self.stdout.write(f"{job.pk} {job.creator} {job.platform} {job.status}")
