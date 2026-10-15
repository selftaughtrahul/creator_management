from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from accounts.models import StudioProfile
from creators.models import CreatorProfile
from networks.instagram.client import InstagramAPIError, InstagramClient, classify_error
from networks.instagram.snapshots import save_snapshot
from networks.instagram.sync import sync_instagram
from networks.instagram_models import (
    InstagramComment,
    InstagramMedia,
    InstagramProfile,
    InstagramSnapshot,
    InstagramSyncJob,
)
from networks.models import ContentItem, SocialAccount

User = get_user_model()
SCOPES = "instagram_business_basic,instagram_business_manage_insights,instagram_business_manage_comments"


def make_creator(email, username):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    StudioProfile.objects.update_or_create(user=user, defaults={"role": StudioProfile.Role.CREATOR})
    CreatorProfile.objects.get_or_create(user=user, defaults={"display_name": username})
    return user


def make_account(user):
    return SocialAccount.objects.create(
        user=user,
        platform="instagram",
        access_token="secret-token-value",
        token_expires_at=timezone.now() + timedelta(days=30),
    )


class FakeInstagram:
    def __init__(self, token, *, insights_error=None, personal=False, hide_likes=False):
        self.token = token
        self.calls = 0
        self.paths = []
        self.field_sets = []
        self.insights_error = insights_error
        self.personal = personal
        self.hide_likes = hide_likes
        if "secret-token-value" == token:
            pass

    def get(self, path, params=None):
        self.calls += 1
        self.paths.append(path)
        params = params or {}
        self.field_sets.append(params.get("fields") or params.get("metric") or "")
        if "access_token" in params or "secret-token-value" in str(params):
            raise AssertionError("token leaked into logged params")
        if path == "me":
            payload = {
                "user_id": "1789001",
                "username": "northline",
                "name": "Northline",
                "biography": "Studio notes",
                "account_type": "PERSONAL" if self.personal else "BUSINESS",
                "profile_picture_url": "https://cdn.example/pic.jpg",
                "followers_count": 120,
                "follows_count": 10,
                "media_count": 2,
            }
            return payload
        if path == "1789001/insights" and params.get("metric") == "follower_demographics":
            return {
                "data": [
                    {
                        "name": "follower_demographics",
                        "total_value": {
                            "breakdowns": [
                                {
                                    "dimension_keys": [params.get("breakdown")],
                                    "results": [
                                        {
                                            "dimension_values": [
                                                "IN" if params.get("breakdown") == "country" else "F"
                                            ],
                                            "value": 80,
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ]
            }
        raise AssertionError(path)

    def paged(self, path, params, limit):
        self.calls += 1
        self.paths.append(path)
        self.field_sets.append(params.get("fields") or "")
        if "media_url" in (params.get("fields") or ""):
            raise AssertionError("media file URL was requested")
        if path.endswith("/media"):
            hidden = {"id": "media-2", "caption": "Second post", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/second/"}
            shown = {
                "id": "media-1",
                "caption": "First reel",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "permalink": "https://www.instagram.com/reel/first/",
                "thumbnail_url": "https://cdn.example/thumb.jpg",
                "timestamp": "2026-10-01T08:00:00+0000",
                "like_count": 4,
                "comments_count": 1,
                "is_comment_enabled": True,
            }
            if not self.hide_likes:
                hidden["like_count"] = 2
                hidden["comments_count"] = 0
            return [shown, hidden][:limit]
        if path.endswith("/comments"):
            if not path.startswith("media-1/"):
                return []
            return [
                {
                    "id": "comment-1",
                    "text": "Nice reel",
                    "username": "viewer",
                    "timestamp": "2026-10-02T08:00:00+0000",
                    "like_count": 1,
                    "replies": {"data": [{"id": "reply-1", "text": "Thanks", "username": "northline", "timestamp": "2026-10-02T09:00:00+0000"}]},
                }
            ]
        raise AssertionError(path)

    def insights(self, path, metrics, params):
        self.calls += 1
        self.paths.append(path + ":insights")
        if self.insights_error:
            raise self.insights_error
        if path == "1789001/insights" and "metric_type" not in params:
            end = "2026-10-14T18:30:00+0000"
            return {
                "data": [
                    {"name": "reach", "values": [{"value": 30, "end_time": end}]},
                    {"name": "follower_count", "values": [{"value": 3, "end_time": end}]},
                ],
                "unsupported": [],
            }
        if path == "1789001/insights":
            data = []
            unsupported = []
            for name in metrics:
                if name == "replies":
                    unsupported.append(name)
                    continue
                data.append({"name": name, "total_value": {"value": 11 if name == "views" else 4}})
            return {"data": data, "unsupported": unsupported}
        values = []
        unsupported = []
        for name in metrics:
            if name == "ig_reels_avg_watch_time":
                values.append({"name": name, "total_value": {"value": 1500}})
                continue
            if name == "ig_reels_video_view_total_time":
                unsupported.append(name)
                continue
            values.append({"name": name, "total_value": {"value": 9 if name == "views" else 2}})
        return {"data": values, "unsupported": unsupported}


class InstagramClientTests(TestCase):
    def test_error_kinds(self):
        self.assertEqual(classify_error(400, {"error": {"code": 100, "message": "metric is not available"}})[0], "unsupported")
        self.assertEqual(classify_error(400, {"error": {"code": 10, "message": "permission denied"}})[0], "permission")
        self.assertEqual(classify_error(400, {"error": {"code": 4, "message": "rate limit"}})[0], "retryable")
        self.assertEqual(classify_error(400, {"error": {"code": 190, "message": "access token expired"}})[0], "token")

    def test_unsupported_metric_is_split_out(self):
        client = InstagramClient("token")

        def fake_get(path, params=None):
            metric = (params or {}).get("metric", "")
            if "views" in metric and "," in metric:
                raise InstagramAPIError("metric views is not available", kind="unsupported", code=100)
            if metric == "views":
                raise InstagramAPIError("metric views is not available", kind="unsupported", code=100)
            return {"data": [{"name": metric, "total_value": {"value": 1}}]}

        with patch.object(client, "get", side_effect=fake_get):
            payload = client.insights("178/insights", ["reach", "views"], {"period": "day"})
        self.assertEqual(payload["unsupported"], ["views"])
        self.assertEqual(payload["data"][0]["name"], "reach")


class InstagramSyncTests(TestCase):
    def setUp(self):
        self.user = make_creator("ig@example.com", "igname")
        self.account = make_account(self.user)

    def _run(self, fake):
        job = InstagramSyncJob.objects.create(account=self.account, mode="full", granted_scopes=SCOPES)
        with (
            patch("networks.instagram.sync.ensure_access_token", return_value="secret-token-value"),
            patch("networks.instagram.sync.InstagramClient", return_value=fake),
        ):
            sync_instagram(self.account, job)
        job.refresh_from_db()
        return job

    def test_full_sync_stores_profile_media_comments_and_insights(self):
        fake = FakeInstagram("secret-token-value", hide_likes=True)
        job = self._run(fake)
        self.assertEqual(job.status, InstagramSyncJob.Status.COMPLETED)
        self.assertIn("replies", job.stages["insights"]["unsupported"])
        profile = InstagramProfile.objects.get(account=self.account)
        self.assertEqual(profile.followers_count, 120)
        self.assertEqual(profile.username, "northline")
        hidden = InstagramMedia.objects.get(media_id="media-2")
        self.assertIsNone(hidden.like_count)
        reel = InstagramMedia.objects.get(media_id="media-1")
        self.assertEqual(reel.views, 9)
        self.assertEqual(reel.avg_watch_time_ms, 1500)
        self.assertIsNone(reel.total_watch_time_ms)
        self.assertEqual(InstagramComment.objects.filter(media=reel).count(), 2)
        daily = profile.snapshots.get(scope=InstagramSnapshot.Scope.INSIGHTS_DAILY, media_key="")
        self.assertEqual(daily.followers_gained, 3)
        self.assertIsNone(daily.followers)
        window = profile.snapshots.get(scope=InstagramSnapshot.Scope.INSIGHTS_RANGE, media_key="")
        self.assertEqual(window.views, 11)
        self.assertIsNone(window.replies)
        self.assertTrue(profile.snapshots.filter(media_key="demographics").exists())
        content = ContentItem.objects.get(account=self.account, external_id="media-2")
        self.assertNotIn("reach", content.__dict__)
        self.assertFalse(any("media_url" in fields for fields in fake.field_sets))
        self.client.force_login(self.user)
        page = self.client.get(reverse("analytics:platform", args=["instagram"]))
        self.assertContains(page, "Instagram library")
        self.assertContains(page, "Hidden")
        self.assertContains(page, "120")
        detail = self.client.get(content.get_absolute_url())
        self.assertContains(detail, "Instagram post")

    def test_insights_permission_keeps_lifetime_followers(self):
        fake = FakeInstagram(
            "secret-token-value",
            insights_error=InstagramAPIError("permission denied", kind="permission", code=10),
        )
        job = InstagramSyncJob.objects.create(
            account=self.account,
            mode="full",
            granted_scopes=SCOPES,
        )
        with (
            patch("networks.instagram.sync.ensure_access_token", return_value="secret-token-value"),
            patch("networks.instagram.sync.InstagramClient", return_value=fake),
        ):
            sync_instagram(self.account, job)
        profile = InstagramProfile.objects.get(account=self.account)
        self.assertEqual(profile.followers_count, 120)
        self.assertIs(profile.insights_authorized, False)
        self.assertFalse(profile.reauthorization_required)
        self.assertEqual(profile.snapshots.filter(scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE).count(), 1)

    def test_past_snapshot_is_not_replaced(self):
        profile = InstagramProfile.objects.create(account=self.account, ig_user_id="1789001", followers_count=10)
        yesterday = timezone.localdate() - timedelta(days=1)
        save_snapshot(
            profile,
            snapshot_date=yesterday,
            scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE,
            source="instagram_user",
            values={"followers": 10},
        )
        save_snapshot(
            profile,
            snapshot_date=yesterday,
            scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE,
            source="instagram_user",
            values={"followers": 99},
        )
        frozen = profile.snapshots.get(snapshot_date=yesterday, scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE)
        self.assertEqual(frozen.followers, 10)
        job = self._run(FakeInstagram("secret-token-value"))
        self.assertIn(job.status, {InstagramSyncJob.Status.COMPLETED, InstagramSyncJob.Status.PARTIAL})
        frozen.refresh_from_db()
        self.assertEqual(frozen.followers, 10)
        today = profile.snapshots.get(
            snapshot_date=timezone.localdate(),
            scope=InstagramSnapshot.Scope.PUBLIC_CUMULATIVE,
            media_key="",
        )
        self.assertEqual(today.followers, 120)
        delta = profile.snapshots.get(
            snapshot_date=timezone.localdate(),
            scope=InstagramSnapshot.Scope.CALCULATED_DELTA,
            media_key="",
        )
        self.assertEqual(delta.followers, 110)

    def test_personal_account_fails_without_media_calls(self):
        fake = FakeInstagram("secret-token-value", personal=True)
        job = self._run(fake)
        self.assertEqual(job.status, InstagramSyncJob.Status.FAILED)
        self.assertFalse(any(path.endswith("/media") for path in fake.paths))

    def test_open_job_is_reused(self):
        from networks.instagram.jobs import enqueue_instagram_sync

        first = enqueue_instagram_sync(self.account, mode="incremental")
        second = enqueue_instagram_sync(self.account, mode="full")
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.mode, InstagramSyncJob.Mode.FULL)
