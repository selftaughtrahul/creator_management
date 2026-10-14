from django.urls import reverse

from allauth.account.adapter import DefaultAccountAdapter

from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, StudioProfile


class AccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        from accounts.permissions import home_url

        return home_url(request.user)

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=True)
        StudioProfile.objects.update_or_create(
            user=user,
            defaults={
                "role": StudioProfile.Role.EMPLOYEE,
                "permissions": list(DEFAULT_EMPLOYEE_PERMISSIONS),
            },
        )
        return user

    def get_signup_redirect_url(self, request):
        return reverse("account_email_verification_sent")
