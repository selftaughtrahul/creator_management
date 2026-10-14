from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress

from accounts.models import StudioProfile
from creators.models import CreatorProfile
from networks.demo import PROFILES, connect_sample
from networks.fetchers import _meta_audience_history, gender_bucket
from networks.models import SocialAccount

User = get_user_model()


def make_user(email, username):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    StudioProfile.objects.update_or_create(user=user, defaults={"role": StudioProfile.Role.CREATOR})
    CreatorProfile.objects.get_or_create(user=user, defaults={"display_name": username})
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
        self.assertContains(overview, "All data")
        self.assertContains(overview, "Growth (30 days)")
        self.assertContains(overview, "Posting habits")
        self.assertContains(overview, "Audience snapshots")
        self.assertContains(overview, "Audience demographics")
        self.assertContains(overview, "India")
        self.assertContains(overview, "Female")
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
        latest = account.snapshots.order_by("-captured_at").first()
        previous = account.snapshots.order_by("-captured_at")[1]
        self.assertGreater(latest.followers, previous.followers)
        self.assertGreater(latest.breakdowns.filter(dimension="country").count(), 0)
        self.assertGreater(latest.breakdowns.filter(dimension="gender").count(), 0)
        self.client.post(reverse("networks:disconnect", args=["twitter"]))
        self.assertFalse(SocialAccount.objects.filter(user=self.user, platform="twitter").exists())

    @override_settings(META_APP_ID="", META_APP_SECRET="")
    def test_live_oauth_without_keys_stays_on_connections(self):
        response = self.client.get(reverse("networks:start", args=["facebook"]))
        self.assertRedirects(response, reverse("networks:connections"))

    @override_settings(
        INSTAGRAM_APP_ID="2133499753913865",
        INSTAGRAM_REDIRECT_URI="https://localhost:8000/connections/instagram/callback/",
        DEBUG=True,
    )
    def test_instagram_authorize_opens_instagram(self):
        response = self.client.get(reverse("networks:start", args=["instagram"]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://www.instagram.com/oauth/authorize?"))
        self.assertIn("client_id=2133499753913865", response.url)
        self.assertIn("enable_fb_login=0", response.url)
        self.assertIn("scope=instagram_business_basic", response.url)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Flocalhost%3A8000%2Fconnections%2Finstagram%2Fcallback%2F",
            response.url,
        )
        self.assertNotIn("facebook.com", response.url)
        self.assertNotIn("config_id", response.url)

    def test_insight_history_rebuilds_daily_gains_into_totals(self):
        payload = {
            "data": [
                {
                    "name": "follower_count",
                    "values": [
                        {"value": 4, "end_time": "2026-10-12T07:00:00+0000"},
                        {"value": 6, "end_time": "2026-10-13T07:00:00+0000"},
                    ],
                }
            ]
        }
        with patch("networks.fetchers.api_get", return_value=payload):
            snapshots = _meta_audience_history(
                "1789",
                "token",
                metrics=("follower_count",),
                followers_key="follower_count",
                current_followers=100,
            )
        self.assertEqual(gender_bucket("F.25-34"), "female")
        self.assertEqual(gender_bucket("M.18-24"), "male")
        self.assertEqual(gender_bucket("U"), "unknown")
        self.assertEqual([item.followers for item in snapshots], [94, 100])
        self.assertEqual(snapshots[-1].captured_at, datetime(2026, 10, 13, 7, tzinfo=dt_timezone.utc))

    def test_anonymous_studio_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
