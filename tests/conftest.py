from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import Request
from sqlalchemy import Connection, Engine, create_engine

from shelfsight_api.app import app
from shelfsight_api.auth import get_current_user
from shelfsight_api.auth_service import AuthenticatedUser
from shelfsight_api.config import DATABASE_URL_ENV
from shelfsight_api.database import get_engine

TEST_DATABASE_URL_ENV = "SHELFSIGHT_TEST_DATABASE_URL"
TEST_SESSION_ID = UUID("00000000-0000-4000-8000-000000000035")


@pytest.fixture(autouse=True)
def authenticated_api_requests() -> Iterator[None]:
    def current_test_user(request: Request) -> AuthenticatedUser:
        actor = request.headers.get("X-ShelfSight-Actor", "owner:test")
        prefix, _, username = actor.partition(":")
        role = prefix if prefix in {"owner", "annotator", "reviewer"} else "owner"
        return AuthenticatedUser(
            username=username or actor,
            role=role,
            session_id=TEST_SESSION_ID,
        )

    app.dependency_overrides[get_current_user] = current_test_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def enforce_authentication() -> Iterator[None]:
    app.dependency_overrides.pop(get_current_user, None)
    yield


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def database_connection(database_engine: Engine) -> Iterator[Connection]:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def application_database(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    """Route every module's get_engine() at the test database for one test."""
    monkeypatch.setenv(DATABASE_URL_ENV, os.environ[TEST_DATABASE_URL_ENV])
    get_engine.cache_clear()
    yield database_engine
    get_engine.cache_clear()
