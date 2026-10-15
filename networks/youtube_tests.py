from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from accounts.models import StudioProfile
from creators.models import CreatorProfile
from networks.models import SocialAccount
from networks.oauth import ensure_access_token
from networks.tokenbox import seal
from networks.youtube.aggregates import recalculate_channel
from networks.youtube.client import YouTubeAPIError, YouTubeClient
from networks.youtube.parse import parse_iso8601_duration
from networks.youtube.snapshots import refresh_calculated_delta, save_snapshot
from networks.youtube.sync import sync_youtube
from networks.youtube_models import (
    YouTubeChannel,
    YouTubeSnapshot,
    YouTubeSyncJob,
    YouTubeTranscript,
    YouTubeVideo,
    YouTubeVideoComment,
)

User = get_user_model()
SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    ]
)


def make_creator(email, username):
    user = User.objects.create_user(username=username, email=email, password="Northline-Studio-2026!")
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    StudioProfile.objects.update_or_create(user=user, defaults={"role": StudioProfile.Role.CREATOR})
    CreatorProfile.objects.get_or_create(user=user, defaults={"display_name": username})
    return user


def make_account(user, token="secret-token-value"):
    return SocialAccount.objects.create(
        user=user,
        platform="youtube",
        access_token=token,
        refresh_token="refresh-secret",
        token_expires_at=timezone.now() + timedelta(hours=1),
    )


