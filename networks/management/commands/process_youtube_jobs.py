from django.core.management.base import BaseCommand

from networks.models import SocialAccount
from networks.youtube.jobs import enqueue_youtube_sync, process_pending_youtube_jobs, process_youtube_job


class Command(BaseCommand):
    help = "Run queued YouTube sync jobs. Use --full to enqueue a full sync for every live YouTube connection first."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Enqueue a full sync for each live YouTube account.")
        parser.add_argument("--account", type=int, help="Only enqueue and run the SocialAccount with this id.")

    def handle(self, *args, **options):
        if options["account"] or options["full"]:
            accounts = SocialAccount.objects.filter(platform="youtube", is_sample=False).exclude(access_token="")
            if options["account"]:
                accounts = accounts.filter(pk=options["account"])
            mode = "full" if options["full"] else "incremental"
            for account in accounts:
                job = enqueue_youtube_sync(account, mode=mode)
                process_youtube_job(job)
                self.stdout.write(f"{account.pk} {job.status} {job.outcome} videos={job.videos_upserted}")
            return
        jobs = process_pending_youtube_jobs()
        if not jobs:
            self.stdout.write("No queued YouTube syncs.")
            return
        for job in jobs:
            self.stdout.write(f"job={job.pk} status={job.status} outcome={job.outcome} videos={job.videos_upserted}")
