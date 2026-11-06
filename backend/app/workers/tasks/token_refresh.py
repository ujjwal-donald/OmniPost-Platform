"""Token expiry monitoring.

Refresh is a different mechanism on every platform:
- Facebook/Instagram page tokens are NOT refreshable — a long-lived user
  token (~60 days) is exchanged at connect time; past expiry the user must
  re-authenticate. The account is flagged `needs_reauth` so the UI can show
  a reconnect banner BEFORE scheduled posts start failing.
- LinkedIn supports programmatic refresh (Phase 2, with the LinkedIn
  adapter's full OAuth flow).
"""
import logging
from datetime import datetime, timedelta, timezone

from app.workers.celery_app import celery_app
from sqlalchemy import select

from app.db.session import worker_session
from app.models import SocialAccount

logger = logging.getLogger(__name__)

WARN_WINDOW_DAYS = 7


@celery_app.task(name="app.workers.tasks.token_refresh.check_expiring_tokens")
def check_expiring_tokens():
    threshold = datetime.now(timezone.utc) + timedelta(days=WARN_WINDOW_DAYS)
    flagged = 0
    with worker_session() as db:
        expiring = db.execute(
            select(SocialAccount).where(
                SocialAccount.is_active == True,  # noqa: E712
                SocialAccount.needs_reauth == False,  # noqa: E712
                SocialAccount.token_expires_at.is_not(None),
                SocialAccount.token_expires_at <= threshold,
            )
        ).scalars().all()

        for account in expiring:
            account.needs_reauth = True
            flagged += 1
            logger.warning(
                f"[Token Monitor] {account.platform.value} account '{account.account_name}' "
                f"expires {account.token_expires_at.isoformat()} — flagged for re-auth."
            )
    return {"flagged": flagged}
