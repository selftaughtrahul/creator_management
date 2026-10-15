from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from accounts.models import StudioProfile
from creators.models import CreatorProfile
from networks.facebook.client import FacebookAPIError, FacebookClient, classify_error
from networks.facebook.snapshots import save_snapshot
from networks.facebook.sync import sync_facebook
from networks.facebook_models import FacebookComment, FacebookPage, FacebookPost, FacebookSnapshot, FacebookSyncJob
from networks.models import SocialAccount

User = get_user_model()
SCOPES = "pages_show_list pages_read_engagement pages_read_user_content public_profile"


def make_creator(email, username):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    StudioProfile.objects.update_or_create(user=user, defaults={"role": StudioProfile.Role.CREATOR})
    CreatorProfile.objects.get_or_create(user=user, defaults={"display_name": username})
    return user


class FakeFacebook:
    def __init__(self, token, *, empty=False, insights_error=None):
        self.token = token
        self.calls = 0
        self.fields = []
        self.metrics = []
        self.empty = empty
        self.insights_error = insights_error

    def get(self, path, params=None):
        self.calls += 1
        params = params or {}
        self.fields.append(params.get("fields") or "")
        self.metrics.append(params.get("metric") or "")
        if "access_token" in params:
            raise AssertionError("token leaked into params")
        if path in {"111", "222"}:
            followers = 80 if path == "111" else 10
            return {
                "id": path,
                "name": "Northline" if path == "111" else "Side page",
                "username": "northline" if path == "111" else "",
                "about": "Studio",
                "category": "Media",
                "followers_count": followers,
                "fan_count": followers - 5,
                "link": f"https://facebook.com/{path}",
            }
        if params.get("metric") == "page_follows_country":
            return {
                "data": [
                    {
                        "name": "page_follows_country",
                        "values": [{"value": {"IN": 40}, "end_time": "2026-10-14T18:30:00+0000"}],
                    }
                ]
            }
        if params.get("metric"):
            raise FacebookAPIError(f"metric {params.get('metric')} is not available", kind="unsupported", code=100)
        raise AssertionError(path)

    def paged(self, path, params, limit):
        self.calls += 1
        fields = (params or {}).get("fields") or ""
        self.fields.append(fields)
        if "source" in fields or "media_source" in fields:
            raise AssertionError("video file URL was requested")
        if path == "me/accounts":
            if self.empty:
                return []
            return [
                {"id": "111", "name": "Northline", "access_token": "page-token-a", "followers_count": 80},
                {"id": "222", "name": "Side page", "access_token": "page-token-b", "followers_count": 10},
            ][:limit]
        if path.endswith("/posts"):
            page_id = path.split("/")[0]
            shown = {
                "id": f"{page_id}_1",
                "message": "Launch day",
                "created_time": "2026-10-03T08:00:00+0000",
                "permalink_url": "https://facebook.com/posts/1",
                "full_picture": "https://cdn.example/thumb.jpg",
                "status_type": "added_photos",
                "shares": {"count": 2},
                "reactions": {"summary": {"total_count": 6}},
                "likes": {"summary": {"total_count": 4}},
                "comments": {"summary": {"total_count": 1 if page_id == "111" else 0}},
            }
            if page_id == "222":
                return [shown][:limit]
            hidden = {
                "id": f"{page_id}_2",
                "message": "Second",
                "created_time": "2026-10-02T08:00:00+0000",
                "permalink_url": "https://facebook.com/posts/2",
            }
            return [shown, hidden][:limit]
        if path.endswith("/comments"):
            if not path.startswith("111_1/"):
                return []
            return [
                {
                    "id": "c1",
                    "message": "Nice",
                    "created_time": "2026-10-03T09:00:00+0000",
                    "like_count": 1,
                    "from": {"name": "Ava", "id": "should-not-matter"},
                }
            ]
        raise AssertionError(path)

    def insights(self, path, metrics, params):
        self.calls += 1
        self.metrics.extend(metrics)
        if self.insights_error and path.endswith("/insights") and "post" not in path and "_" not in path.split("/")[0]:
            raise self.insights_error
        if path.endswith("/posts") or "/posts/" in path:
            raise AssertionError(path)
        if "_" in path.split("/")[0]:
            data = []
            unsupported = []
            for name in metrics:
                if name == "post_video_views":
                    unsupported.append(name)
                    continue
                if name == "post_reactions_by_type_total":
                    data.append({"name": name, "values": [{"value": {"LIKE": 4, "LOVE": 2}}]})
                    continue
                data.append({"name": name, "values": [{"value": 9 if name == "post_media_view" else 3}]})
            return {"data": data, "unsupported": unsupported}
        end = "2026-10-14T18:30:00+0000"
        data = []
        unsupported = []
        for name in metrics:
            if name in {"page_video_view_time", "page_impressions"}:
                unsupported.append(name)
                continue
            amount = {"page_follows": 80, "page_daily_follows_unique": 4, "page_media_view": 21}.get(name, 2)
            data.append({"name": name, "values": [{"value": amount, "end_time": end}]})
        return {"data": data, "unsupported": unsupported}


class FacebookClientTests(TestCase):
    def test_error_kinds(self):
        self.assertEqual(classify_error(400, {"error": {"code": 100, "message": "metric is invalid"}})[0], "unsupported")
        self.assertEqual(classify_error(403, {"error": {"code": 10, "message": "permission denied"}})[0], "permission")
        self.assertEqual(classify_error(400, {"error": {"code": 4, "message": "rate limit"}})[0], "retryable")
        self.assertEqual(classify_error(400, {"error": {"code": 190, "message": "access token expired"}})[0], "token")

    def test_unsupported_metric_is_split_out(self):
        client = FacebookClient("token")

        def fake_get(path, params=None):
            metric = (params or {}).get("metric", "")
            if metric == "page_media_view":
                raise FacebookAPIError("metric is invalid", kind="unsupported", code=100)
            if "," in metric and "page_media_view" in metric:
                raise FacebookAPIError("metric is invalid", kind="unsupported", code=100)
            return {"data": [{"name": metric, "values": [{"value": 1}]}]}

        with patch.object(client, "get", side_effect=fake_get):
            payload = client.insights("111/insights", ["page_follows", "page_media_view"], {"period": "day"})
        self.assertEqual(payload["unsupported"], ["page_media_view"])
        self.assertEqual(payload["data"][0]["name"], "page_follows")


