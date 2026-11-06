def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_security_headers(client):
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_request_id(client):
    assert len(client.get("/api/v1/health").headers["X-Request-ID"]) == 32
    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert echoed.headers["X-Request-ID"] == "trace-123"


def test_protected_route_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_refresh_requires_csrf(client):
    # No CSRF cookie/header pair → 403 before anything else.
    assert client.post("/api/v1/auth/refresh").status_code == 403


def test_workspace_routes_require_auth(client):
    assert client.get("/api/v1/workspaces").status_code == 401
