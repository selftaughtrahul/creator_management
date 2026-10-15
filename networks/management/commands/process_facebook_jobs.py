from django.core.management.base import BaseCommand

from networks.facebook.jobs import enqueue_facebook_sync, process_facebook_job, process_pending_facebook_jobs
from networks.models import SocialAccount


class Command(BaseCommand):
    help = "Run queued Facebook sync jobs. Use --full to enqueue a full sync for every live Facebook connection first."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Enqueue a full sync for each live Facebook account.")
        parser.add_argument("--account", type=int, help="Only enqueue and run the SocialAccount with this id.")

    def handle(self, *args, **options):
        if options["account"] or options["full"]:
            accounts = SocialAccount.objects.filter(platform="facebook", is_sample=False).exclude(access_token="")
            if options["account"]:
                accounts = accounts.filter(pk=options["account"])
            mode = "full" if options["full"] else "incremental"
            for account in accounts:
                job = enqueue_facebook_sync(account, mode=mode)
                process_facebook_job(job)
                self.stdout.write(f"{account.pk} {job.status} {job.outcome} posts={job.posts_upserted}")
            return
        jobs = process_pending_facebook_jobs()
        if not jobs:
            self.stdout.write("No queued Facebook syncs.")
            return
        for job in jobs:
            self.stdout.write(f"job={job.pk} status={job.status} outcome={job.outcome} posts={job.posts_upserted}")
