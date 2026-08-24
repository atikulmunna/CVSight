from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, insert, select, update

from shelfsight_api.config import AuthUser, Role
from shelfsight_api.models import auth_events, auth_sessions

SESSION_LIFETIME = timedelta(hours=12)
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    role: Role
    session_id: UUID

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.username}"


def hash_password(password: str, salt: bytes | None = None) -> str:
    if not password or len(password) > 1024:
        raise ValueError("password must contain 1 to 1024 characters")
    effective_salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    if len(effective_salt) != SALT_BYTES:
        raise ValueError("password salt must be 16 bytes")
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=effective_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
    )
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{_encode(effective_salt)}:{_encode(derived)}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded.split(":")
        if (
            algorithm != "scrypt"
            or int(n) != SCRYPT_N
            or int(r) != SCRYPT_R
            or int(p) != SCRYPT_P
        ):
            return False
        salt = _decode(salt_text)
        expected = _decode(expected_text)
        if len(salt) != SALT_BYTES or len(expected) != KEY_BYTES:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=KEY_BYTES,
        )
    except (UnicodeEncodeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def create_session(
    connection: Connection,
    user: AuthUser,
    now: datetime | None = None,
) -> tuple[str, AuthenticatedUser]:
    created_at = datetime.now(UTC) if now is None else now
    token = secrets.token_urlsafe(32)
    row = connection.execute(
        insert(auth_sessions)
        .values(
            username=user.username,
            role=user.role,
            token_sha256=_token_hash(token),
            created_at=created_at,
            expires_at=created_at + SESSION_LIFETIME,
        )
        .returning(auth_sessions.c.id)
    ).one()
    return token, AuthenticatedUser(user.username, user.role, row.id)


def find_session(
    connection: Connection,
    token: str,
    configured_users: dict[str, AuthUser],
    now: datetime | None = None,
) -> AuthenticatedUser | None:
    checked_at = datetime.now(UTC) if now is None else now
    row = (
        connection.execute(
            select(
                auth_sessions.c.id,
                auth_sessions.c.username,
                auth_sessions.c.role,
            ).where(
                auth_sessions.c.token_sha256 == _token_hash(token),
                auth_sessions.c.revoked_at.is_(None),
                auth_sessions.c.expires_at > checked_at,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    configured = configured_users.get(str(row["username"]).casefold())
    if configured is None or configured.role != row["role"]:
        return None
    return AuthenticatedUser(configured.username, configured.role, row["id"])


def revoke_session(
    connection: Connection,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    revoked_at = datetime.now(UTC) if now is None else now
    connection.execute(
        update(auth_sessions)
        .where(auth_sessions.c.id == session_id, auth_sessions.c.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )


def record_auth_event(
    connection: Connection,
    action: str,
    request_path: str,
    user: AuthenticatedUser | AuthUser | None = None,
    username: str | None = None,
) -> None:
    connection.execute(
        insert(auth_events).values(
            username=user.username if user is not None else username,
            role=user.role if user is not None else None,
            action=action,
            request_path=request_path[:512],
        )
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


DUMMY_PASSWORD_HASH = hash_password("invalid-user-password", bytes(SALT_BYTES))
