from __future__ import annotations

import json

import pytest

from shelfsight_api.config import ConfigurationError, get_auth_users, session_cookie_is_secure


def test_auth_users_require_unique_valid_entries() -> None:
    values = [
        {"username": "munna", "role": "owner", "password_hash": "scrypt:hash"},
        {"username": "review.one", "role": "reviewer", "password_hash": "scrypt:hash"},
    ]

    users = get_auth_users({"SHELFSIGHT_AUTH_USERS": json.dumps(values)})

    assert users["munna"].role == "owner"
    assert users["review.one"].username == "review.one"


@pytest.mark.parametrize(
    "values",
    [
        [],
        [{"username": "bad:name", "role": "owner", "password_hash": "hash"}],
        [{"username": "user", "role": "administrator", "password_hash": "hash"}],
        [{"username": "user", "role": "owner", "password": "plaintext"}],
        [
            {"username": "User", "role": "owner", "password_hash": "hash"},
            {"username": "user", "role": "reviewer", "password_hash": "hash"},
        ],
    ],
)
def test_auth_users_reject_invalid_configuration(values: object) -> None:
    with pytest.raises(ConfigurationError):
        get_auth_users({"SHELFSIGHT_AUTH_USERS": json.dumps(values)})


def test_cookie_secure_defaults_on_and_requires_boolean() -> None:
    assert session_cookie_is_secure({}) is True
    assert session_cookie_is_secure({"SHELFSIGHT_SESSION_COOKIE_SECURE": "false"}) is False
    with pytest.raises(ConfigurationError):
        session_cookie_is_secure({"SHELFSIGHT_SESSION_COOKIE_SECURE": "sometimes"})
