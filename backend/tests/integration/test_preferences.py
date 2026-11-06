"""Per-user theme preference — saved on the user, returned on every login."""
import pytest

from .conftest import register

pytestmark = pytest.mark.integration


def test_new_user_defaults_to_light(api):
    actor = register(api)
    assert api.get("/api/v1/auth/me", headers=actor.headers).json()["theme_preference"] == "light"


def test_theme_is_saved_and_returned_on_next_login(api):
    actor = register(api)
    r = api.patch("/api/v1/auth/me", json={"theme_preference": "dark"}, headers=actor.headers)
    assert r.status_code == 200, r.text
    assert r.json()["theme_preference"] == "dark"

    login = api.post("/api/v1/auth/login", json={"email": actor.email, "password": "password123"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["theme_preference"] == "dark"


def test_theme_change_is_audited_once(api, db_exec):
    actor = register(api)
    api.patch("/api/v1/auth/me", json={"theme_preference": "system"}, headers=actor.headers)
    api.patch("/api/v1/auth/me", json={"theme_preference": "system"}, headers=actor.headers)  # no change
    rows = db_exec(
        "SELECT changes FROM audit_events WHERE entity_type = 'User' AND entity_id = :id", id=actor.user_id
    ).all()
    assert [r[0] for r in rows] == [{"before": {"theme_preference": "light"}, "after": {"theme_preference": "system"}}]


def test_one_user_cannot_change_another_users_theme(api):
    alice, bob = register(api, "Alice"), register(api, "Bob")
    api.patch("/api/v1/auth/me", json={"theme_preference": "dark"}, headers=alice.headers)
    assert api.get("/api/v1/auth/me", headers=bob.headers).json()["theme_preference"] == "light"


def test_invalid_theme_and_anonymous_requests_are_rejected(api):
    actor = register(api)
    assert api.patch("/api/v1/auth/me", json={"theme_preference": "blue"}, headers=actor.headers).status_code == 422
    assert api.patch("/api/v1/auth/me", json={"theme_preference": None}, headers=actor.headers).status_code == 422
    assert api.patch("/api/v1/auth/me", json={"theme_preference": "dark"}).status_code == 401
