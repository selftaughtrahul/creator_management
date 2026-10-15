from django.core.management.base import BaseCommand

from networks.instagram.jobs import enqueue_instagram_sync, process_instagram_job, process_pending_instagram_jobs
from networks.models import SocialAccount


class Command(BaseCommand):
    help = "Run queued Instagram sync jobs. Use --full to enqueue a full sync for every live Instagram connection first."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Enqueue a full sync for each live Instagram account.")
        parser.add_argument("--account", type=int, help="Only enqueue and run the SocialAccount with this id.")

    def handle(self, *args, **options):
        if options["account"] or options["full"]:
            accounts = SocialAccount.objects.filter(platform="instagram", is_sample=False).exclude(access_token="")
            if options["account"]:
                accounts = accounts.filter(pk=options["account"])
            mode = "full" if options["full"] else "incremental"
            for account in accounts:
                job = enqueue_instagram_sync(account, mode=mode)
                process_instagram_job(job)
                self.stdout.write(f"{account.pk} {job.status} {job.outcome} media={job.media_upserted}")
            return
        jobs = process_pending_instagram_jobs()
        if not jobs:
            self.stdout.write("No queued Instagram syncs.")
            return
        for job in jobs:
            self.stdout.write(f"job={job.pk} status={job.status} outcome={job.outcome} media={job.media_upserted}")
