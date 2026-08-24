from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, select

from shelfsight_api.auth_service import (
    SESSION_LIFETIME,
    create_session,
    find_session,
    hash_password,
    password_matches,
    revoke_session,
)
from shelfsight_api.config import AuthUser
from shelfsight_api.models import auth_sessions


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert password_matches("correct horse battery staple", first)
    assert not password_matches("wrong", first)
    assert not password_matches("password", "invalid-format")


def test_session_stores_only_token_hash_and_honors_expiry_and_revocation(
    database_connection: Connection,
) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    configured = AuthUser("worker", "annotator", hash_password("password"))
    token, user = create_session(database_connection, configured, now)
    row = database_connection.execute(
        select(auth_sessions).where(auth_sessions.c.id == user.session_id)
    ).mappings().one()

    assert token not in str(dict(row))
    assert len(row["token_sha256"]) == 64
    assert find_session(database_connection, token, {"worker": configured}, now) == user
    assert (
        find_session(
            database_connection,
            token,
            {"worker": configured},
            now + SESSION_LIFETIME + timedelta(seconds=1),
        )
        is None
    )

    revoke_session(database_connection, user.session_id, now + timedelta(minutes=1))
    assert find_session(database_connection, token, {"worker": configured}, now) is None


def test_session_stops_when_user_role_changes(database_connection: Connection) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    configured = AuthUser("reviewer", "reviewer", hash_password("password"))
    token, _ = create_session(database_connection, configured, now)

    changed = AuthUser("reviewer", "owner", configured.password_hash)

    assert find_session(database_connection, token, {"reviewer": changed}, now) is None
