import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from networks.facebook.client import FacebookAPIError
from networks.facebook.sync import sync_facebook
from networks.facebook_models import FacebookSyncJob

logger = logging.getLogger("networks.facebook")

OPEN_STATUSES = (FacebookSyncJob.Status.PENDING, FacebookSyncJob.Status.RUNNING)


def enqueue_facebook_sync(account, mode="incremental", requested_by=None, scopes=""):
    if mode not in {FacebookSyncJob.Mode.FULL, FacebookSyncJob.Mode.INCREMENTAL}:
        mode = FacebookSyncJob.Mode.INCREMENTAL
    existing = FacebookSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    if existing:
        updates = []
        if scopes and scopes != existing.granted_scopes:
            existing.granted_scopes = scopes
            updates.append("granted_scopes")
        if (
            mode == FacebookSyncJob.Mode.FULL
            and existing.mode != FacebookSyncJob.Mode.FULL
            and existing.status == FacebookSyncJob.Status.PENDING
        ):
            existing.mode = FacebookSyncJob.Mode.FULL
            updates.append("mode")
        if updates:
            existing.save(update_fields=updates)
        logger.info("facebook_sync_duplicate job_id=%s account_id=%s", existing.pk, account.pk)
        return existing
    try:
        job = FacebookSyncJob.objects.create(
            account=account,
            requested_by=requested_by,
            mode=mode,
            granted_scopes=scopes or "",
        )
    except IntegrityError:
        return FacebookSyncJob.objects.filter(account=account, status__in=OPEN_STATUSES).first()
    logger.info("facebook_sync_queued job_id=%s account_id=%s mode=%s", job.pk, account.pk, mode)
    return job


def maybe_run(job):
    if not getattr(settings, "FACEBOOK_SYNC_EAGER", False):
        return job
    return process_facebook_job(job)


def process_facebook_job(job):
    if job.status == FacebookSyncJob.Status.COMPLETED:
        return job
    job.status = FacebookSyncJob.Status.RUNNING
    job.attempts += 1
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "attempts", "started_at"])
    try:
        return sync_facebook(job.account, job)
    except Exception as exc:
        logger.exception("facebook_sync_unexpected job_id=%s", job.pk)
        job.status = FacebookSyncJob.Status.FAILED
        job.outcome = FacebookSyncJob.Outcome.RETRYABLE
        job.last_error = str(exc)[:1000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "outcome", "last_error", "finished_at"])
        return job


def run_for_account(account, mode="incremental", requested_by=None):
    job = enqueue_facebook_sync(account, mode=mode, requested_by=requested_by)
    if job.status == FacebookSyncJob.Status.RUNNING and job.attempts:
        return job
    process_facebook_job(job)
    if job.status == FacebookSyncJob.Status.FAILED and job.outcome == FacebookSyncJob.Outcome.RETRYABLE:
        raise FacebookAPIError(job.last_error or "Facebook sync will be retried.", kind="retryable")
    if job.status == FacebookSyncJob.Status.FAILED and job.outcome == FacebookSyncJob.Outcome.PERMISSION_DENIED:
        raise FacebookAPIError(job.last_error or "Facebook permission was denied.", kind="permission")
    if job.status == FacebookSyncJob.Status.FAILED:
        raise FacebookAPIError(job.last_error or "Facebook sync failed.", kind="permanent")
    return job


def process_pending_facebook_jobs(limit=None):
    limit = limit or int(getattr(settings, "FACEBOOK_JOB_BATCH_SIZE", 5) or 5)
    cutoff = timezone.now() - timedelta(minutes=int(getattr(settings, "FACEBOOK_STALE_MINUTES", 30) or 30))
    FacebookSyncJob.objects.filter(status=FacebookSyncJob.Status.RUNNING, started_at__lt=cutoff).update(
        status=FacebookSyncJob.Status.PENDING
    )
    jobs = list(
        FacebookSyncJob.objects.filter(status=FacebookSyncJob.Status.PENDING)
        .select_related("account", "account__user")
        .order_by("created_at")[:limit]
    )
    return [process_facebook_job(job) for job in jobs]
