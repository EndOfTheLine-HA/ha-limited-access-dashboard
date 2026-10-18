from __future__ import annotations

import argparse
import getpass
import sys

from .config import ConfigError, load_settings
from .security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(prog="ha-guest-portal")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hash-password", help="Generate an Argon2 password hash")
    check = subparsers.add_parser("check-config", help="Validate configuration and exit")
    check.add_argument("path", nargs="?", default="config.yml")
    args = parser.parse_args()

    if args.command == "hash-password":
        first = getpass.getpass("Password (12+ characters): ")
        second = getpass.getpass("Confirm password: ")
        if first != second:
            print("Passwords do not match", file=sys.stderr)
            raise SystemExit(2)
        try:
            print(hash_password(first))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        return

    try:
        settings = load_settings(args.path)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        f"Valid: {len(settings.accounts)} accounts, "
        f"{len(settings.profiles)} profiles, {len(settings.actions)} actions"
    )


if __name__ == "__main__":
    main()
