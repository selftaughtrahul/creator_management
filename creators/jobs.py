import logging
import time

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone

from creators.models import EnrichmentJob
from networks.http import ProviderError
from networks.models import SocialAccount
from networks.sync import sync_account
from networks.youtube.client import YouTubeAPIError
from networks.instagram.client import InstagramAPIError
from networks.facebook.client import FacebookAPIError

logger = logging.getLogger("creators.jobs")

OPEN_STATUSES = (EnrichmentJob.Status.PENDING, EnrichmentJob.Status.RUNNING)
MAX_ATTEMPTS = 3


def enqueue_job(creator, platform, requested_by=None):
    existing = EnrichmentJob.objects.filter(
        creator=creator,
        platform=platform,
        status__in=OPEN_STATUSES,
    ).first()
    if existing:
        logger.info(
            "enrichment_duplicate creator_id=%s platform=%s job_id=%s",
            creator.pk,
            platform,
            existing.pk,
        )
        return existing, False
    try:
        job = EnrichmentJob.objects.create(
            creator=creator,
            platform=platform,
            requested_by=requested_by,
        )
    except IntegrityError:
        existing = EnrichmentJob.objects.filter(
            creator=creator,
            platform=platform,
            status__in=OPEN_STATUSES,
        ).first()
        return existing, False
    logger.info("enrichment_queued job_id=%s creator_id=%s platform=%s", job.pk, creator.pk, platform)
    return job, True


def process_pending_jobs(limit=None):
    limit = limit or int(getattr(settings, "JOB_BATCH_SIZE", 10) or 10)
    delay = float(getattr(settings, "JOB_PLATFORM_DELAY_SECONDS", 1) or 0)
    jobs = list(
        EnrichmentJob.objects.filter(status=EnrichmentJob.Status.PENDING)
        .select_related("creator", "creator__user")
        .order_by("created_at")[:limit]
    )
    processed = []
    for index, job in enumerate(jobs):
        processed.append(process_job(job))
        if delay and index < len(jobs) - 1:
            time.sleep(delay)
    return processed


def process_job(job):
    job.status = EnrichmentJob.Status.RUNNING
    job.attempts += 1
    job.started_at = timezone.now()
    job.last_error = ""
    job.save(update_fields=["status", "attempts", "started_at", "last_error"])
    account = SocialAccount.objects.filter(
        user=job.creator.user,
        platform=job.platform,
    ).first()
    if not account:
        return _finish(
            job,
            EnrichmentJob.Status.BLOCKED,
            "No authorized connection for this platform. Official APIs in this app only read accounts the user connected.",
        )
    if account.is_sample:
        return _finish(
            job,
            EnrichmentJob.Status.BLOCKED,
            "This account is labeled sample data. Connect a live account before enrichment.",
        )
    if not account.has_token:
        return _finish(
            job,
            EnrichmentJob.Status.BLOCKED,
            "The connected account has no access token stored.",
        )
    try:
        if account.platform == "youtube":
            from networks.youtube.jobs import run_for_account

            run_for_account(account, mode="incremental", requested_by=job.requested_by)
        elif account.platform == "instagram":
            from networks.instagram.jobs import run_for_account as run_instagram

            run_instagram(account, mode="incremental", requested_by=job.requested_by)
        elif account.platform == "facebook":
            from networks.facebook.jobs import run_for_account as run_facebook

            run_facebook(account, mode="incremental", requested_by=job.requested_by)
        else:
            sync_account(account)
    except YouTubeAPIError as exc:
        if exc.kind in {"permission", "permanent"}:
            return _finish(job, EnrichmentJob.Status.FAILED, str(exc))
        if exc.kind == "quota":
            return _finish(job, EnrichmentJob.Status.BLOCKED, str(exc))
        return _retry_or_fail(job, str(exc))
    except InstagramAPIError as exc:
        if exc.kind in {"permission", "permanent", "token"}:
            return _finish(job, EnrichmentJob.Status.FAILED, str(exc))
        return _retry_or_fail(job, str(exc))
    except FacebookAPIError as exc:
        if exc.kind in {"permission", "permanent", "token"}:
            return _finish(job, EnrichmentJob.Status.FAILED, str(exc))
        return _retry_or_fail(job, str(exc))
    except ProviderError as exc:
        return _retry_or_fail(job, str(exc))
    except Exception as exc:
        logger.exception("enrichment_unexpected job_id=%s", job.pk)
        return _retry_or_fail(job, str(exc)[:400])
    job.creator.refresh_status()
    return _finish(job, EnrichmentJob.Status.SUCCEEDED, "")


def retry_job(job):
    if job.status not in (EnrichmentJob.Status.FAILED, EnrichmentJob.Status.BLOCKED):
        return job
    open_job = EnrichmentJob.objects.filter(
        creator=job.creator,
        platform=job.platform,
        status__in=OPEN_STATUSES,
    ).exclude(pk=job.pk)
    if open_job.exists():
        return open_job.first()
    job.status = EnrichmentJob.Status.PENDING
    job.last_error = ""
    job.save(update_fields=["status", "last_error"])
    return job


def backlog_counts():
    counts = {status: 0 for status, _ in EnrichmentJob.Status.choices}
    for row in EnrichmentJob.objects.values("status").annotate(total=Count("id")):
        counts[row["status"]] = row["total"]
    counts["open"] = counts.get(EnrichmentJob.Status.PENDING, 0) + counts.get(EnrichmentJob.Status.RUNNING, 0)
    return counts


def _retry_or_fail(job, error):
    if job.attempts < MAX_ATTEMPTS:
        job.status = EnrichmentJob.Status.PENDING
        job.last_error = error
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        logger.warning("enrichment_retry job_id=%s attempts=%s", job.pk, job.attempts)
        return job
    return _finish(job, EnrichmentJob.Status.FAILED, error)


def _finish(job, status, error):
    job.status = status
    job.last_error = error
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "last_error", "finished_at"])
    logger.info("enrichment_done job_id=%s status=%s", job.pk, status)
    return job
