"""Stale job detection.

A job stuck in PROCESSING for over an hour means the worker died around the
platform call — the outcome is UNKNOWN, so the job is flagged needs_review
(never blindly failed-and-retried: that risks a double post). The window is
measured from `started_at`, not created_at — a job created at 09:00 for a
17:00 publish is not "stuck" at 10:00.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.workers.celery_app import celery_app
from sqlalchemy import select

from app.db.session import worker_session
from app.models import JobStatus, PublishJob
from app.workers.tasks.publish import _finalize_post

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=1)


@celery_app.task(name="app.workers.tasks.cleanup.cleanup_stale_jobs")
def cleanup_stale_jobs():
    cutoff = datetime.now(timezone.utc) - STALE_AFTER
    flagged = 0
    with worker_session() as db:
        stuck = db.execute(
            select(PublishJob).where(
                PublishJob.status == JobStatus.PROCESSING,
                PublishJob.started_at.is_not(None),
                PublishJob.started_at <= cutoff,
            )
        ).scalars().all()

        for job in stuck:
            job.status = JobStatus.NEEDS_REVIEW
            job.error_message = (
                "Worker did not report an outcome within an hour of starting. "
                "Verify on the platform before retrying."
            )
            job.processed_at = datetime.now(timezone.utc)
            _finalize_post(db, job.post_id)
            flagged += 1
            logger.warning(f"[Cleanup] job {job.id} flagged needs_review")
    return {"flagged": flagged}
