from django.core.management.base import BaseCommand

from networks.http import ProviderError
from networks.models import SocialAccount
from networks.sync import sync_account
from networks.facebook.jobs import enqueue_facebook_sync, process_facebook_job
from networks.instagram.jobs import enqueue_instagram_sync, process_instagram_job
from networks.youtube.jobs import enqueue_youtube_sync, process_youtube_job


class Command(BaseCommand):
    help = "Refresh every connected social account and store a new snapshot."

    def handle(self, *args, **options):
        accounts = SocialAccount.objects.select_related("user")
        if not accounts.exists():
            self.stdout.write("No connected accounts.")
            return
        for account in accounts:
            label = f"{account.user.email} · {account.platform}"
            try:
                if account.platform == "youtube" and not account.is_sample and account.has_token:
                    job = enqueue_youtube_sync(account, mode="incremental")
                    process_youtube_job(job)
                    if job.status not in {"completed", "partial"}:
                        raise ProviderError(job.last_error or "YouTube sync did not finish.")
                elif account.platform == "instagram" and not account.is_sample and account.has_token:
                    job = enqueue_instagram_sync(account, mode="incremental")
                    process_instagram_job(job)
                    if job.status not in {"completed", "partial"}:
                        raise ProviderError(job.last_error or "Instagram sync did not finish.")
                elif account.platform == "facebook" and not account.is_sample and account.has_token:
                    job = enqueue_facebook_sync(account, mode="incremental")
                    process_facebook_job(job)
                    if job.status not in {"completed", "partial"}:
                        raise ProviderError(job.last_error or "Facebook sync did not finish.")
                else:
                    sync_account(account)
            except ProviderError as exc:
                account.last_error = str(exc)
                account.save(update_fields=["last_error"])
                self.stderr.write(self.style.ERROR(f"{label}: {exc}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Synced {label}"))
