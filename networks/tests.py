from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from allauth.account.models import EmailAddress

from networks.demo import PROFILES, connect_sample
from networks.models import SocialAccount

User = get_user_model()


def make_user(email, username):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


class SampleConnectionTests(TestCase):
    def setUp(self):
        self.user = make_user("ada@example.com", "ada")
        self.client.force_login(self.user)

    def test_sample_data_feeds_overview_platform_and_post(self):
        for platform in ("instagram", "facebook", "youtube", "twitter"):
            response = self.client.post(reverse("networks:sample", args=[platform]))
            self.assertRedirects(response, reverse("analytics:platform", args=[platform]))

        audience = sum(profile["followers"] for profile in PROFILES.values())
        overview = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(overview, "Studio overview")
        self.assertContains(overview, f"{audience:,}")
        self.assertContains(overview, "Sample")
        self.assertContains(overview, "Studio tour, ten minutes")

        youtube = self.client.get(reverse("analytics:platform", args=["youtube"]))
        self.assertContains(youtube, "Northline")
        self.assertContains(youtube, "Subscribers")

        item = SocialAccount.objects.get(user=self.user, platform="youtube").contents.get(external_id="tour")
        detail = self.client.get(item.get_absolute_url())
        self.assertContains(detail, "Studio tour, ten minutes")
        self.assertContains(detail, "Watch time")

        other = make_user("bea@example.com", "bea")
        self.client.force_login(other)
        self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 404)

    def test_sync_sample_adds_a_snapshot_and_disconnect_removes_it(self):
        self.client.post(reverse("networks:sample", args=["twitter"]))
        account = SocialAccount.objects.get(user=self.user, platform="twitter")
        before = account.snapshots.count()
        self.client.post(reverse("networks:sync", args=["twitter"]))
        self.assertEqual(account.snapshots.count(), before + 1)
        self.client.post(reverse("networks:disconnect", args=["twitter"]))
        self.assertFalse(SocialAccount.objects.filter(user=self.user, platform="twitter").exists())

    def test_live_oauth_without_keys_stays_on_connections(self):
        response = self.client.get(reverse("networks:start", args=["facebook"]))
        self.assertRedirects(response, reverse("networks:connections"))

    def test_anonymous_studio_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
