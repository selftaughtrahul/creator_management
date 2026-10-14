from django.utils import timezone

from accounts.notify import notify_staff_invitation_response, notify_staff_offer_response
from campaigns.models import CampaignCreator, Invitation, Offer


OPEN_INVITATIONS = {
    Invitation.Status.DRAFT,
    Invitation.Status.CONFIRMED,
    Invitation.Status.STORED_LOCALLY,
    Invitation.Status.SENT,
    Invitation.Status.RESPONDED,
}

OPEN_OFFERS = {Offer.State.PROPOSED, Offer.State.COUNTERED}


def apply_invitation_response(invitation, *, accepted, actor, note=""):
    invitation.status = Invitation.Status.ACCEPTED if accepted else Invitation.Status.REJECTED
    invitation.outcome_note = note
    invitation.outcome_at = timezone.now()
    invitation.save(update_fields=["status", "outcome_note", "outcome_at"])
    if accepted:
        membership, created = CampaignCreator.objects.get_or_create(
            campaign=invitation.campaign,
            creator=invitation.creator,
            defaults={"added_by": actor, "state": CampaignCreator.State.SHORTLISTED},
        )
        if not created and membership.state != CampaignCreator.State.ASSIGNED:
            membership.state = CampaignCreator.State.SHORTLISTED
            membership.save(update_fields=["state", "updated_at"])
        invitation.creator.refresh_status()
    notify_staff_invitation_response(invitation)
    return invitation


def apply_offer_response(offer, *, accepted, actor, note="", notify=True):
    offer.state = Offer.State.ACCEPTED if accepted else Offer.State.DECLINED
    offer.decided_by = actor
    if note:
        offer.note = f"{offer.note}\n{note}".strip() if offer.note else note
    offer.save(update_fields=["state", "decided_by", "note", "updated_at"])
    if accepted:
        membership, _ = CampaignCreator.objects.get_or_create(
            campaign=offer.campaign,
            creator=offer.creator,
            defaults={"added_by": actor, "state": CampaignCreator.State.ASSIGNED},
        )
        membership.state = CampaignCreator.State.ASSIGNED
        if membership.recorded_cost is None:
            membership.recorded_cost = offer.amount
        membership.save()
        offer.creator.refresh_status()
        related = (
            Invitation.objects.filter(campaign=offer.campaign, creator=offer.creator, status__in=OPEN_INVITATIONS)
            .order_by("-created_at")
            .first()
        )
        if related:
            related.status = Invitation.Status.ACCEPTED
            related.outcome_at = timezone.now()
            related.outcome_note = related.outcome_note or "Accepted with the offer."
            related.save(update_fields=["status", "outcome_at", "outcome_note"])
    if notify:
        notify_staff_offer_response(offer)
    return offer
