from __future__ import annotations

import argparse
import getpass

from shelfsight_api.auth_service import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a ShelfSight password hash")
    parser.parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    print(hash_password(password))


if __name__ == "__main__":
    main()
