
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from accounts.models import StudioProfile
from creators.classify import classify_with_rules
from creators.evaluation import evaluate_labels
from creators.jobs import enqueue_job, process_job
from creators.metrics import creator_metrics
from creators.models import (
    Category,
    Classification,
    ClassificationLabel,
    CreatorProfile,
    EnrichmentJob,
    Subcategory,
)
from creators.parser import parse_query
from creators.taxonomy import ensure_taxonomy
from networks.models import ContentItem, MetricSnapshot, SocialAccount

User = get_user_model()


def make_user(email, username, role=StudioProfile.Role.ADMIN):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    StudioProfile.objects.update_or_create(user=user, defaults={"role": role})
    return user


def make_creator(email, username, **fields):
    user = make_user(email, username, role=StudioProfile.Role.CREATOR)
    defaults = {
        "display_name": fields.pop("display_name", username),
        "bio": fields.pop("bio", "I make mobile gaming videos in India."),
        "location": fields.pop("location", "India"),
        "discoverable": fields.pop("discoverable", True),
        "share_metrics": fields.pop("share_metrics", True),
        "audience_notes": fields.pop("audience_notes", "Audience plays mobile games."),
    }
    defaults.update(fields)
    return CreatorProfile.objects.create(user=user, **defaults)


def add_snapshot(creator, platform="youtube", followers=120000, views=800000, likes=4000, comments=200):
    account = SocialAccount.objects.create(
        user=creator.user,
        platform=platform,
        display_name=creator.display_name,
        username=creator.display_name,
        external_id=f"{platform}-{creator.pk}",
        is_sample=False,
        access_token="token",
        last_synced_at=timezone.now(),
    )
    MetricSnapshot.objects.create(
        account=account,
        captured_at=timezone.now(),
        followers=followers,
        views=views,
        likes=likes,
        comments=comments,
    )
    ContentItem.objects.create(
        account=account,
        external_id="clip-1",
        title="BGMI ranked mobile gaming",
        caption="Mobile gaming campaign on BGMI",
        views=views,
        likes=likes,
        comments=comments,
        published_at=timezone.now(),
    )
    return account


class TaxonomyAndClassifyTests(TestCase):
    def setUp(self):
        ensure_taxonomy()

    def test_rules_classify_mobile_gaming_from_bio_and_captions(self):
        creator = make_creator("gamer@example.com", "gamer")
        add_snapshot(creator)
        result = classify_with_rules(creator)
        self.assertEqual(result.category.slug, "gaming")
        self.assertEqual(result.subcategory.slug, "mobile-gaming")
        self.assertEqual(result.method, Classification.Method.RULES)
        self.assertEqual(result.provenance, Classification.Provenance.ESTIMATE)
        self.assertTrue(result.evidence)

    def test_evaluation_math_on_labeled_fixture(self):
        gaming = Category.objects.get(slug="gaming")
        beauty = Category.objects.get(slug="beauty")
        a = make_creator("a@example.com", "a", bio="makeup tutorial lipstick")
        b = make_creator("b@example.com", "b", bio="mobile gaming bgmi")
        c = make_creator("c@example.com", "c", bio="skincare serum")
        d = make_creator("d@example.com", "d", bio="street food recipe")
        classify_with_rules(a)
        classify_with_rules(b)
        classify_with_rules(c)
        ClassificationLabel.objects.create(creator=a, category=beauty)
        ClassificationLabel.objects.create(creator=b, category=gaming)
        ClassificationLabel.objects.create(creator=c, category=beauty)
        ClassificationLabel.objects.create(creator=d, category=Category.objects.get(slug="food"))
        scores = evaluate_labels()
        self.assertTrue(scores["ready"])
        self.assertEqual(scores["labeled"], 4)
        self.assertEqual(scores["predicted"], 3)
        self.assertEqual(scores["correct"], 3)
        self.assertEqual(scores["precision"], 1.0)
        self.assertEqual(scores["recall"], 0.75)
        self.assertEqual(scores["f1"], 0.8571)

    def test_evaluation_without_labels_is_not_ready(self):
        scores = evaluate_labels([])
        self.assertFalse(scores["ready"])
        self.assertIsNone(scores["f1"])
        self.assertIn("not established", scores["message"])


class ParserAndMetricsTests(TestCase):
    def setUp(self):
        ensure_taxonomy()

    @override_settings(ANTHROPIC_API_KEY="")
    def test_rule_parser_reads_example_query(self):
        parsed = parse_query(
            "Find gaming creators in India with 50K–500K followers, strong engagement, and an audience relevant to mobile gaming campaigns.",
            use_anthropic=False,
        )
        criteria = parsed["criteria"]
        self.assertEqual(parsed["parser"], "rules")
        self.assertEqual(criteria["location"], "India")
        self.assertEqual(criteria["follower_min"], 50000)
        self.assertEqual(criteria["follower_max"], 500000)
        self.assertEqual(criteria["niche"], "gaming")
        self.assertEqual(criteria["subcategory"], "mobile-gaming")
        self.assertEqual(criteria["min_engagement"], "3.0")

    def test_metrics_never_invent_followers(self):
        creator = make_creator("empty@example.com", "empty")
        metrics = creator_metrics(creator)
        self.assertIsNone(metrics["followers"]["value"])
        self.assertEqual(metrics["followers"]["source"], "unavailable")
        self.assertIn("followers", metrics["missing"])


class JobTests(TestCase):
    def setUp(self):
        ensure_taxonomy()
        self.creator = make_creator("job@example.com", "jobber")

    def test_duplicate_open_jobs_are_rejected(self):
        first, created = enqueue_job(self.creator, "youtube")
        self.assertTrue(created)
        second, created_again = enqueue_job(self.creator, "youtube")
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EnrichmentJob.objects.filter(creator=self.creator).count(), 1)

    def test_job_without_live_connection_is_blocked(self):
        job, _ = enqueue_job(self.creator, "youtube")
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, EnrichmentJob.Status.BLOCKED)
        self.assertIn("authorized", job.last_error.lower())

    def test_sample_account_is_blocked(self):
        SocialAccount.objects.create(
            user=self.creator.user,
            platform="youtube",
            is_sample=True,
            access_token="x",
            external_id="demo",
        )
        job, _ = enqueue_job(self.creator, "youtube")
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, EnrichmentJob.Status.BLOCKED)


class CreatorAccessTests(TestCase):
    def setUp(self):
        ensure_taxonomy()
        self.team = make_user("team@example.com", "team")
        self.creator = make_creator("creator@example.com", "creator")

    def test_creator_cannot_open_studio_dashboard(self):
        self.client.force_login(self.creator.user)
        response = self.client.get(reverse("campaigns:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_team_can_open_studio(self):
        self.client.force_login(self.team)
        response = self.client.get(reverse("campaigns:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Campaign studio")

    def test_creator_join_creates_creator_role(self):
        response = self.client.post(
            reverse("creators:join"),
            {
                "full_name": "Riya Gamer",
                "email": "riya@example.com",
                "password1": "Northline-Studio-2026!",
                "password2": "Northline-Studio-2026!",
                "location": "India",
                "share_metrics": "on",
                "discoverable": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="riya@example.com")
        self.assertEqual(user.studio.role, StudioProfile.Role.CREATOR)
        self.assertTrue(CreatorProfile.objects.filter(user=user).exists())
        self.assertFalse(EmailAddress.objects.get(email="riya@example.com").verified)
