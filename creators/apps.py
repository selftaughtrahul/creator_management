from django.apps import AppConfig
from django.db.models.signals import post_migrate


def seed_taxonomy(sender, **kwargs):
    from django.db.utils import OperationalError, ProgrammingError

    from creators.taxonomy import ensure_taxonomy

    try:
        ensure_taxonomy()
    except (OperationalError, ProgrammingError):
        return


class CreatorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "creators"

    def ready(self):
        post_migrate.connect(seed_taxonomy, sender=self, dispatch_uid="creators.taxonomy")
