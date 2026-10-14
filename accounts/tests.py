import re
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from allauth.account.models import EmailAddress

User = get_user_model()


class EmailSignupTests(TestCase):
    password = "Northline-Studio-2026!"

    def test_email_signup_cannot_enter_until_verified(self):
        response = self.client.post(
            reverse("account_signup"),
            {
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "password1": self.password,
                "password2": self.password,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verify your email")
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        user = User.objects.get(email="ada@example.com")
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertFalse(EmailAddress.objects.get(user=user).verified)

        blocked = self.client.post(
            reverse("account_login"),
            {"login": "ada@example.com", "password": self.password},
            follow=True,
        )
        self.assertFalse(blocked.wsgi_request.user.is_authenticated)
        self.assertContains(blocked, "Verify your email")

        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"https?://[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(match)
        confirm_path = urlparse(match.group(0)).path
        self.client.get(confirm_path)
        confirmed = self.client.post(confirm_path, follow=True)
        self.assertTrue(EmailAddress.objects.get(email="ada@example.com").verified)
        if not confirmed.wsgi_request.user.is_authenticated:
            confirmed = self.client.post(
                reverse("account_login"),
                {"login": "ada@example.com", "password": self.password},
                follow=True,
            )
        self.assertTrue(confirmed.wsgi_request.user.is_authenticated)
        self.assertContains(confirmed, "Studio overview")


class LegalPageTests(TestCase):
    def test_privacy_and_deletion_pages_are_public(self):
        privacy = self.client.get(reverse("accounts:privacy"))
        deletion = self.client.get(reverse("accounts:data_deletion"))
        self.assertContains(privacy, "Privacy policy")
        self.assertContains(privacy, "Connected networks")
        self.assertContains(deletion, "Data deletion")
