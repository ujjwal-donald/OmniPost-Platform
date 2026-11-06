"""Publishing journey and retry — real API, real DB, worker task run in-process."""
import uuid

import pytest

from .conftest import register, run_job

pytestmark = pytest.mark.integration


def make_account(api, actor, platform="facebook", name=None):
    account_id = f"mock-{platform}-{uuid.uuid4().hex[:8]}"
    r = api.post(
        f"/api/v1/social/workspaces/{actor.workspace_id}/connect/mock",
        json={"platform": platform, "account_name": name or account_id, "platform_account_id": account_id},
        headers=actor.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def approved_post(api, actor, caption="Hello from the test suite"):
    brand = api.post(f"/api/v1/workspaces/{actor.workspace_id}/brands", json={"name": "Test brand"}, headers=actor.headers).json()
    post = api.post(f"/api/v1/brands/{brand['id']}/posts", json={"caption": caption}, headers=actor.headers).json()
    assert api.post(f"/api/v1/posts/{post['id']}/submit", headers=actor.headers).status_code == 200
    r = api.post(f"/api/v1/posts/{post['id']}/approve", headers=actor.headers)  # one-person workspace: self-approval on
    assert r.status_code == 200, r.text
    return post["id"]


def publish_now(api, actor, post_id, account_ids):
    r = api.post(f"/api/v1/posts/{post_id}/publish-now", json={"social_account_ids": account_ids}, headers=actor.headers)
    assert r.status_code == 200, r.text
    return {j["social_account_id"]: j["id"] for j in r.json()["jobs"]}


def post_status(api, actor, post_id):
    return api.get(f"/api/v1/posts/{post_id}", headers=actor.headers).json()["status"]


def jobs_by_account(api, actor, post_id):
    return {j["social_account_id"]: j for j in api.get(f"/api/v1/posts/{post_id}/jobs", headers=actor.headers).json()}


# ── Journey ────────────────────────────────────────────────────────────
def test_publish_now_end_to_end(api):
    owner = register(api)
    acct = make_account(api, owner)
    post_id = approved_post(api, owner)

    jobs = publish_now(api, owner, post_id, [acct["id"]])
    assert post_status(api, owner, post_id) == "scheduled"

    assert run_job(jobs[acct["id"]])["status"] == "published"
    assert post_status(api, owner, post_id) == "published"
    job = jobs_by_account(api, owner, post_id)[acct["id"]]
    assert job["status"] == "completed" and job["result"]["platform_post_id"].startswith("fb_mock_")


# ── Retry ──────────────────────────────────────────────────────────────
def failed_post(api, owner):
    """An approved post whose only job failed because the account was disconnected."""
    acct = make_account(api, owner)
    post_id = approved_post(api, owner)
    jobs = publish_now(api, owner, post_id, [acct["id"]])
    assert api.delete(f"/api/v1/social/accounts/{acct['id']}", headers=owner.headers).status_code == 200
    assert run_job(jobs[acct["id"]])["status"] == "failed"
    assert post_status(api, owner, post_id) == "failed"
    return post_id, acct, jobs[acct["id"]]


def test_retry_after_reconnect_publishes(api, db_exec):
    owner = register(api)
    post_id, acct, job_id = failed_post(api, owner)

    # Still disconnected → told to reconnect, nothing queued.
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 400 and "Reconnect" in r.json()["detail"]

    # Reconnect (mock connect reactivates the same page), then retry.
    make_account_again = api.post(
        f"/api/v1/social/workspaces/{owner.workspace_id}/connect/mock",
        json={"platform": "facebook", "account_name": acct["account_name"], "platform_account_id": acct["platform_account_id"]},
        headers=owner.headers,
    )
    assert make_account_again.status_code == 201
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 200, r.text
    assert [j["id"] for j in r.json()["jobs"]] == [job_id]          # same job re-queued
    assert r.json()["jobs"][0]["status"] == "queued"
    assert post_status(api, owner, post_id) == "scheduled"

    assert run_job(job_id)["status"] == "published"
    assert post_status(api, owner, post_id) == "published"

    # The earlier failure is kept in the audit trail.
    row = db_exec(
        "SELECT changes FROM audit_events WHERE entity_id = :p AND changes->'after'->>'mode' = 'retry'", p=post_id
    ).first()
    assert row and "disconnected" in row[0]["after"]["previous"][0]["error"]

    # Published posts can't be retried.
    assert api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers).status_code == 400


def test_retry_never_resends_completed_jobs(api):
    owner = register(api)
    a = make_account(api, owner)
    b = make_account(api, owner)
    post_id = approved_post(api, owner)
    jobs = publish_now(api, owner, post_id, [a["id"], b["id"]])
    api.delete(f"/api/v1/social/accounts/{b['id']}", headers=owner.headers)
    run_job(jobs[a["id"]])
    run_job(jobs[b["id"]])
    assert post_status(api, owner, post_id) == "partially_published"

    # Asking to retry the job that already went out is refused.
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={"job_ids": [jobs[a["id"]]]}, headers=owner.headers)
    assert r.status_code == 400 and "never sent twice" in r.json()["detail"]

    api.post(
        f"/api/v1/social/workspaces/{owner.workspace_id}/connect/mock",
        json={"platform": "facebook", "account_name": b["account_name"], "platform_account_id": b["platform_account_id"]},
        headers=owner.headers,
    )
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 200
    assert [j["id"] for j in r.json()["jobs"]] == [jobs[b["id"]]]    # only the failed one
    run_job(jobs[b["id"]])
    assert post_status(api, owner, post_id) == "published"
    assert jobs_by_account(api, owner, post_id)[a["id"]]["status"] == "completed"


def test_retry_unknown_outcome_needs_confirmation(api, db_exec):
    owner = register(api)
    acct = make_account(api, owner)
    post_id = approved_post(api, owner)
    jobs = publish_now(api, owner, post_id, [acct["id"]])
    # Simulate a worker crash mid-publish: outcome unknown.
    db_exec("UPDATE publish_jobs SET status = 'needs_review', error_message = 'Unknown outcome' WHERE id = :j", j=jobs[acct["id"]])
    db_exec("UPDATE posts SET status = 'failed' WHERE id = :p", p=post_id)

    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 409 and "confirm" in r.json()["detail"]

    r = api.post(f"/api/v1/posts/{post_id}/retry", json={"confirm_not_posted": True}, headers=owner.headers)
    assert r.status_code == 200 and r.json()["jobs"][0]["status"] == "queued"


def test_retry_refuses_content_changed_since_approval(api, db_exec):
    owner = register(api)
    post_id, _, _ = failed_post(api, owner)
    db_exec("UPDATE posts SET caption = 'tampered' WHERE id = :p", p=post_id)  # bypasses the API on purpose
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 409 and "approval" in r.json()["detail"]


def test_editing_failed_post_returns_it_to_draft(api):
    owner = register(api)
    post_id, _, _ = failed_post(api, owner)
    r = api.patch(f"/api/v1/posts/{post_id}", json={"caption": "Fixed caption"}, headers=owner.headers)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers).status_code == 400
    assert api.post(f"/api/v1/posts/{post_id}/submit", headers=owner.headers).status_code == 200


def test_retry_wrong_status_and_cross_tenant(api):
    owner = register(api)
    make_account(api, owner)
    post_id = approved_post(api, owner)
    r = api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=owner.headers)
    assert r.status_code == 400  # approved, not failed

    outsider = register(api, "Outsider")
    assert api.post(f"/api/v1/posts/{post_id}/retry", json={}, headers=outsider.headers).status_code == 404
