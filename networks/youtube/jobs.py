import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from networks.youtube.client import YouTubeAPIError
from networks.youtube.sync import sync_youtube
from networks.youtube_models import YouTubeSyncJob

logger = logging.getLogger("networks.youtube")

OPEN_STATUSES = (YouTubeSyncJob.Status.PENDING, YouTubeSyncJob.Status.RUNNING)


def enqueue_youtube_sync(account, mode="incremental", requested_by=None, scopes=""):
    if mode not in {YouTubeSyncJob.Mode.FULL, YouTubeSyncJob.Mode.INCREMENTAL}:
        mode = YouTubeSyncJob.Mode.INCREMENTAL
    existing = YouTubeSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    if existing:
        updates = []
        if scopes and scopes != existing.granted_scopes:
            existing.granted_scopes = scopes
            updates.append("granted_scopes")
        if mode == YouTubeSyncJob.Mode.FULL and existing.mode != YouTubeSyncJob.Mode.FULL and existing.status == YouTubeSyncJob.Status.PENDING:
            existing.mode = YouTubeSyncJob.Mode.FULL
            updates.append("mode")
        if updates:
            existing.save(update_fields=updates)
        logger.info("youtube_sync_duplicate job_id=%s account_id=%s", existing.pk, account.pk)
        return existing
    try:
        job = YouTubeSyncJob.objects.create(
            account=account,
            requested_by=requested_by,
            mode=mode,
            granted_scopes=scopes or "",
        )
    except IntegrityError:
        return YouTubeSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    logger.info("youtube_sync_queued job_id=%s account_id=%s mode=%s", job.pk, account.pk, mode)
    return job


def store_granted_scopes(account, scopes):
    if not scopes:
        return
    account.youtube_channels.update(granted_scopes=scopes)
    YouTubeSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES, granted_scopes="").update(granted_scopes=scopes)


def maybe_run(job):
    if not getattr(settings, "YOUTUBE_SYNC_EAGER", False):
        return job
    return process_youtube_job(job)


def process_youtube_job(job):
    if job.status == YouTubeSyncJob.Status.COMPLETED:
        return job
    job.status = YouTubeSyncJob.Status.RUNNING
    job.attempts += 1
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "attempts", "started_at"])
    try:
        return sync_youtube(job.account, job)
    except Exception as exc:
        logger.exception("youtube_sync_unexpected job_id=%s", job.pk)
        job.status = YouTubeSyncJob.Status.FAILED
        job.outcome = YouTubeSyncJob.Outcome.RETRYABLE
        job.last_error = str(exc)[:1000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "outcome", "last_error", "finished_at"])
        return job


def run_for_account(account, mode="incremental", requested_by=None):
    job = enqueue_youtube_sync(account, mode=mode, requested_by=requested_by)
    if job.status == YouTubeSyncJob.Status.RUNNING and job.attempts:
        return job
    process_youtube_job(job)
    if job.status == YouTubeSyncJob.Status.PENDING and job.outcome == YouTubeSyncJob.Outcome.RETRYABLE:
        raise YouTubeAPIError(job.last_error or "YouTube sync will be retried.", kind="retryable")
    if job.status == YouTubeSyncJob.Status.FAILED and job.outcome == YouTubeSyncJob.Outcome.QUOTA_EXHAUSTED:
        raise YouTubeAPIError(job.last_error or "YouTube quota was exhausted.", kind="quota")
    if job.status == YouTubeSyncJob.Status.FAILED and job.outcome == YouTubeSyncJob.Outcome.PERMISSION_DENIED:
        raise YouTubeAPIError(job.last_error or "YouTube permission was denied.", kind="permission")
    if job.status == YouTubeSyncJob.Status.FAILED:
        raise YouTubeAPIError(job.last_error or "YouTube sync failed.", kind="permanent")
    return job


def process_pending_youtube_jobs(limit=None):
    limit = limit or int(getattr(settings, "YOUTUBE_JOB_BATCH_SIZE", 5) or 5)
    cutoff = timezone.now() - timedelta(minutes=int(getattr(settings, "YOUTUBE_STALE_MINUTES", 30) or 30))
    YouTubeSyncJob.objects.filter(status=YouTubeSyncJob.Status.RUNNING, started_at__lt=cutoff).update(
        status=YouTubeSyncJob.Status.PENDING
    )
    jobs = list(
        YouTubeSyncJob.objects.filter(status=YouTubeSyncJob.Status.PENDING)
        .select_related("account", "account__user")
        .order_by("created_at")[:limit]
    )
    return [process_youtube_job(job) for job in jobs]
