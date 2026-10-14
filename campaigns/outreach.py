import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from campaigns.models import Invitation

logger = logging.getLogger("campaigns.outreach")

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
LOCAL_BACKENDS = {
    "accounts.mail.DatabaseEmailBackend",
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}


def send_invitation(invitation):
    invitation.status = Invitation.Status.CONFIRMED
    invitation.save(update_fields=["status"])
    backend = settings.EMAIL_BACKEND
    try:
        sent = send_mail(
            invitation.subject,
            invitation.body,
            settings.DEFAULT_FROM_EMAIL,
            [invitation.to_email],
            fail_silently=False,
        )
    except Exception as exc:
        invitation.status = Invitation.Status.FAILED
        invitation.last_error = str(exc)[:400]
        invitation.save(update_fields=["status", "last_error"])
        logger.warning("invitation_failed id=%s", invitation.pk)
        return invitation
    invitation.sent_at = timezone.now()
    invitation.last_error = ""
    if backend == SMTP_BACKEND:
        invitation.status = Invitation.Status.SENT if sent else Invitation.Status.FAILED
        if not sent:
            invitation.last_error = "The SMTP backend accepted zero messages."
    elif backend in LOCAL_BACKENDS:
        invitation.status = Invitation.Status.STORED_LOCALLY
        if backend == "accounts.mail.DatabaseEmailBackend":
            invitation.last_error = ""
        else:
            invitation.last_error = f"Message handed to {backend}. Delivery is not confirmed."
    else:
        invitation.status = Invitation.Status.SENT if sent else Invitation.Status.FAILED
        if sent:
            invitation.last_error = f"Unknown email backend {backend}; marked sent only because send_mail returned a count."
    invitation.save(update_fields=["status", "sent_at", "last_error"])
    logger.info("invitation_result id=%s status=%s", invitation.pk, invitation.status)
    return invitation


def record_outcome(invitation, status, note=""):
    allowed = {
        Invitation.Status.RESPONDED,
        Invitation.Status.ACCEPTED,
        Invitation.Status.REJECTED,
    }
    if status not in allowed:
        raise ValueError("Only responded, accepted, or rejected can be recorded by a person.")
    invitation.status = status
    invitation.outcome_note = note
    invitation.outcome_at = timezone.now()
    invitation.save(update_fields=["status", "outcome_note", "outcome_at"])
    return invitation
