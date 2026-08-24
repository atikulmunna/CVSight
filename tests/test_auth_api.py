from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from shelfsight_api.app import app
from shelfsight_api.auth_service import hash_password
from shelfsight_api.database import get_engine
from shelfsight_api.models import auth_events, auth_sessions

client = TestClient(app)


@pytest.fixture
def configured_auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    password = "local-test-password"
    users = [
        {"username": "owner35", "role": "owner", "password_hash": hash_password(password)},
        {
            "username": "annotator35",
            "role": "annotator",
            "password_hash": hash_password(password),
        },
        {
            "username": "reviewer35",
            "role": "reviewer",
            "password_hash": hash_password(password),
        },
    ]
    monkeypatch.setenv("SHELFSIGHT_AUTH_USERS", json.dumps(users))
    monkeypatch.setenv("SHELFSIGHT_SESSION_COOKIE_SECURE", "false")
    yield {"password": password}
    client.cookies.clear()


def test_every_application_route_requires_authentication(enforce_authentication: None) -> None:
    excluded = {"/api/health", "/api/health/database"}
    paths = app.openapi()["paths"]
    protected_routes = {
        path: operations
        for path, operations in paths.items()
        if path.startswith("/api/")
        and not path.startswith("/api/auth/")
        and path not in excluded
    }
    assert protected_routes

    for route_path, operations in protected_routes.items():
        path = _valid_path(route_path)
        for method in operations:
            response = client.request(method, path)
            assert response.status_code == 401, f"{method} {route_path} was not protected"
            assert response.json()["detail"]["code"] == "authentication_required"


def test_login_session_logout_and_audit(
    configured_auth: dict[str, str],
    enforce_authentication: None,
) -> None:
    client.cookies.clear()
    wrong = client.post(
        "/api/auth/login",
        json={"username": "owner35", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert configured_auth["password"] not in wrong.text

    login = client.post(
        "/api/auth/login",
        json={"username": "OWNER35", "password": configured_auth["password"]},
    )
    assert login.status_code == 200
    assert login.json() == {"username": "owner35", "role": "owner"}
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "local-test-password" not in cookie

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["role"] == "owner"

    with get_engine().connect() as connection:
        stored_session = connection.execute(
            select(auth_sessions)
            .where(auth_sessions.c.username == "owner35")
            .order_by(auth_sessions.c.created_at.desc())
        ).mappings().first()
        actions = connection.execute(
            select(auth_events.c.action).where(auth_events.c.username.in_(["owner35", "OWNER35"]))
        ).scalars().all()
    assert stored_session is not None
    assert len(stored_session["token_sha256"]) == 64
    assert "shelfsight_session" not in str(dict(stored_session))
    assert "login_failure" in actions
    assert "login_success" in actions

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/session").status_code == 401


@pytest.mark.parametrize(
    ("actor", "method", "path", "expected"),
    [
        ("annotator:test", "POST", "/api/datasets", 403),
        ("reviewer:test", "GET", "/api/jobs/11111111-1111-4111-8111-111111111111", 403),
        ("annotator:test", "GET", "/api/model-registry", 403),
        (
            "annotator:test",
            "GET",
            "/api/dataset-versions/11111111-1111-4111-8111-111111111111/review-queue",
            403,
        ),
        (
            "reviewer:test",
            "GET",
            "/api/dataset-versions/11111111-1111-4111-8111-111111111111/review-queue",
            404,
        ),
        ("annotator:test", "GET", "/api/annotations/11111111-1111-4111-8111-111111111111", 404),
        ("reviewer:test", "GET", "/api/skus", 200),
    ],
)
def test_role_boundaries(actor: str, method: str, path: str, expected: int) -> None:
    response = client.request(method, path, headers={"X-ShelfSight-Actor": actor})
    assert response.status_code == expected


def test_missing_auth_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    enforce_authentication: None,
) -> None:
    monkeypatch.delenv("SHELFSIGHT_AUTH_USERS", raising=False)
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "not-logged"},
    )

    assert response.status_code == 503
    assert "not-logged" not in response.text


def test_login_validation_does_not_reflect_password(enforce_authentication: None) -> None:
    password = "private-credential" * 100

    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": password},
    )

    assert response.status_code == 422
    assert password not in response.text
    assert "input" not in response.text


def test_authenticated_roles_cannot_be_overridden_by_actor_header(
    configured_auth: dict[str, str],
    enforce_authentication: None,
) -> None:
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": "annotator35", "password": configured_auth["password"]},
    )
    assert login.status_code == 200

    denied = client.post(
        "/api/datasets",
        headers={"X-ShelfSight-Actor": "owner:spoofed"},
        json={"name": "must-not-be-created"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "authorization_forbidden"

    assert client.post("/api/auth/logout").status_code == 204
    reviewer_login = client.post(
        "/api/auth/login",
        json={"username": "reviewer35", "password": configured_auth["password"]},
    )
    assert reviewer_login.status_code == 200
    reviewer_request = client.get(
        "/api/dataset-versions/11111111-1111-4111-8111-111111111111/review-queue"
    )
    assert reviewer_request.status_code == 404

    with get_engine().connect() as connection:
        denied_events = connection.execute(
            select(auth_events.c.action).where(
                auth_events.c.username == "annotator35",
                auth_events.c.action == "access_denied",
            )
        ).scalars().all()
    assert denied_events


def test_https_configuration_marks_session_cookie_secure(
    configured_auth: dict[str, str],
    enforce_authentication: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.cookies.clear()
    monkeypatch.setenv("SHELFSIGHT_SESSION_COOKIE_SECURE", "true")

    response = client.post(
        "/api/auth/login",
        json={"username": "owner35", "password": configured_auth["password"]},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def _valid_path(path: str) -> str:
    value = path
    replacements = {
        "dataset_id": "11111111-1111-4111-8111-111111111111",
        "dataset_version_id": "22222222-2222-4222-8222-222222222222",
        "image_id": "33333333-3333-4333-8333-333333333333",
        "annotation_id": "44444444-4444-4444-8444-444444444444",
        "suggestion_set_id": "55555555-5555-4555-8555-555555555555",
        "sku_id": "66666666-6666-4666-8666-666666666666",
        "reference_id": "77777777-7777-4777-8777-777777777777",
        "job_id": "88888888-8888-4888-8888-888888888888",
        "export_type": "detection",
        "variant": "canonical",
        "model_role": "known_sku_detector",
    }
    for name, replacement in replacements.items():
        value = value.replace(f"{{{name}}}", replacement)
    assert "{" not in value
    return value