class FakeYouTube:
    def __init__(self, *, comment_error=None, quota_on_comments=False, video_count=3):
        self.calls = []
        self.comment_error = comment_error
        self.quota_on_comments = quota_on_comments
        self.video_count = video_count

    def json(self, method, url, headers=None, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if "secret-token-value" in str(params) or "refresh-secret" in str(params):
            raise AssertionError("token leaked into query params")
        if url.endswith("/channels"):
            return self._channel()
        if "playlistItems" in url:
            return self._playlist(params.get("pageToken") or "")
        if url.endswith("/videos"):
            return {"items": [self._video(video_id) for video_id in params.get("id", "").split(",") if video_id]}
        if "commentThreads" in url:
            return self._comments(params)
        if url.endswith("/captions"):
            if params.get("videoId") == "v1":
                return {
                    "items": [
                        {
                            "id": "cap1",
                            "snippet": {"language": "en", "name": "English", "trackKind": "standard"},
                        }
                    ]
                }
            return {"items": []}
        if "youtubeanalytics.googleapis.com" in url:
            return self._analytics(params)
        raise AssertionError(url)

    def text(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "/captions/" not in url:
            raise AssertionError(url)
        return "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello from the caption\n"

    def _channel(self):
        return {
            "items": [
                {
                    "id": "UC123",
                    "snippet": {
                        "title": "Northline",
                        "description": "A channel",
                        "customUrl": "@northline",
                        "publishedAt": "2020-01-02T00:00:00Z",
                        "country": "IN",
                        "thumbnails": {"medium": {"url": "https://img.example/channel.jpg"}},
                    },
                    "statistics": {
                        "viewCount": "9999",
                        "subscriberCount": "15",
                        "videoCount": str(self.video_count),
                        "hiddenSubscriberCount": False,
                    },
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
                    "status": {"privacyStatus": "public"},
                }
            ]
        }

    def _playlist(self, page_token):
        ids = [f"v{index}" for index in range(1, self.video_count + 1)]
        if self.video_count <= 50:
            pages = [ids[:2], ids[2:]] if self.video_count > 2 else [ids]
            if page_token == "page-2":
                chunk = pages[1] if len(pages) > 1 else []
                return {"items": [self._playlist_item(video_id) for video_id in chunk]}
            next_token = "page-2" if len(pages) > 1 and pages[1] else ""
            payload = {"items": [self._playlist_item(video_id) for video_id in pages[0]]}
            if next_token:
                payload["nextPageToken"] = next_token
            return payload
        payload = {"items": [self._playlist_item(video_id) for video_id in ids]}
        return payload

    def _playlist_item(self, video_id):
        return {"contentDetails": {"videoId": video_id}, "snippet": {"title": video_id}}

    def _video(self, video_id):
        now = timezone.now()
        published = {
            "v1": now,
            "v2": now - timedelta(days=10),
            "v3": now - timedelta(days=400),
        }.get(video_id, now)
        privacy = {"v1": "public", "v2": "private", "v3": "unlisted"}.get(video_id, "public")
        duration = {"v1": "PT1M30S", "v2": "PT1H"}.get(video_id, "")
        views = {"v1": "10", "v2": "50", "v3": "20"}.get(video_id, "1")
        stats = {"viewCount": views, "commentCount": "2", "favoriteCount": "0", "fooCount": "3"}
        if video_id != "v2":
            stats["likeCount"] = "4"
        return {
            "id": video_id,
            "snippet": {
                "title": f"Video {video_id}",
                "description": "desc",
                "publishedAt": published.isoformat(),
                "thumbnails": {"medium": {"url": f"https://img.example/{video_id}.jpg"}},
                "categoryId": "22",
                "defaultLanguage": "en",
                "tags": ["studio"],
                "liveBroadcastContent": "none",
                "channelId": "UC123",
            },
            "contentDetails": {"duration": duration},
            "statistics": stats,
            "status": {"privacyStatus": privacy, "embeddable": True, "madeForKids": False},
        }

    def _comments(self, params):
        if self.quota_on_comments:
            raise YouTubeAPIError("quota exceeded", kind="quota", status_code=403, reason="quotaExceeded")
        if self.comment_error and params.get("videoId") == "v2":
            raise self.comment_error
        if params.get("pageToken") == "page-2":
            return {"items": [self._thread("c2", params["videoId"])]}
        return {
            "items": [self._thread("c1", params["videoId"], reply=True)],
            "nextPageToken": "page-2",
        }

    def _thread(self, comment_id, video_id, reply=False):
        top = {
            "id": f"{video_id}-{comment_id}",
            "snippet": {
                "authorDisplayName": "Ada",
                "authorChannelId": {"value": "UCada"},
                "textOriginal": f"Hello {comment_id}",
                "publishedAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-02T00:00:00Z",
                "likeCount": 2,
            },
        }
        snippet = {"totalReplyCount": 1 if reply else 0, "topLevelComment": top}
        payload = {"snippet": snippet}
        if reply:
            payload["replies"] = {
                "comments": [
                    {
                        "id": f"{video_id}-{comment_id}-r",
                        "snippet": {
                            "parentId": top["id"],
                            "authorDisplayName": "Bea",
                            "authorChannelId": {"value": "UCbea"},
                            "textOriginal": "Reply",
                            "publishedAt": "2024-01-03T00:00:00Z",
                            "likeCount": 0,
                        },
                    }
                ]
            }
        return payload

    def _analytics(self, params):
        metrics = params.get("metrics") or ""
        dimensions = params.get("dimensions") or ""
        if "engagedViews" in metrics:
            raise YouTubeAPIError("metric is not supported", kind="unsupported", status_code=400)
        if "estimatedRevenue" in metrics:
            raise YouTubeAPIError("revenue is not authorized", kind="permission", status_code=403, reason="forbidden")
        if dimensions == "day":
            names = ["day", *metrics.split(",")]
            day = (timezone.localdate() - timedelta(days=1)).isoformat()
            row = [day, *([1] * (len(names) - 1))]
            return {"columnHeaders": [{"name": name} for name in names], "rows": [row]}
        if dimensions == "video":
            return {"columnHeaders": [{"name": "video"}, {"name": "views"}], "rows": [["v1", 5]]}
        if dimensions == "elapsedVideoTimeRatio":
            return {
                "columnHeaders": [
                    {"name": "elapsedVideoTimeRatio"},
                    {"name": "audienceWatchRatio"},
                    {"name": "relativeRetentionPerformance"},
                ],
                "rows": [[0.5, 0.25, 1]],
            }
        if dimensions == "gender" and "viewerPercentage" in metrics:
            return {
                "columnHeaders": [{"name": "gender"}, {"name": "viewerPercentage"}],
                "rows": [["female", 62], ["male", 35], ["user_specified", 3]],
            }
        if dimensions == "ageGroup" and "viewerPercentage" in metrics:
            return {
                "columnHeaders": [{"name": "ageGroup"}, {"name": "viewerPercentage"}],
                "rows": [["age18-24", 40], ["age25-34", 60]],
            }
        if dimensions == "ageGroup,gender":
            return {
                "columnHeaders": [{"name": "ageGroup"}, {"name": "gender"}, {"name": "viewerPercentage"}],
                "rows": [["age18-24", "female", 25], ["age25-34", "male", 20]],
            }
        if dimensions == "country" and "estimatedMinutesWatched" in metrics:
            return {
                "columnHeaders": [{"name": "country"}, {"name": "estimatedMinutesWatched"}],
                "rows": [["IN", 120], ["US", 40]],
            }
        if dimensions == "country":
            return {"columnHeaders": [{"name": "country"}, {"name": "views"}], "rows": [["IN", 80], ["US", 20]]}
        column = dimensions or "segment"
        return {"columnHeaders": [{"name": column}, {"name": "views"}], "rows": [["US", 4]]}


@override_settings(
    YOUTUBE_MAX_VIDEOS=100,
    YOUTUBE_INCREMENTAL_VIDEOS=1,
    YOUTUBE_COMMENT_LIMIT=20,
    YOUTUBE_COMMENT_VIDEO_LIMIT=5,
    YOUTUBE_FULL_COMMENT_VIDEO_LIMIT=5,
    YOUTUBE_TRANSCRIPT_VIDEO_LIMIT=5,
    YOUTUBE_FULL_TRANSCRIPT_VIDEO_LIMIT=5,
    YOUTUBE_ANALYTICS_DAYS=28,
    YOUTUBE_VIDEO_ANALYTICS_LIMIT=5,
    YOUTUBE_RETENTION_VIDEO_LIMIT=5,
    YOUTUBE_SYNC_EAGER=False,
)
class YouTubeSyncTests(TestCase):
    def setUp(self):
        self.user = make_creator("yt@example.com", "yter")
        self.account = make_account(self.user)
        self.fake = FakeYouTube()

    def _run(self, mode="full", fake=None):
        fake = fake or self.fake
        job = YouTubeSyncJob.objects.create(account=self.account, mode=mode, granted_scopes=SCOPES)
        with (
            patch("networks.youtube.client.request_json", side_effect=fake.json),
            patch("networks.youtube.client.request_text", side_effect=fake.text),
        ):
            sync_youtube(self.account, job)
        job.refresh_from_db()
        return job

    def test_full_sync_is_idempotent_and_keeps_unavailable_stats_null(self):
        job = self._run("full")
        self.assertIn(job.status, {"completed", "partial"})
        self.assertEqual(YouTubeChannel.objects.get(channel_id="UC123").view_count, 9999)
        self.assertEqual(YouTubeVideo.objects.count(), 3)
        private = YouTubeVideo.objects.get(video_id="v2")
        self.assertIsNone(private.like_count)
        self.assertFalse(private.statistics_available["likeCount"])
        self.assertEqual(private.statistics["fooCount"], "3")
        channel = YouTubeChannel.objects.get(channel_id="UC123")
        self.assertEqual(channel.summed_video_views, 80)
        self.assertNotEqual(channel.summed_video_views, channel.view_count)
        self.assertEqual(channel.total_duration_seconds, 90 + 3600)
        self.assertEqual(channel.public_video_count, 1)
        self.assertEqual(channel.private_video_count, 1)
        self.assertEqual(channel.unlisted_video_count, 1)
        self.assertIsNone(channel.deleted_video_count)
        self.assertEqual(channel.most_viewed_video_id, "v2")
        self.assertEqual(channel.latest_video_id, "v1")
        self.assertEqual(channel.videos_last_7_days, 1)
        self.assertEqual(YouTubeVideoComment.objects.count(), 9)
        transcript = YouTubeTranscript.objects.get(video__video_id="v1", language="en")
        self.assertEqual(transcript.status, YouTubeTranscript.Status.AVAILABLE)
        self.assertIn("Hello from the caption", transcript.text)
        self.assertTrue(transcript.segments)
        self.assertEqual(YouTubeTranscript.objects.get(video__video_id="v2").status, YouTubeTranscript.Status.UNAVAILABLE)
        self.assertTrue(channel.snapshots.filter(scope="public_cumulative", video_key="").exists())
        self.assertTrue(channel.snapshots.filter(scope="analytics_daily", video_key="").exists())
        self.assertIsNone(channel.snapshots.filter(scope="analytics_daily", video_key="").first().estimated_revenue)
        self.assertFalse(channel.revenue_authorized)
        self.assertTrue(channel.analytics_authorized)
        report = channel.snapshots.filter(scope="analytics_range", video_key="").first()
        self.assertEqual(report.breakdowns.get(dimension="gender", segment="female").value, 62)
        self.assertEqual(report.breakdowns.get(dimension="age", segment="age18-24").value, 40)
        self.assertEqual(report.breakdowns.get(dimension="age_gender", segment="age18-24|female").value, 25)
        self.assertEqual(report.breakdowns.get(dimension="country", segment="IN").value, 80)
        self.assertEqual(report.breakdowns.get(dimension="country_watch_time", segment="IN").value, 120)
        from networks.models import AudienceBreakdown

        female = AudienceBreakdown.objects.get(snapshot__account=self.account, dimension="gender", segment="female")
        self.assertEqual(female.count, 9)
        self.assertEqual(female.basis, "estimated")
        self.assertFalse(any("videoplayback" in url or url.rstrip("/").endswith("/download") for url, _params in self.fake.calls))
        playlist_calls = [params for url, params in self.fake.calls if "playlistItems" in url]
        self.assertEqual(len(playlist_calls), 2)
        self.assertEqual(playlist_calls[1].get("pageToken"), "page-2")

        past = channel.snapshots.filter(scope="analytics_daily", video_key="").order_by("snapshot_date").first()
        original = past.views
        save_snapshot(
            channel,
            snapshot_date=past.snapshot_date,
            scope="analytics_daily",
            source="youtube_analytics_api",
            views=(original or 0) + 100,
        )
        past.refresh_from_db()
        self.assertEqual(past.views, original)

        again = self._run("full")
        self.assertIn(again.status, {"completed", "partial"})
        self.assertEqual(YouTubeVideo.objects.count(), 3)
        self.assertEqual(YouTubeVideoComment.objects.count(), 9)
        self.assertEqual(YouTubeTranscript.objects.filter(video__video_id="v1").count(), 1)

    def test_comment_failure_does_not_wipe_videos_or_look_like_an_empty_list(self):
        job = self._run("full")
        self.assertGreater(job.videos_upserted, 0)
        comment = YouTubeVideoComment.objects.filter(video__video_id="v2").first()
        self.assertIsNotNone(comment)
        failing = FakeYouTube(comment_error=YouTubeAPIError("backend failed", kind="failed", status_code=500))
        failed = self._run("incremental", fake=failing)
        self.assertEqual(failed.status, "partial")
        video = YouTubeVideo.objects.get(video_id="v2")
        self.assertEqual(video.comments_sync_status, "failed")
        self.assertNotEqual(video.comments_sync_status, "empty")
        self.assertTrue(YouTubeVideoComment.objects.filter(pk=comment.pk).exists())
        self.assertEqual(YouTubeVideo.objects.count(), 3)

    def test_disabled_comments_are_not_stored_as_an_empty_successful_list(self):
        self._run("full")
        disabled = FakeYouTube(
            comment_error=YouTubeAPIError(
                "comments disabled",
                kind="unavailable",
                status_code=403,
                reason="commentsDisabled",
            )
        )
        job = self._run("incremental", fake=disabled)
        self.assertIn(job.status, {"completed", "partial"})
        video = YouTubeVideo.objects.get(video_id="v2")
        self.assertEqual(video.comments_sync_status, "disabled")
        self.assertGreater(video.comment_rows.count(), 0)

    def test_quota_stops_the_job_and_keeps_saved_videos(self):
        failing = FakeYouTube(quota_on_comments=True)
        job = self._run("full", fake=failing)
        self.assertEqual(job.outcome, "quota_exhausted")
        self.assertGreater(YouTubeVideo.objects.count(), 0)
        self.assertEqual(YouTubeVideoComment.objects.count(), 0)

    def test_incremental_sync_does_not_walk_the_whole_playlist_or_mark_videos_removed(self):
        self._run("full")
        channel = YouTubeChannel.objects.get()
        YouTubeVideo.objects.create(channel=channel, video_id="gone", title="Gone")
        job = self._run("incremental")
        self.assertEqual(job.videos_upserted, 1)
        self.assertEqual(YouTubeVideo.objects.get(video_id="gone").availability, YouTubeVideo.Availability.AVAILABLE)
        self._run("full")
        self.assertEqual(YouTubeVideo.objects.get(video_id="gone").availability, YouTubeVideo.Availability.REMOVED)

    @override_settings(YOUTUBE_MAX_VIDEOS=100, YOUTUBE_FULL_COMMENT_VIDEO_LIMIT=0, YOUTUBE_FULL_TRANSCRIPT_VIDEO_LIMIT=0, YOUTUBE_VIDEO_ANALYTICS_LIMIT=0, YOUTUBE_RETENTION_VIDEO_LIMIT=0)
    def test_video_details_are_requested_in_batches(self):
        fake = FakeYouTube(video_count=51)
        self._run("full", fake=fake)
        batches = [params.get("id", "") for url, params in fake.calls if url.endswith("/videos")]
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0].split(",")), 50)
        self.assertEqual(len(batches[1].split(",")), 1)
        self.assertEqual(YouTubeVideo.objects.count(), 51)

    def test_token_refresh_seals_tokens_and_permission_failure_asks_for_reauthorization(self):
        self.account.access_token = "old-access"
        self.account.refresh_token = "refresh-secret"
        self.account.token_expires_at = timezone.now() - timedelta(minutes=10)
        self.account.save()
        with patch(
            "networks.oauth.api_post",
            return_value={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        ) as posted:
            token = ensure_access_token(self.account)
        self.assertEqual(token, "new-access")
        self.account.refresh_from_db()
        self.assertTrue(self.account.access_token.startswith("enc:v1:"))
        self.assertNotIn("new-access", self.account.access_token)
        body = posted.call_args.kwargs["data"]
        self.assertEqual(body["refresh_token"], "refresh-secret")
        self.assertNotIn("new-access", str(body))

        self.account.access_token = seal("stored-access")
        self.account.token_expires_at = timezone.now() + timedelta(hours=1)
        self.account.save(update_fields=["access_token", "token_expires_at"])
        with patch("networks.oauth.api_post") as refresh:
            self.assertEqual(ensure_access_token(self.account), "stored-access")
        refresh.assert_not_called()

        YouTubeChannel.objects.create(account=self.account, channel_id="UCreauth", title="Reauth")
        job = YouTubeSyncJob.objects.create(account=self.account, mode="full", granted_scopes=SCOPES)
        from networks.http import ProviderError
        from networks.youtube.jobs import process_youtube_job

        with patch("networks.youtube.sync.ensure_access_token", side_effect=ProviderError("invalid_grant")):
            process_youtube_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.outcome, "permission_denied")
        self.assertTrue(YouTubeChannel.objects.get(channel_id="UCreauth").reauthorization_required)


