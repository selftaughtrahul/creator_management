from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_site(sender, **kwargs):
    from django.contrib.sites.models import Site
    from django.db.utils import OperationalError

    try:
        Site.objects.update_or_create(
            pk=1,
            defaults={"domain": "127.0.0.1:8000", "name": "Awisee"},
        )
    except OperationalError:
        return


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        post_migrate.connect(ensure_site, sender=self)
