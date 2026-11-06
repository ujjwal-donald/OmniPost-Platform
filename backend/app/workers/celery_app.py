from celery import Celery

from app.config import settings

celery_app = Celery(
    "ai_content_creator",
    broker=settings.REDIS_URL,
    include=[
        "app.workers.tasks.publish",
        "app.workers.tasks.token_refresh",
        "app.workers.tasks.cleanup",
    ],
)

celery_app.conf.update(
    # JSON only — pickle is a remote-code-execution vector.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # No result backend: job state lives in the publish_jobs table.
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        # ── SCHEDULING MODEL ──────────────────────────────────────────
        # No long-ETA dispatch (Redis re-delivers ETA tasks every
        # visibility_timeout and loses them on restarts). This Beat task
        # scans publish_jobs every minute and dispatches due jobs as
        # immediate tasks. Cancellation = a status update on the job row.
        "dispatch-due-publish-jobs": {
            "task": "app.workers.tasks.publish.dispatch_due_jobs",
            "schedule": 60.0,
        },
        "check-expiring-tokens": {
            "task": "app.workers.tasks.token_refresh.check_expiring_tokens",
            "schedule": 6 * 3600.0,
        },
        "cleanup-stale-jobs": {
            "task": "app.workers.tasks.cleanup.cleanup_stale_jobs",
            "schedule": 3600.0,
        },
    },
)
