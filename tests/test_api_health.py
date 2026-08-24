from fastapi.testclient import TestClient

from shelfsight_api.app import app

client = TestClient(app)


def test_health_is_available_without_database() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "shelfsight-api"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_database_health_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("shelfsight_api.app.database_is_ready", lambda: False)

    response = client.get("/api/health/database")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "service": "postgresql"}


def test_database_health_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr("shelfsight_api.app.database_is_ready", lambda: True)

    response = client.get("/api/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "postgresql"}
