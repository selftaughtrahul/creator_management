from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save


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


def ensure_studio_profile(sender, instance, created, **kwargs):
    if not created:
        return
    from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, StudioProfile

    role = StudioProfile.Role.ADMIN if instance.is_superuser else StudioProfile.Role.EMPLOYEE
    defaults = {"role": role}
    if role == StudioProfile.Role.EMPLOYEE:
        defaults["permissions"] = list(DEFAULT_EMPLOYEE_PERMISSIONS)
    StudioProfile.objects.get_or_create(user=instance, defaults=defaults)


def backfill_studio_profiles(sender, **kwargs):
    from django.contrib.auth import get_user_model
    from django.db.utils import OperationalError, ProgrammingError

    from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, StudioProfile

    User = get_user_model()
    try:
        missing = User.objects.filter(studio__isnull=True)
        StudioProfile.objects.bulk_create(
            [
                StudioProfile(
                    user=user,
                    role=StudioProfile.Role.ADMIN if user.is_superuser else StudioProfile.Role.EMPLOYEE,
                    permissions=[] if user.is_superuser else list(DEFAULT_EMPLOYEE_PERMISSIONS),
                )
                for user in missing
            ],
            ignore_conflicts=True,
        )
    except (OperationalError, ProgrammingError):
        return


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.auth import get_user_model

        post_migrate.connect(ensure_site, sender=self)
        post_migrate.connect(backfill_studio_profiles, sender=self)
        post_save.connect(
            ensure_studio_profile,
            sender=get_user_model(),
            dispatch_uid="accounts.studio_profile",
        )
