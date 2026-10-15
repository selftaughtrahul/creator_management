from django.test import TestCase

from classify import IABTaxonomyError, classify, collect_chunks, import_taxonomy, parse_taxonomy_tsv
from creators.models import IABCategory, IABClassification
from creators.tests import add_snapshot, make_creator
from networks.models import SocialAccount
from networks.youtube_models import YouTubeChannel, YouTubeTranscript, YouTubeVideo


class IABTaxonomyTests(TestCase):
    def test_parser_rejects_another_version(self):
        text = "Relational ID System\t\t\tContent Taxonomy v2.2 Tiered Categories\t\t\t\t\nUnique ID\tParent\tName\tTier 1\tTier 2\tTier 3\tTier 4\t\n1\t\tAutomotive\tAutomotive\t\t\t\n"
        with self.assertRaises(IABTaxonomyError):
            parse_taxonomy_tsv(text)

    def test_import_keeps_official_ids(self):
        rows, mappings = import_taxonomy()
        self.assertGreater(rows, 700)
        self.assertGreaterEqual(mappings, 9)
        ai = IABCategory.objects.get(unique_id="597", taxonomy_version="3.1")
        self.assertEqual(ai.name, "Artificial Intelligence")
        self.assertEqual(ai.parent_id, "596")
        self.assertEqual(ai.tier_1, "Technology & Computing")
        self.assertTrue(IABCategory.objects.filter(unique_id="80DV8O", name="Communication").exists())

    def test_content_classification_uses_transcript_and_mapping(self):
        import_taxonomy()
        creator = make_creator(
            "agent@example.com",
            "agent",
            bio="Daily updates from my desk.",
            audience_notes="",
        )
        account = add_snapshot(creator, platform="youtube")
        account.contents.all().delete()
        channel = YouTubeChannel.objects.create(account=account, channel_id="chan-1", title="Agents")
        video = YouTubeVideo.objects.create(
            channel=channel,
            video_id="vid-1",
            title="Building AI Agents with LangGraph",
            description="A tutorial.",
        )
        YouTubeTranscript.objects.create(
            video=video,
            language="en",
            source="captions",
            status=YouTubeTranscript.Status.AVAILABLE,
            text="A tutorial explaining LLMs, tool calling, and multi-step agent workflows.",
        )
        profile_rows = classify(creator, mode="profile")
        content_rows = classify(creator, mode="content")
        retrieved_rows = classify(creator, mode="retrieved")
        self.assertEqual(profile_rows, [])
        self.assertEqual(content_rows[0].iab_category.unique_id, "597")
        self.assertEqual(content_rows[0].custom_niche, "Tech")
        self.assertEqual(content_rows[0].custom_sub_niche, "Artificial Intelligence")
        self.assertEqual(content_rows[0].custom_micro_niche, "AI Agent Development")
        self.assertEqual(content_rows[0].taxonomy_version, "3.1")
        self.assertEqual(retrieved_rows[0].iab_category.unique_id, "597")
        chunks = collect_chunks(creator, mode="content")
        self.assertTrue(any(chunk["source"] == "youtube_transcript" for chunk in chunks))

    def test_unknown_iab_id_is_dropped(self):
        import_taxonomy()
        creator = make_creator("drop@example.com", "drop", bio="I post lifestyle vlogs.", audience_notes="")

        def fake_complete(prompt):
            return {"labels": [{"iab_category_id": "not-official", "confidence": 0.99, "evidence": ["vlogs"], "explanation": "no"}]}

        rows = classify(creator, mode="profile", method="anthropic", complete=fake_complete)
        self.assertEqual(rows, [])
        self.assertFalse(IABClassification.objects.filter(creator=creator).exists())

    def test_instagram_caption_is_collected(self):
        import_taxonomy()
        creator = make_creator("ig@example.com", "igcreator", bio="", audience_notes="")
        account = SocialAccount.objects.create(
            user=creator.user,
            platform="instagram",
            display_name="ig",
            username="ig",
            external_id="ig-1",
        )
        from networks.instagram_models import InstagramMedia, InstagramProfile

        profile = InstagramProfile.objects.create(account=account, ig_user_id="ig-user")
        InstagramMedia.objects.create(profile=profile, media_id="media-1", caption="skincare serum routine")
        rows = classify(creator, mode="content")
        self.assertEqual(rows[0].iab_category.unique_id, "553")
        self.assertEqual(rows[0].custom_niche, "Beauty")
