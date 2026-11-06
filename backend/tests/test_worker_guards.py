"""publish_post_task guards that do not need a database."""
from datetime import datetime, timedelta, timezone

from app.core.exceptions import PermanentPublishError, RetryablePublishError, UnknownOutcomePublishError
from app.workers.tasks.publish import IN_PROGRESS_GRACE


def test_unknown_outcome_is_not_retryable_or_permanent():
    """The worker routes it to needs_review; it must not be caught by the
    Retryable/Permanent handlers that sit above it."""
    assert not issubclass(UnknownOutcomePublishError, RetryablePublishError)
    assert not issubclass(UnknownOutcomePublishError, PermanentPublishError)


def test_in_progress_grace_is_shorter_than_stale_cleanup():
    from app.workers.tasks.cleanup import STALE_AFTER
    assert timedelta(0) < IN_PROGRESS_GRACE <= STALE_AFTER


def test_adapters_final_call_raises_unknown_outcome(monkeypatch):
    """A dropped connection on the irreversible publish call must surface as
    UnknownOutcome, never as Retryable (which would publish twice)."""
    import httpx
    from app.config import settings
    from app.services.publishing.facebook import FacebookAdapter
    from app.services.publishing.linkedin import LinkedInAdapter

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")  # disable mock path

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): raise httpx.ReadTimeout("dropped")

    monkeypatch.setattr(httpx, "Client", Boom)
    for adapter, meta in ((FacebookAdapter(), {"page_id": "1"}), (LinkedInAdapter(), {"author_urn": "urn:li:x"})):
        try:
            adapter.publish("hi", [], "real-token", meta)
        except UnknownOutcomePublishError:
            pass
        else:
            raise AssertionError(f"{type(adapter).__name__} did not raise UnknownOutcomePublishError")
