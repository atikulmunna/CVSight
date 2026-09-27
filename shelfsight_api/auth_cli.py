from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from shelfsight_api.auth_env_file import (
    ROLES,
    AccountError,
    add_user,
    load_users,
    remove_user,
    replace_users,
    set_password,
    write_env_file,
)
from shelfsight_api.auth_service import hash_password
from shelfsight_api.config import ConfigurationError


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print a CVSight password hash, or manage the users in an env file"
    )
    commands = parser.add_subparsers(dest="command")
    users = commands.add_parser("users", help="list, add, or remove users in an env file")
    users.add_argument("--env-file", type=Path, default=Path(".env"))
    actions = users.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    add = actions.add_parser("add")
    add.add_argument("username")
    add.add_argument("role", choices=ROLES)
    actions.add_parser("remove").add_argument("username")
    actions.add_parser("password").add_argument("username")
    arguments = parser.parse_args(argv)

    if arguments.command is None:
        print(hash_password(_new_password(parser)))
        return
    try:
        _manage_users(arguments, parser)
    except (AccountError, ConfigurationError, OSError) as error:
        parser.exit(1, f"error: {error}\n")


def _manage_users(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    path: Path = arguments.env_file
    text = path.read_text(encoding="utf-8")
    users = load_users(text)
    if arguments.action == "list":
        for user in users:
            print(f"{user['username']:<24} {user['role']}")
        return
    if arguments.action == "remove":
        users = remove_user(users, arguments.username)
        message = f"Removed {arguments.username}."
    else:
        if arguments.action == "add":
            users = add_user(users, arguments.username, arguments.role, "pending")
            message = f"Added {arguments.username} as {arguments.role}."
        else:
            users = set_password(users, arguments.username, "pending")
            message = f"Changed the password for {arguments.username}."
        # Check the name and the user limit before asking for a password.
        replace_users(text, users)
        users = set_password(users, arguments.username, hash_password(_new_password(parser)))
    write_env_file(path, replace_users(text, users))
    print(message)


def _new_password(parser: argparse.ArgumentParser) -> str:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    return password


if __name__ == "__main__":
    main()