class YouTubeClientTests(TestCase):
    def test_duration_parser_and_snapshot_delta(self):
        self.assertEqual(parse_iso8601_duration("PT1H2M3S"), 3723)
        self.assertEqual(parse_iso8601_duration("PT45S"), 45)
        self.assertEqual(parse_iso8601_duration("P1DT1H"), 90000)
        self.assertIsNone(parse_iso8601_duration(""))
        user = make_creator("delta@example.com", "delta")
        account = make_account(user)
        channel = YouTubeChannel.objects.create(account=account, channel_id="UCdelta", title="Delta")
        yesterday = timezone.localdate() - timedelta(days=1)
        today = timezone.localdate()
        save_snapshot(channel, snapshot_date=yesterday, scope="public_cumulative", source="youtube_data_api", views=100, subscribers=10)
        save_snapshot(channel, snapshot_date=today, scope="public_cumulative", source="youtube_data_api", views=140, subscribers=12)
        delta = refresh_calculated_delta(channel, "", today)
        self.assertEqual(delta.views, 40)
        self.assertEqual(delta.subscribers, 2)
        self.assertEqual(delta.source, "calculated_snapshot_delta")
        save_snapshot(channel, snapshot_date=today, scope="public_cumulative", source="youtube_data_api", views=90, subscribers=12)
        reset = refresh_calculated_delta(channel, "", today)
        self.assertIsNone(reset.views)
        self.assertTrue(reset.counter_reset)
        self.assertEqual(YouTubeSnapshot.objects.filter(channel=channel, snapshot_date=today, scope="public_cumulative").count(), 1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                YouTubeSnapshot.objects.create(
                    channel=channel,
                    video_key="",
                    snapshot_date=today,
                    scope="public_cumulative",
                    source="youtube_data_api",
                    collected_at=timezone.now(),
                )
        YouTubeVideo.objects.create(channel=channel, video_id="a", title="A", duration_seconds=30, view_count=10, privacy_status="public", published_at=timezone.now())
        YouTubeVideo.objects.create(channel=channel, video_id="b", title="B", duration_seconds=None, view_count=None, privacy_status=None, published_at=timezone.now() - timedelta(days=3))
        recalculate_channel(channel)
        channel.refresh_from_db()
        self.assertEqual(channel.total_duration_seconds, 30)
        self.assertEqual(channel.average_views, 10)
        self.assertIsNone(channel.deleted_video_count)
        self.assertFalse(channel.privacy_counts_complete)

    def test_retries_transient_errors_and_stops_on_quota(self):
        client = YouTubeClient("token")
        attempts = {"count": 0}

        def flaky(method, url, headers=None, params=None, timeout=None):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise YouTubeAPIError("slow down", kind="retryable", status_code=429, reason="rateLimitExceeded")
            return {"items": []}

        with patch("networks.youtube.client.request_json", side_effect=flaky), patch("networks.youtube.client.time.sleep") as slept:
            payload = client.get_json("https://www.googleapis.com/youtube/v3/videos", {}, cost=1)
        self.assertEqual(payload, {"items": []})
        self.assertEqual(attempts["count"], 2)
        slept.assert_called()

        def quota(method, url, headers=None, params=None, timeout=None):
            raise YouTubeAPIError("quota", kind="quota", status_code=403, reason="quotaExceeded")

        with patch("networks.youtube.client.request_json", side_effect=quota), patch("networks.youtube.client.time.sleep") as slept:
            with self.assertRaises(YouTubeAPIError) as raised:
                client.get_json("https://www.googleapis.com/youtube/v3/videos", {}, cost=1)
        self.assertEqual(raised.exception.kind, "quota")
        slept.assert_not_called()

    def test_disabled_analytics_api_is_not_a_missing_scope(self):
        from networks.youtube.client import classify_error

        error = classify_error(
            403,
            {"error": {"message": "Access Not Configured.", "errors": [{"reason": "accessNotConfigured"}]}},
        )
        self.assertEqual(error.kind, "not_configured")
        self.assertEqual(error.reason, "accessNotConfigured")


@override_settings(YOUTUBE_SYNC_EAGER=False)
class YouTubeApiTests(TestCase):
    def setUp(self):
        self.user = make_creator("api@example.com", "apiuser")
        self.other = make_creator("other@example.com", "other")
        self.account = make_account(self.user)
        self.channel = YouTubeChannel.objects.create(
            account=self.account,
            channel_id="UCapi",
            title="API channel",
            view_count=20,
            subscriber_count=5,
        )
        self.video = YouTubeVideo.objects.create(
            channel=self.channel,
            video_id="vid1",
            title="One",
            view_count=20,
            like_count=None,
            statistics_available={"viewCount": True, "likeCount": False},
            published_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def test_youtube_library_renders_on_the_studio_pages(self):
        from networks.models import ContentItem

        ContentItem.objects.create(
            account=self.account,
            external_id="vid1",
            title="One",
            views=20,
        )
        YouTubeVideoComment.objects.create(
            video=self.video,
            comment_id="c-page",
            author_display_name="Ada",
            text="Shown on the video page",
            is_reply=False,
            synced_at=timezone.now(),
        )
        YouTubeTranscript.objects.create(
            video=self.video,
            language="en",
            track_id="cap",
            source="youtube_captions",
            status=YouTubeTranscript.Status.AVAILABLE,
            text="Hello from the page",
        )
        self.channel.view_count = 999
        self.channel.summed_video_views = 20
        self.channel.sync_status = "succeeded"
        self.channel.save()
        from networks.youtube.snapshots import replace_breakdowns, save_snapshot

        report, _created = save_snapshot(
            self.channel,
            snapshot_date=timezone.localdate(),
            scope="analytics_range",
            source="youtube_analytics_api",
            period_start=timezone.localdate(),
        )
        replace_breakdowns(
            report,
            [
                {"dimension": "gender", "segment": "female", "value": 62},
                {"dimension": "age", "segment": "age18-24", "value": 40},
                {"dimension": "age_gender", "segment": "age18-24|female", "value": 25},
                {"dimension": "country", "segment": "IN", "value": 80},
                {"dimension": "country_watch_time", "segment": "IN", "value": 120},
            ],
        )
        page = self.client.get(reverse("analytics:platform", args=["youtube"]))
        self.assertContains(page, "YouTube library")
        self.assertContains(page, "Female")
        self.assertContains(page, "18–24")
        self.assertContains(page, "India")
        self.assertContains(page, "18–24 · Female")
        self.assertContains(page, "Official channel views")
        self.assertContains(page, "999")
        self.assertContains(page, "Catalog views")
        self.assertContains(page, "Full sync")
        self.assertContains(page, "Unavailable")
        detail = self.client.get(reverse("analytics:content", args=[self.account.contents.get(external_id="vid1").pk]))
        self.assertContains(detail, "Shown on the video page")
        self.assertContains(detail, "Hello from the page")
        self.assertContains(detail, "Duration")
        connections = self.client.get(reverse("networks:connections"))
        self.assertContains(connections, "API channel")

    def test_owner_can_read_and_other_users_cannot(self):
        listing = self.client.get(reverse("networks:youtube_channels"))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 1)
        detail = self.client.get(reverse("networks:youtube_channel", args=["UCapi"]))
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.json()["aggregates"]["deleted_videos"])
        self.assertFalse(detail.json()["aggregates"]["deleted_videos_available"])
        videos = self.client.get(reverse("networks:youtube_videos", args=["UCapi"]))
        self.assertEqual(videos.json()["results"][0]["statistics"]["like_count"], None)
        self.assertEqual(self.client.get(reverse("networks:youtube_video", args=["vid1"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("networks:youtube_comments", args=["vid1"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("networks:youtube_transcript", args=["vid1"])).json()["available"], False)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("networks:youtube_channel", args=["UCapi"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("networks:youtube_video", args=["vid1"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("networks:youtube_channels")).status_code, 401)

    def test_sync_post_queues_a_job_the_owner_can_poll(self):
        response = self.client.post(
            reverse("networks:youtube_sync", args=["UCapi"]),
            data={"mode": "full"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["mode"], "full")
        follow = self.client.get(reverse("networks:youtube_job", args=[job_id]))
        self.assertEqual(follow.status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("networks:youtube_job", args=[job_id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("networks:youtube_sync", args=["UCapi"])).status_code, 404)

    def test_stale_running_job_can_be_picked_up_again(self):
        job = YouTubeSyncJob.objects.create(
            account=self.account,
            mode="incremental",
            status=YouTubeSyncJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=2),
            attempts=1,
        )
        with patch("networks.youtube.jobs.process_youtube_job", side_effect=lambda item: item) as process:
            from networks.youtube.jobs import process_pending_youtube_jobs

            process_pending_youtube_jobs()
        process.assert_called()
        job.refresh_from_db()
        self.assertEqual(job.status, YouTubeSyncJob.Status.PENDING)
