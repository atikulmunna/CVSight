"""Read and change the user list in the SHELFSIGHT_AUTH_USERS line of an env file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from shelfsight_api.config import AUTH_USERS_ENV, get_auth_users

ROLES = ("owner", "annotator", "reviewer")
UserEntry = dict[str, str]


class AccountError(ValueError):
    """Raised when an account change is not allowed."""


def load_users(text: str) -> list[UserEntry]:
    raw, _ = _users_value(text)
    # The same validation the API applies at startup, so a bad file is caught here first.
    get_auth_users({AUTH_USERS_ENV: raw})
    users: list[UserEntry] = json.loads(raw)
    return users


def add_user(
    users: list[UserEntry], username: str, role: str, password_hash: str
) -> list[UserEntry]:
    if role not in ROLES:
        raise AccountError(f"role must be one of {', '.join(ROLES)}")
    if _find(users, username) is not None:
        raise AccountError(f"{username} already exists")
    return [*users, {"username": username, "role": role, "password_hash": password_hash}]


def remove_user(users: list[UserEntry], username: str) -> list[UserEntry]:
    target = _require(users, username)
    remaining = [user for user in users if user is not target]
    if not any(user["role"] == "owner" for user in remaining):
        raise AccountError("the last owner cannot be removed")
    return remaining


def set_password(users: list[UserEntry], username: str, password_hash: str) -> list[UserEntry]:
    target = _require(users, username)
    return [{**user, "password_hash": password_hash} if user is target else user for user in users]


def replace_users(text: str, users: list[UserEntry]) -> str:
    raw = json.dumps(users, separators=(",", ":"))
    get_auth_users({AUTH_USERS_ENV: raw})
    _, quote = _users_value(text)
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(f"{AUTH_USERS_ENV}="):
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = f"{AUTH_USERS_ENV}={quote}{raw}{quote}{ending}"
    return "".join(lines)


def write_env_file(path: Path, text: str) -> None:
    """Replace the file atomically, keeping its mode and owner.

    The tool can run as root inside a container with the host directory mounted, so a
    plain write would leave the host user with a file they cannot read.
    """
    status = path.stat()
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(temporary, status.st_mode)
        if hasattr(os, "chown"):
            try:
                os.chown(temporary, status.st_uid, status.st_gid)
            except OSError:
                pass  # Some mounted file systems do not support ownership; the mode still holds.
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _users_value(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        if line.startswith(f"{AUTH_USERS_ENV}="):
            value = line.split("=", 1)[1].strip()
            quote = (
                value[0] if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0] else ""
            )
            return (value[1:-1] if quote else value), quote
    raise AccountError(f"the env file has no {AUTH_USERS_ENV} line")


def _find(users: list[UserEntry], username: str) -> UserEntry | None:
    return next(
        (user for user in users if user["username"].casefold() == username.casefold()), None
    )


def _require(users: list[UserEntry], username: str) -> UserEntry:
    user = _find(users, username)
    if user is None:
        raise AccountError(f"{username} does not exist")
    return user
