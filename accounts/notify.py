import logging

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Notification, StudioProfile
from accounts.permissions import has_perm

logger = logging.getLogger("accounts.notify")
User = get_user_model()


def notify_users(users, *, title, body="", url="", kind=Notification.Kind.STATUS):
    seen = set()
    rows = []
    for user in users:
        if not user or user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(
            Notification(user=user, title=title, body=body, url=url, kind=kind)
        )
    if rows:
        Notification.objects.bulk_create(rows)
        logger.info("notifications_created count=%s kind=%s", len(rows), kind)
    return len(rows)


def staff_recipients(*, permission="outreach"):
    users = []
    for profile in StudioProfile.objects.select_related("user").exclude(role=StudioProfile.Role.CREATOR):
        user = profile.user
        if not user.is_active:
            continue
        if has_perm(user, permission) or has_perm(user, "users"):
            users.append(user)
    users.extend(User.objects.filter(is_superuser=True, is_active=True))
    return users


def notify_creator_invitation(invitation):
    creator_user = invitation.creator.user
    url = reverse("creators:invitations")
    notify_users(
        [creator_user],
        title=f"Invitation: {invitation.campaign.name}",
        body=invitation.subject,
        url=url,
        kind=Notification.Kind.INVITATION,
    )


def notify_staff_invitation_response(invitation):
    decision = invitation.get_status_display()
    url = reverse("campaigns:detail", args=[invitation.campaign_id])
    notify_users(
        staff_recipients(permission="outreach"),
        title=f"{invitation.creator} {decision.lower()} {invitation.campaign.name}",
        body=invitation.outcome_note or f"Invitation is now {decision}.",
        url=url,
        kind=Notification.Kind.INVITATION,
    )


def notify_staff_offer_response(offer):
    url = reverse("campaigns:detail", args=[offer.campaign_id])
    notify_users(
        staff_recipients(permission="campaigns"),
        title=f"{offer.creator} {offer.get_state_display().lower()} an offer on {offer.campaign.name}",
        body=f"{offer.amount} {offer.currency}. {offer.note}".strip(),
        url=url,
        kind=Notification.Kind.OFFER,
    )


def notify_creator_offer(offer):
    notify_users(
        [offer.creator.user],
        title=f"Offer on {offer.campaign.name}",
        body=f"{offer.amount} {offer.currency}. {offer.note}".strip(),
        url=reverse("creators:my_campaign", args=[offer.campaign_id]),
        kind=Notification.Kind.OFFER,
    )