class FacebookSyncTests(TestCase):
    def setUp(self):
        self.user = make_creator("fb@example.com", "fbname")
        self.account = SocialAccount.objects.create(
            user=self.user,
            platform="facebook",
            access_token="user-token",
            token_expires_at=timezone.now() + timedelta(days=30),
        )

    def _run(self, fake):
        self.fake = fake
        job = FacebookSyncJob.objects.create(account=self.account, mode="full", granted_scopes=SCOPES)

        def factory(token):
            return fake

        with (
            patch("networks.facebook.sync.ensure_access_token", return_value="user-token"),
            patch("networks.facebook.sync.FacebookClient", side_effect=factory),
        ):
            sync_facebook(self.account, job)
        job.refresh_from_db()
        return job

    def test_full_sync_stores_pages_posts_comments_and_insights(self):
        job = self._run(FakeFacebook("user-token"))
        self.assertEqual(job.status, FacebookSyncJob.Status.COMPLETED, (job.stages, job.last_error))
        self.assertEqual(FacebookPage.objects.count(), 2)
        primary = FacebookPage.objects.get(page_id="111")
        self.assertTrue(primary.is_primary)
        self.assertEqual(primary.followers_count, 80)
        self.assertEqual(primary.fan_count, 75)
        self.assertFalse(FacebookPage.objects.get(page_id="222").is_primary)
        hidden = FacebookPost.objects.get(post_id="111_2")
        self.assertIsNone(hidden.reaction_count)
        shown = FacebookPost.objects.get(post_id="111_1")
        self.assertEqual(shown.reaction_count, 6)
        self.assertEqual(shown.like_count, 4)
        self.assertEqual(shown.views, 9)
        self.assertEqual(FacebookComment.objects.get(comment_id="c1").author_name, "Ava")
        self.assertFalse(hasattr(FacebookComment.objects.get(comment_id="c1"), "author_id"))
        daily = primary.snapshots.get(scope=FacebookSnapshot.Scope.INSIGHTS_DAILY, post_key="")
        self.assertEqual(daily.followers, 80)
        self.assertEqual(daily.followers_gained, 4)
        self.assertEqual(daily.views, 21)
        self.assertTrue(primary.snapshots.filter(post_key="demographics").exists())
        self.assertNotIn("access_token", [field.name for field in FacebookPage._meta.fields])
        self.assertFalse(any("source" in fields for fields in self.fake.fields))
        self.assertNotIn("page-token-a", primary.about)
        self.client.force_login(self.user)
        page = self.client.get(reverse("analytics:platform", args=["facebook"]))
        self.assertContains(page, "Facebook library")
        self.assertContains(page, "Page likes")
        self.assertContains(page, "Hidden")
        detail = self.client.get(shown.page.account.contents.get(external_id="111_2").get_absolute_url())
        self.assertContains(detail, "Facebook post")

    def test_missing_page_fails(self):
        job = self._run(FakeFacebook("user-token", empty=True))
        self.assertEqual(job.status, FacebookSyncJob.Status.FAILED)
        self.assertIn("No Facebook Page", job.last_error)

    def test_insights_permission_keeps_followers(self):
        fake = FakeFacebook(
            "user-token",
            insights_error=FacebookAPIError("permission denied", kind="permission", code=10),
        )
        job = self._run(fake)
        page = FacebookPage.objects.get(page_id="111")
        self.assertEqual(page.followers_count, 80)
        self.assertIs(page.insights_authorized, False)
        self.assertFalse(page.reauthorization_required)
        self.assertEqual(job.status, FacebookSyncJob.Status.PARTIAL)

    def test_past_follower_total_is_not_replaced(self):
        page = FacebookPage.objects.create(account=self.account, page_id="111", followers_count=10)
        yesterday = timezone.localdate() - timedelta(days=1)
        save_snapshot(
            page,
            snapshot_date=yesterday,
            scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE,
            source="facebook_page",
            values={"followers": 10},
        )
        save_snapshot(
            page,
            snapshot_date=yesterday,
            scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE,
            source="facebook_page",
            values={"followers": 99},
        )
        frozen = page.snapshots.get(snapshot_date=yesterday, scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE)
        self.assertEqual(frozen.followers, 10)
        self._run(FakeFacebook("user-token"))
        frozen.refresh_from_db()
        self.assertEqual(frozen.followers, 10)
        today = page.snapshots.get(
            snapshot_date=timezone.localdate(),
            scope=FacebookSnapshot.Scope.PUBLIC_CUMULATIVE,
            post_key="",
        )
        self.assertEqual(today.followers, 80)
        delta = page.snapshots.get(
            snapshot_date=timezone.localdate(),
            scope=FacebookSnapshot.Scope.CALCULATED_DELTA,
            post_key="",
        )
        self.assertEqual(delta.followers, 70)

    def test_open_job_is_reused(self):
        from networks.facebook.jobs import enqueue_facebook_sync

        first = enqueue_facebook_sync(self.account, mode="incremental")
        second = enqueue_facebook_sync(self.account, mode="full")
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.mode, FacebookSyncJob.Mode.FULL)
