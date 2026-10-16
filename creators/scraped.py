"""Claim a sheet-imported creator when they join or connect a platform."""

import logging

from django.contrib.auth import get_user_model

from accounts.models import StudioProfile
from networks.models import SocialAccount

logger = logging.getLogger("creators.scraped")

User = get_user_model()
PLACEHOLDER_DOMAIN = "scraped.awisee.local"


def claimable_user(email):
    """A creator already in the database who has not set a password yet."""
    user = User.objects.filter(email__iexact=email).select_related("studio", "creator_profile").first()
    if user is None or user.has_usable_password():
        return None
    studio = getattr(user, "studio", None)
    if studio is None or studio.role != StudioProfile.Role.CREATOR:
        return None
    if not hasattr(user, "creator_profile"):
        return None
    if is_placeholder_email(user.email):
        return None
    return user


def claim_imported_user(user, *, password, name, location=""):
    first, _, last = name.partition(" ")
    user.set_password(password)
    user.first_name = first
    user.last_name = last
    user.save(update_fields=["password", "first_name", "last_name"])
    profile = user.creator_profile
    if name and profile.display_name in {"", profile.user.email, profile.user.username}:
        profile.display_name = name
        profile.save(update_fields=["display_name", "updated_at"])
    if location and not profile.location:
        profile.location = location
        profile.save(update_fields=["location", "updated_at"])
    logger.info("scraped_claimed_email creator_id=%s", profile.pk)
    return profile


def claim_by_handles(profile, handles=()):
    if profile is None:
        return 0
    claimed = 0
    for item in handles or ():
        if isinstance(item, (list, tuple)) and len(item) == 2:
            platform, handle = item
        else:
            platform, handle = "", item
        platform = normalize_platform(platform)
        handle = normalize_handle(handle, platform=platform)
        if not platform or not handle:
            continue
        other = find_account(platform, handle)
        if other is None or other.user_id == profile.user_id or other.has_token:
            continue
        _merge_account(other, profile)
        claimed += 1
        logger.info(
            "scraped_claimed_handle creator_id=%s platform=%s handle=%s",
            profile.pk,
            platform,
            handle,
        )
    return claimed


def attach_connected_account(account, extra_handles=()):
    profile = getattr(account.user, "creator_profile", None)
    if profile is None:
        return 0
    handles = [(account.platform, account.username)]
    if account.external_id:
        handles.append((account.platform, account.external_id))
    handles.extend(extra_handles)
    return claim_by_handles(profile, handles)


def find_account(platform, handle):
    for account in SocialAccount.objects.filter(platform=platform).exclude(username=""):
        if same_handle(platform, account.username, handle):
            return account
        if account.profile_url and same_handle(platform, handle_from_url(account.profile_url) or "", handle):
            return account
        if platform == "youtube" and account.external_id and same_handle(platform, account.external_id, handle):
            return account
    return None


def _merge_account(source, profile):
    source_user = source.user
    source_profile = getattr(source_user, "creator_profile", None)
    if source_profile:
        updates = []
        if not profile.display_name and source_profile.display_name:
            profile.display_name = source_profile.display_name
            updates.append("display_name")
        if not profile.bio and source_profile.bio:
            profile.bio = source_profile.bio
            updates.append("bio")
        if not profile.location and source_profile.location:
            profile.location = source_profile.location
            updates.append("location")
        if not profile.audience_notes and source_profile.audience_notes:
            profile.audience_notes = source_profile.audience_notes
            updates.append("audience_notes")
        if updates:
            profile.save(update_fields=updates + ["updated_at"])
    existing = SocialAccount.objects.filter(user=profile.user, platform=source.platform).first()
    if existing is None:
        source.user = profile.user
        source.save(update_fields=["user"])
    else:
        if not existing.has_token:
            if not existing.username:
                existing.username = source.username
            if not existing.profile_url:
                existing.profile_url = source.profile_url
            if not existing.display_name:
                existing.display_name = source.display_name
            existing.save()
            if source.snapshots.exists() and not existing.snapshots.exists():
                source.snapshots.update(account=existing)
        source.delete()
    if is_placeholder_email(source_user.email) and not source_user.social_accounts.exists():
        source_user.delete()


def is_placeholder_email(email):
    return str(email or "").lower().endswith(f"@{PLACEHOLDER_DOMAIN}")


def normalize_platform(value):
    text = (value or "").strip().lower()
    if "instagram" in text or text == "ig":
        return "instagram"
    if "youtube" in text or text in {"yt", "ytb"}:
        return "youtube"
    if "tiktok" in text:
        return "tiktok"
    if "facebook" in text:
        return "facebook"
    if text in {"x", "twitter"}:
        return "twitter"
    return text


def normalize_handle(value, *, platform=""):
    text = (value or "").strip()
    if not text:
        return ""
    if any(part in text.lower() for part in ("instagram.com/", "youtube.com/", "tiktok.com/", "x.com/", "twitter.com/")):
        return handle_from_url(text)
    text = text.split("?")[0].rstrip("/")
    if platform == "youtube":
        if text.startswith("UC") and len(text) >= 16:
            return text
        return "@" + text.lstrip("@").lower()
    return text.lstrip("@").lower()


def handle_from_url(url):
    text = (url or "").strip().split("?")[0].rstrip("/")
    lowered = text.lower()
    if "instagram.com/" in lowered:
        return text.split("instagram.com/")[-1].split("/")[0].lstrip("@").lower()
    if "tiktok.com/" in lowered:
        return text.split("tiktok.com/")[-1].split("/")[0].lstrip("@").lower()
    if "youtube.com/" in lowered:
        rest = text.split("youtube.com/")[-1]
        if rest.startswith("@"):
            return "@" + rest[1:].split("/")[0].lower()
        if rest.startswith("channel/"):
            return rest.split("/")[1]
        if rest.startswith("user/") or rest.startswith("c/"):
            return "@" + rest.split("/")[1].lower()
        return rest.split("/")[0].lower()
    return ""


def same_handle(platform, left, right):
    return normalize_handle(left, platform=platform) == normalize_handle(right, platform=platform)
