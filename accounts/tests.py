import re
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from allauth.account.models import EmailAddress

from accounts.models import Notification, StudioProfile
from creators.tests import make_creator, make_user

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
        self.assertEqual(user.studio.role, "employee")
        self.assertContains(confirmed, "Campaign studio")


class RoleAccessTests(TestCase):
    def test_employee_without_discover_cannot_open_discover(self):
        employee = make_user("emp@example.com", "emp", role=StudioProfile.Role.EMPLOYEE)
        employee.studio.permissions = ["studio_dashboard", "campaigns"]
        employee.studio.save()
        self.client.force_login(employee)
        self.assertEqual(self.client.get(reverse("campaigns:discover")).status_code, 403)
        self.assertEqual(self.client.get(reverse("campaigns:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:people")).status_code, 403)

    def test_admin_can_manage_people_and_see_creators(self):
        admin = make_user("boss@example.com", "boss", role=StudioProfile.Role.ADMIN)
        make_creator("c@example.com", "creat")
        self.client.force_login(admin)
        people = self.client.get(reverse("accounts:people"))
        self.assertContains(people, "People")
        directory = self.client.get(reverse("creators:directory"))
        self.assertContains(directory, "creat")

    def test_creator_accepts_invitation_and_notifies_staff(self):
        from campaigns.models import Campaign, Invitation

        admin = make_user("lead2@example.com", "lead2", role=StudioProfile.Role.ADMIN)
        creator = make_creator("invited@example.com", "invited")
        campaign = Campaign.objects.create(owner=admin, name="Spring")
        invitation = Invitation.objects.create(
            campaign=campaign,
            creator=creator,
            to_email=creator.user.email,
            subject="Join Spring",
            body="Please join",
            status=Invitation.Status.STORED_LOCALLY,
            created_by=admin,
        )
        self.client.force_login(creator.user)
        response = self.client.post(
            reverse("creators:respond_invitation", args=[invitation.pk]),
            {"decision": "accept"},
        )
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.ACCEPTED)
        self.assertTrue(
            Notification.objects.filter(user=admin, kind=Notification.Kind.INVITATION).exists()
        )


class LegalPageTests(TestCase):
    def test_privacy_and_deletion_pages_are_public(self):
        privacy = self.client.get(reverse("accounts:privacy"))
        deletion = self.client.get(reverse("accounts:data_deletion"))
        self.assertContains(privacy, "Privacy policy")
        self.assertContains(privacy, "Connected networks")
        self.assertContains(deletion, "Data deletion")
