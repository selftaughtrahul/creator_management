from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StudioProfile
from campaigns.models import (
    Campaign,
    CampaignCreator,
    Invitation,
    MessageTemplate,
    RecommendationFeedback,
)
from campaigns.search import search_creators
from creators.classify import classify_with_rules
from creators.models import Category
from creators.taxonomy import ensure_taxonomy
from creators.tests import add_snapshot, make_creator, make_user

User = get_user_model()


class CampaignAndSearchTests(TestCase):
    def setUp(self):
        ensure_taxonomy()
        self.team = make_user("lead@example.com", "lead")
        self.client.force_login(self.team)
        self.gaming = Category.objects.get(slug="gaming")
        self.creator = make_creator("india-gamer@example.com", "indiagamer")
        add_snapshot(self.creator, followers=120000, views=50000, likes=4000, comments=500)
        classify_with_rules(self.creator)
        self.other = make_creator(
            "beauty@example.com",
            "beauty",
            bio="makeup lipstick tutorial",
            location="United States",
            audience_notes="beauty shoppers",
            display_name="Beauty",
        )
        add_snapshot(self.other, platform="instagram", followers=8000, views=1000, likes=20, comments=1)
        classify_with_rules(self.other)

    def test_campaign_create_and_edit(self):
        created = self.client.post(
            reverse("campaigns:create"),
            {
                "name": "Mobile launch",
                "objective": "Find mid-tier gaming creators",
                "budget": "50000",
                "niche": self.gaming.pk,
                "location": "India",
                "platforms": ["youtube"],
                "follower_min": 50000,
                "follower_max": 500000,
                "min_engagement": "3",
                "audience_notes": "mobile gaming campaigns",
                "status": Campaign.Status.ACTIVE,
            },
        )
        campaign = Campaign.objects.get(name="Mobile launch")
        self.assertRedirects(created, reverse("campaigns:detail", args=[campaign.pk]))
        edited = self.client.post(
            reverse("campaigns:edit", args=[campaign.pk]),
            {
                "name": "Mobile launch",
                "objective": "Updated brief",
                "budget": "60000",
                "niche": self.gaming.pk,
                "location": "India",
                "platforms": ["youtube"],
                "follower_min": 50000,
                "follower_max": 500000,
                "min_engagement": "3",
                "audience_notes": "mobile gaming campaigns",
                "status": Campaign.Status.ACTIVE,
            },
        )
        self.assertRedirects(edited, reverse("campaigns:detail", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.objective, "Updated brief")
        self.assertEqual(campaign.budget, Decimal("60000"))

    @override_settings(ANTHROPIC_API_KEY="")
    def test_search_returns_database_backed_india_gamer(self):
        result = search_creators(
            "Find gaming creators in India with 50K–500K followers, strong engagement, and an audience relevant to mobile gaming campaigns.",
            use_anthropic=False,
        )
        ids = [row["creator"].pk for row in result["page"].object_list]
        self.assertIn(self.creator.pk, ids)
        self.assertNotIn(self.other.pk, ids)
        gamer = next(row for row in result["page"].object_list if row["creator"].pk == self.creator.pk)
        self.assertIsNotNone(gamer["metrics"]["followers"]["value"])
        self.assertIn("MetricSnapshot", gamer["metrics"]["followers"]["source"])
        self.assertTrue(any("followers" in reason.lower() for reason in gamer["reasons"]))

    def test_feedback_is_unique_per_reviewer_and_can_update(self):
        campaign = Campaign.objects.create(owner=self.team, name="Test", niche=self.gaming, location="India")
        url = reverse("campaigns:feedback_submit", args=[campaign.pk, self.creator.pk])
        first = self.client.post(url, {"decision": "yes", "comment": "fits"})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(RecommendationFeedback.objects.count(), 1)
        self.client.post(url, {"decision": "no", "comment": "changed mind"})
        row = RecommendationFeedback.objects.get()
        self.assertEqual(row.decision, RecommendationFeedback.Decision.NO)
        self.assertEqual(row.comment, "changed mind")
        self.assertEqual(row.classification_snapshot.get("method"), "rules")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invitation_locmem_is_stored_locally_not_delivered(self):
        campaign = Campaign.objects.create(owner=self.team, name="Outreach", status=Campaign.Status.ACTIVE)
        CampaignCreator.objects.create(campaign=campaign, creator=self.creator, added_by=self.team)
        template = MessageTemplate.objects.create(
            name="Invite",
            subject="Join {campaign_name}",
            body="Hello {creator_name}",
            created_by=self.team,
        )
        self.client.post(
            reverse("campaigns:outreach", args=[campaign.pk]),
            {"template": template.pk, "creator_ids": str(self.creator.pk)},
        )
        confirm = self.client.post(
            reverse("campaigns:confirm_outreach", args=[campaign.pk]),
            {"confirm": "1"},
        )
        self.assertEqual(confirm.status_code, 302)
        invite = Invitation.objects.get()
        self.assertEqual(invite.status, Invitation.Status.STORED_LOCALLY)
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotEqual(invite.status, Invitation.Status.ACCEPTED)
        from accounts.models import Notification

        self.assertTrue(
            Notification.objects.filter(user=self.creator.user, kind=Notification.Kind.INVITATION).exists()
        )

    def test_creator_cannot_see_another_creators_campaign(self):
        campaign = Campaign.objects.create(owner=self.team, name="Private")
        CampaignCreator.objects.create(campaign=campaign, creator=self.creator)
        outsider = make_creator("out@example.com", "out")
        self.client.force_login(outsider.user)
        response = self.client.get(reverse("creators:my_campaign", args=[campaign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_dashboard_counts_match_rows(self):
        Campaign.objects.create(owner=self.team, name="Live", status=Campaign.Status.ACTIVE, budget=1000)
        response = self.client.get(reverse("campaigns:dashboard"))
        self.assertContains(response, "Campaign studio")
        self.assertEqual(response.context["kpis"]["campaigns_total"], Campaign.objects.count())
        self.assertEqual(response.context["kpis"]["campaigns_active"], 1)
        self.assertIsNone(response.context["kpis"]["acceptance_rate"])
