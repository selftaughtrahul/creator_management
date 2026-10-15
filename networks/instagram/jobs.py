import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from networks.instagram.client import InstagramAPIError
from networks.instagram.sync import sync_instagram
from networks.instagram_models import InstagramSyncJob

logger = logging.getLogger("networks.instagram")

OPEN_STATUSES = (InstagramSyncJob.Status.PENDING, InstagramSyncJob.Status.RUNNING)


def enqueue_instagram_sync(account, mode="incremental", requested_by=None, scopes=""):
    if mode not in {InstagramSyncJob.Mode.FULL, InstagramSyncJob.Mode.INCREMENTAL}:
        mode = InstagramSyncJob.Mode.INCREMENTAL
    existing = InstagramSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    if existing:
        updates = []
        if scopes and scopes != existing.granted_scopes:
            existing.granted_scopes = scopes
            updates.append("granted_scopes")
        if (
            mode == InstagramSyncJob.Mode.FULL
            and existing.mode != InstagramSyncJob.Mode.FULL
            and existing.status == InstagramSyncJob.Status.PENDING
        ):
            existing.mode = InstagramSyncJob.Mode.FULL
            updates.append("mode")
        if updates:
            existing.save(update_fields=updates)
        logger.info("instagram_sync_duplicate job_id=%s account_id=%s", existing.pk, account.pk)
        return existing
    try:
        job = InstagramSyncJob.objects.create(
            account=account,
            requested_by=requested_by,
            mode=mode,
            granted_scopes=scopes or "",
        )
    except IntegrityError:
        return InstagramSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    logger.info("instagram_sync_queued job_id=%s account_id=%s mode=%s", job.pk, account.pk, mode)
    return job


def maybe_run(job):
    if not getattr(settings, "INSTAGRAM_SYNC_EAGER", False):
        return job
    return process_instagram_job(job)


def process_instagram_job(job):
    if job.status == InstagramSyncJob.Status.COMPLETED:
        return job
    job.status = InstagramSyncJob.Status.RUNNING
    job.attempts += 1
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "attempts", "started_at"])
    try:
        return sync_instagram(job.account, job)
    except Exception as exc:
        logger.exception("instagram_sync_unexpected job_id=%s", job.pk)
        job.status = InstagramSyncJob.Status.FAILED
        job.outcome = InstagramSyncJob.Outcome.RETRYABLE
        job.last_error = str(exc)[:1000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "outcome", "last_error", "finished_at"])
        return job


def run_for_account(account, mode="incremental", requested_by=None):
    job = enqueue_instagram_sync(account, mode=mode, requested_by=requested_by)
    if job.status == InstagramSyncJob.Status.RUNNING and job.attempts:
        return job
    process_instagram_job(job)
    if job.status == InstagramSyncJob.Status.FAILED and job.outcome == InstagramSyncJob.Outcome.RETRYABLE:
        raise InstagramAPIError(job.last_error or "Instagram sync will be retried.", kind="retryable")
    if job.status == InstagramSyncJob.Status.FAILED and job.outcome == InstagramSyncJob.Outcome.PERMISSION_DENIED:
        raise InstagramAPIError(job.last_error or "Instagram permission was denied.", kind="permission")
    if job.status == InstagramSyncJob.Status.FAILED:
        raise InstagramAPIError(job.last_error or "Instagram sync failed.", kind="permanent")
    return job


def process_pending_instagram_jobs(limit=None):
    limit = limit or int(getattr(settings, "INSTAGRAM_JOB_BATCH_SIZE", 5) or 5)
    cutoff = timezone.now() - timedelta(minutes=int(getattr(settings, "INSTAGRAM_STALE_MINUTES", 30) or 30))
    InstagramSyncJob.objects.filter(status=InstagramSyncJob.Status.RUNNING, started_at__lt=cutoff).update(
        status=InstagramSyncJob.Status.PENDING
    )
    jobs = list(
        InstagramSyncJob.objects.filter(status=InstagramSyncJob.Status.PENDING)
        .select_related("account", "account__user")
        .order_by("created_at")[:limit]
    )
    return [process_instagram_job(job) for job in jobs]
