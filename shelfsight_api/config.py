from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DATABASE_URL_ENV = "SHELFSIGHT_DATABASE_URL"
MEDIA_ROOT_ENV = "SHELFSIGHT_MEDIA_ROOT"
IMPORT_ROOT_ENV = "SHELFSIGHT_IMPORT_ROOT"
AUTH_USERS_ENV = "SHELFSIGHT_AUTH_USERS"
SESSION_COOKIE_SECURE_ENV = "SHELFSIGHT_SESSION_COOKIE_SECURE"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
Role = Literal["owner", "annotator", "reviewer"]


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: Role
    password_hash: str


class ConfigurationError(ValueError):
    """Raised when required application configuration is invalid."""


def get_database_url(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get(DATABASE_URL_ENV, "").strip()
    if not value:
        raise ConfigurationError(f"{DATABASE_URL_ENV} is required")
    try:
        url = make_url(value)
    except ArgumentError as error:
        raise ConfigurationError(f"{DATABASE_URL_ENV} is invalid") from error
    if url.get_backend_name() != "postgresql":
        raise ConfigurationError(f"{DATABASE_URL_ENV} must use PostgreSQL")
    return value


def get_media_root(environ: Mapping[str, str] | None = None) -> Path:
    return _get_directory(MEDIA_ROOT_ENV, environ)


def get_import_root(environ: Mapping[str, str] | None = None) -> Path:
    return _get_directory(IMPORT_ROOT_ENV, environ)


def get_auth_users(environ: Mapping[str, str] | None = None) -> dict[str, AuthUser]:
    source = os.environ if environ is None else environ
    raw = source.get(AUTH_USERS_ENV, "").strip()
    if not raw:
        raise ConfigurationError(f"{AUTH_USERS_ENV} is required")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{AUTH_USERS_ENV} must be valid JSON") from error
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ConfigurationError(f"{AUTH_USERS_ENV} must contain 1 to 20 users")

    users: dict[str, AuthUser] = {}
    for value in values:
        user = _parse_auth_user(value)
        key = user.username.casefold()
        if key in users:
            raise ConfigurationError(f"{AUTH_USERS_ENV} contains duplicate usernames")
        users[key] = user
    return users


def session_cookie_is_secure(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    value = source.get(SESSION_COOKIE_SECURE_ENV, "true").strip().lower()
    if value not in {"true", "false"}:
        raise ConfigurationError(f"{SESSION_COOKIE_SECURE_ENV} must be true or false")
    return value == "true"


def _parse_auth_user(value: object) -> AuthUser:
    if not isinstance(value, dict) or set(value) != {"username", "role", "password_hash"}:
        raise ConfigurationError(
            f"each {AUTH_USERS_ENV} entry must contain username, role, and password_hash"
        )
    username = value["username"]
    role = value["role"]
    password_hash = value["password_hash"]
    if not isinstance(username, str) or USERNAME_PATTERN.fullmatch(username) is None:
        raise ConfigurationError(f"{AUTH_USERS_ENV} contains an invalid username")
    if role not in {"owner", "annotator", "reviewer"}:
        raise ConfigurationError(f"{AUTH_USERS_ENV} contains an invalid role")
    if not isinstance(password_hash, str) or len(password_hash) > 512:
        raise ConfigurationError(f"{AUTH_USERS_ENV} contains an invalid password hash")
    return AuthUser(username=username, role=role, password_hash=password_hash)


def _get_directory(
    variable_name: str,
    environ: Mapping[str, str] | None,
) -> Path:
    source = os.environ if environ is None else environ
    value = source.get(variable_name, "").strip()
    if not value:
        raise ConfigurationError(f"{variable_name} is required")
    directory = Path(value).resolve()
    if directory == Path(directory.anchor):
        raise ConfigurationError(f"{variable_name} cannot be a filesystem root")
    return directory
