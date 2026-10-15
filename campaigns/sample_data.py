from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.models import StudioProfile
from accounts.utils import unique_username
from campaigns.models import (
    Campaign,
    CampaignCreator,
    Invitation,
    MessageTemplate,
    Offer,
    RecommendationFeedback,
)
from classify import classify_with_rules
from creators.models import Category, CreatorProfile, Subcategory
from creators.taxonomy import ensure_taxonomy
from networks.demo import connect_sample

User = get_user_model()

SAMPLE_DOMAIN = "@sample.awisee.local"
SAMPLE_OBJECTIVE = "Sample studio data for discovery, metrics, and outreach."
SAMPLE_TEMPLATE_PREFIX = "Sample invite"

CREATORS = (
    {
        "email": "mira.games@sample.awisee.local",
        "name": "Mira Kapoor",
        "location": "India",
        "bio": "I make mobile gaming videos in India.",
        "audience": "Audience plays mobile games.",
        "platform": "youtube",
        "niche": "mobile-gaming",
    },
    {
        "email": "lena.beauty@sample.awisee.local",
        "name": "Lena Brooks",
        "location": "United States",
        "bio": "Makeup and skincare tutorials.",
        "audience": "Beauty shoppers.",
        "platform": "instagram",
        "niche": "makeup",
    },
    {
        "email": "arjun.food@sample.awisee.local",
        "name": "Arjun Mehta",
        "location": "India",
        "bio": "Street food recipes from Mumbai.",
        "audience": "People who cook at home.",
        "platform": "facebook",
        "niche": "street-food",
    },
    {
        "email": "noah.tech@sample.awisee.local",
        "name": "Noah Adler",
        "location": "United Kingdom",
        "bio": "Short reviews of new tech products.",
        "audience": "Early adopters.",
        "platform": "twitter",
        "niche": "mobile-tech",
    },
    {
        "email": "priya.games@sample.awisee.local",
        "name": "Priya Shah",
        "location": "India",
        "bio": "PC gaming streams and patch notes.",
        "audience": "People who play on PC.",
        "platform": "youtube",
        "niche": "pc-gaming",
    },
    {
        "email": "sofia.beauty@sample.awisee.local",
        "name": "Sofia Rossi",
        "location": "France",
        "bio": "Skincare routines and product notes.",
        "audience": "Skincare beginners.",
        "platform": "instagram",
        "niche": "skincare",
    },
    {
        "email": "kenji.fit@sample.awisee.local",
        "name": "Kenji Sato",
        "location": "Japan",
        "bio": "Short yoga sessions for desk workers.",
        "audience": "People who work at a desk.",
        "platform": "facebook",
        "niche": "yoga",
    },
    {
        "email": "amira.fashion@sample.awisee.local",
        "name": "Amira Hassan",
        "location": "United Arab Emirates",
        "bio": "Streetwear outfits and shop finds.",
        "audience": "Streetwear shoppers.",
        "platform": "twitter",
        "niche": "streetwear",
    },
    {
        "email": "lucas.travel@sample.awisee.local",
        "name": "Lucas Ferreira",
        "location": "Brazil",
        "bio": "City walks and weekend travel notes.",
        "audience": "Travel planners.",
        "platform": "youtube",
        "niche": "travel",
    },
    {
        "email": "hannah.edu@sample.awisee.local",
        "name": "Hannah Cole",
        "location": "Canada",
        "bio": "Career advice for first jobs.",
        "audience": "Students and new graduates.",
        "platform": "instagram",
        "niche": "career",
    },
)


def load_studio_sample(actor):
    """Replace the labeled sample studio set. Live accounts are left in place."""
    with transaction.atomic():
        delete_studio_sample()
        ensure_taxonomy()
        creators = [_create_creator(spec) for spec in CREATORS]
        campaigns = _create_campaigns(actor, creators)
    return {"creators": len(creators), "campaigns": len(campaigns)}


def delete_studio_sample():
    """Remove only the studio sample creators, their metrics, and the sample campaign."""
    User.objects.filter(email__iendswith=SAMPLE_DOMAIN).delete()
    Campaign.objects.filter(objective=SAMPLE_OBJECTIVE).delete()
    MessageTemplate.objects.filter(name__startswith=SAMPLE_TEMPLATE_PREFIX).delete()


def _create_creator(spec):
    user = User.objects.create_user(
        username=unique_username(spec["email"]),
        email=spec["email"],
        password=None,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    StudioProfile.objects.update_or_create(user=user, defaults={"role": StudioProfile.Role.CREATOR})
    creator = CreatorProfile.objects.create(
        user=user,
        display_name=spec["name"],
        bio=spec["bio"],
        location=spec["location"],
        audience_notes=spec["audience"],
        share_metrics=True,
        discoverable=True,
    )
    niche = Subcategory.objects.filter(slug=spec["niche"]).first()
    if niche is None and spec["niche"] == "tech":
        niche = Subcategory.objects.filter(category__slug="tech").first()
    if niche is None and spec["niche"] == "street-food":
        niche = Subcategory.objects.filter(slug="street-food").first() or Subcategory.objects.filter(category__slug="food").first()
    if niche:
        creator.niches.add(niche)
    connect_sample(user, spec["platform"])
    classify_with_rules(creator)
    creator.refresh_status()
    return creator


def _create_campaigns(actor, creators):
    niches = list(Category.objects.all()[:10])
    locations = ["India", "United States", "United Kingdom", "France", "Japan", "Brazil", "Canada", "Germany", "Australia", "Indonesia"]
    platforms = ["youtube", "instagram", "facebook", "twitter"]
    campaigns = []
    for index, creator in enumerate(creators):
        niche = niches[index % len(niches)] if niches else None
        campaign = Campaign.objects.create(
            owner=actor,
            name=f"Sample campaign {index + 1:02d}",
            objective=SAMPLE_OBJECTIVE,
            budget=20000 + index * 5000,
            primary_niche=niche.name if niche else "",
            location=locations[index % len(locations)],
            platforms=[platforms[index % len(platforms)]],
            follower_min=1000,
            follower_max=500000,
            min_engagement=1,
            audience_notes="Sample audience notes.",
            status=Campaign.Status.ACTIVE if index % 2 == 0 else Campaign.Status.DRAFT,
        )
        CampaignCreator.objects.create(
            campaign=campaign,
            creator=creator,
            state=CampaignCreator.State.SHORTLISTED,
            added_by=actor,
        )
        template = MessageTemplate.objects.create(
            name=f"{SAMPLE_TEMPLATE_PREFIX} {index + 1:02d}",
            subject="Join {campaign_name}",
            body="Hi {creator_name}, we would like you on {campaign_name} in {location}.",
            created_by=actor,
        )
        Invitation.objects.create(
            campaign=campaign,
            creator=creator,
            template=template,
            to_email=creator.user.email,
            subject=template.subject,
            body=template.body,
            status=Invitation.Status.STORED_LOCALLY,
            created_by=actor,
        )
        Offer.objects.create(
            campaign=campaign,
            creator=creator,
            amount=15000 + index * 1000,
            currency="INR",
            state=Offer.State.PROPOSED,
            note="Sample offer.",
            created_by=actor,
        )
        RecommendationFeedback.objects.create(
            campaign=campaign,
            creator=creator,
            user=actor,
            decision=RecommendationFeedback.Decision.YES if index % 2 == 0 else RecommendationFeedback.Decision.NEEDS_REVIEW,
            comment="Sample feedback.",
        )
        campaigns.append(campaign)
    return campaigns
