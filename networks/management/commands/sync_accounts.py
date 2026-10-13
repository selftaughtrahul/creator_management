from django.core.management.base import BaseCommand

from networks.http import ProviderError
from networks.models import SocialAccount
from networks.sync import sync_account


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
                sync_account(account)
            except ProviderError as exc:
                account.last_error = str(exc)
                account.save(update_fields=["last_error"])
                self.stderr.write(self.style.ERROR(f"{label}: {exc}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Synced {label}"))
