"""Generate a bcrypt hash for the admin password.

Usage: python -m scripts.create_admin_hash <password>
"""
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: create_admin_hash <password>")
    pw = sys.argv[1].encode()
    print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode())


if __name__ == "__main__":
    main()
