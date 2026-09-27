from __future__ import annotations

import json
from pathlib import Path

import pytest

from shelfsight_api import auth_cli
from shelfsight_api.auth_env_file import (
    AccountError,
    add_user,
    load_users,
    remove_user,
    replace_users,
    set_password,
)
from shelfsight_api.auth_service import password_matches
from shelfsight_api.config import ConfigurationError

OWNER = {"username": "owner", "role": "owner", "password_hash": "scrypt:owner-hash"}


def env_text(users: list[dict[str, str]], quote: str = "", ending: str = "\n") -> str:
    raw = json.dumps(users, separators=(",", ":"))
    return ending.join(
        [
            "SHELFSIGHT_DB_PASSWORD=secret",
            f"SHELFSIGHT_AUTH_USERS={quote}{raw}{quote}",
            "CVSIGHT_SITE_ADDRESS=localhost",
            "",
        ]
    )


@pytest.mark.parametrize("quote", ["", "'"], ids=["unquoted", "single-quoted"])
def test_changes_touch_only_the_users_line_and_keep_its_quoting(quote: str) -> None:
    text = env_text([OWNER], quote, ending="\r\n")

    users = add_user(load_users(text), "rahim", "annotator", "scrypt:rahim-hash")
    updated = replace_users(text, users)

    assert updated.splitlines()[0] == "SHELFSIGHT_DB_PASSWORD=secret"
    assert updated.splitlines()[2] == "CVSIGHT_SITE_ADDRESS=localhost"
    assert updated.splitlines()[1].startswith(f"SHELFSIGHT_AUTH_USERS={quote}[")
    assert updated.count("\r\n") == text.count("\r\n")
    assert [user["username"] for user in load_users(updated)] == ["owner", "rahim"]


def test_account_rules_protect_the_owner_and_usernames() -> None:
    users = [OWNER]

    with pytest.raises(AccountError, match="already exists"):
        add_user(users, "OWNER", "annotator", "scrypt:x")
    with pytest.raises(AccountError, match="role must be"):
        add_user(users, "rahim", "admin", "scrypt:x")
    with pytest.raises(AccountError, match="last owner"):
        remove_user(users, "owner")
    with pytest.raises(AccountError, match="does not exist"):
        set_password(users, "nobody", "scrypt:x")
    with pytest.raises(ConfigurationError, match="invalid username"):
        replace_users(env_text(users), add_user(users, "bad name", "annotator", "scrypt:x"))


def test_the_user_limit_is_enforced_before_writing() -> None:
    users = [OWNER] + [
        {"username": f"user{index}", "role": "annotator", "password_hash": "scrypt:x"}
        for index in range(19)
    ]

    with pytest.raises(ConfigurationError, match="1 to 20 users"):
        replace_users(env_text(users), add_user(users, "one-more", "annotator", "scrypt:x"))


def test_an_env_file_without_users_is_refused() -> None:
    with pytest.raises(AccountError, match="no SHELFSIGHT_AUTH_USERS line"):
        load_users("SHELFSIGHT_DB_PASSWORD=secret\n")


def test_cli_adds_a_user_with_a_working_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / ".env"
    path.write_text(env_text([OWNER]), encoding="utf-8")
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _prompt: "correct horse")

    auth_cli.main(["users", "--env-file", str(path), "add", "rahim", "annotator"])

    added = load_users(path.read_text(encoding="utf-8"))[-1]
    assert added["role"] == "annotator"
    assert password_matches("correct horse", added["password_hash"])
    assert "Added rahim as annotator." in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [path]


def test_cli_checks_the_username_before_asking_for_a_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    original = env_text([OWNER])
    path.write_text(original, encoding="utf-8")

    def refuse(_prompt: str) -> str:
        raise AssertionError("asked for a password for an invalid username")

    monkeypatch.setattr(auth_cli.getpass, "getpass", refuse)

    with pytest.raises(SystemExit):
        auth_cli.main(["users", "--env-file", str(path), "add", "bad name", "annotator"])
    with pytest.raises(SystemExit):
        auth_cli.main(["users", "--env-file", str(path), "password", "nobody"])
    assert path.read_text(encoding="utf-8") == original


def test_cli_refuses_mismatched_passwords_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    original = env_text([OWNER])
    path.write_text(original, encoding="utf-8")
    answers = iter(["first", "second"])
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _prompt: next(answers))

    with pytest.raises(SystemExit):
        auth_cli.main(["users", "--env-file", str(path), "add", "rahim", "reviewer"])
    assert path.read_text(encoding="utf-8") == original
