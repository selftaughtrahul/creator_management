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

    def test_staff_cannot_sign_themselves_up(self):
        closed = self.client.post(
            reverse("account_signup"),
            {
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "password1": self.password,
                "password2": self.password,
            },
        )
        self.assertEqual(closed.status_code, 403)
        self.assertContains(closed, "Staff cannot sign up here", status_code=403)
        self.assertEqual(self.client.get(reverse("accounts:employee_join")).status_code, 404)
        self.assertFalse(User.objects.filter(email="ada@example.com").exists())

    def test_creator_signup_cannot_enter_until_verified(self):
        response = self.client.post(
            reverse("creators:join"),
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
        self.assertEqual(user.studio.role, "creator")
        self.assertFalse(EmailAddress.objects.get(user=user).verified)

        blocked = self.client.post(
            reverse("account_login"),
            {"login": "ada@example.com", "password": self.password},
            follow=True,
        )
        self.assertFalse(blocked.wsgi_request.user.is_authenticated)

        self.assertGreaterEqual(len(mail.outbox), 1)
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
        self.assertContains(confirmed, "Creator profile")

    def test_staff_log_in_with_email_and_creator_login_rejects_them(self):
        staff = make_user("emp@example.com", "emp", role=StudioProfile.Role.EMPLOYEE)
        home = self.client.get(reverse("accounts:home"))
        self.assertContains(home, "Creator signup")
        self.assertNotContains(home, "Register as a creator")
        self.assertNotContains(home, "Creator log in")
        self.assertNotContains(home, "Staff signup")
        rejected = self.client.post(
            reverse("account_login"),
            {"login": staff.email, "password": self.password},
        )
        self.assertContains(rejected, "Staff accounts use the staff log in")
        self.assertFalse(rejected.wsgi_request.user.is_authenticated)
        logged_in = self.client.post(
            reverse("accounts:staff_login"),
            {"login": staff.email, "password": self.password},
            follow=True,
        )
        self.assertTrue(logged_in.wsgi_request.user.is_authenticated)
        self.assertContains(logged_in, "Campaign studio")
        creator = make_creator("c@example.com", "creat")
        self.client.logout()
        creator_on_staff = self.client.post(
            reverse("accounts:staff_login"),
            {"login": creator.user.email, "password": self.password},
        )
        self.assertContains(creator_on_staff, "This log in is for staff")
        self.assertFalse(creator_on_staff.wsgi_request.user.is_authenticated)


class RoleAccessTests(TestCase):
    def test_employee_without_discover_cannot_open_discover(self):
        employee = make_user("emp@example.com", "emp", role=StudioProfile.Role.EMPLOYEE)
        employee.studio.permissions = ["studio_dashboard", "campaigns"]
        employee.studio.save()
        self.client.force_login(employee)
        self.assertEqual(self.client.get(reverse("campaigns:discover")).status_code, 403)
        self.assertEqual(self.client.get(reverse("campaigns:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:people")).status_code, 403)

    def test_only_superuser_can_open_people(self):
        admin = make_user("boss@example.com", "boss", role=StudioProfile.Role.ADMIN)
        make_creator("c@example.com", "creat")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("accounts:people")).status_code, 403)
        studio = self.client.get(reverse("campaigns:dashboard"))
        self.assertNotContains(studio, "People")
        directory = self.client.get(reverse("creators:directory"))
        self.assertContains(directory, "creat")
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        people = self.client.get(reverse("accounts:people"))
        self.assertContains(people, "People")

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

    def test_staff_do_not_see_personal_network_nav_or_connections(self):
        admin = make_user("nav@example.com", "navadmin", role=StudioProfile.Role.ADMIN)
        self.client.force_login(admin)
        studio = self.client.get(reverse("campaigns:dashboard"))
        self.assertContains(studio, "Campaign studio")
        self.assertNotContains(studio, "My overview")
        self.assertNotContains(studio, "My analytics")
        self.assertEqual(self.client.get(reverse("analytics:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("networks:connections")).status_code, 403)

    def test_user_can_choose_a_premium_theme(self):
        admin = make_user("theme@example.com", "themeadmin", role=StudioProfile.Role.ADMIN)
        self.client.force_login(admin)
        saved = self.client.post(reverse("accounts:theme"), {"theme": "jade"})
        self.assertEqual(saved.status_code, 200)
        admin.studio.refresh_from_db()
        self.assertEqual(admin.studio.theme, "jade")
        page = self.client.get(reverse("campaigns:dashboard"))
        self.assertContains(page, 'data-theme="jade"')
        self.assertContains(page, "Ivory")
        self.assertContains(page, "Rouge")
        rejected = self.client.post(reverse("accounts:theme"), {"theme": "neon"})
        self.assertEqual(rejected.status_code, 400)


class LegalPageTests(TestCase):
    def test_privacy_and_deletion_pages_are_public(self):
        privacy = self.client.get(reverse("accounts:privacy"))
        deletion = self.client.get(reverse("accounts:data_deletion"))
        self.assertContains(privacy, "Privacy policy")
        self.assertContains(privacy, "Connected networks")
        self.assertContains(deletion, "Data deletion")
