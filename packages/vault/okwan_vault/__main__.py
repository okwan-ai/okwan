"""Vault CLI. `python -m okwan_vault keygen` prints a new master key."""
import base64
import sys

from .crypto import new_key

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "keygen":
        print(base64.urlsafe_b64encode(new_key()).decode())
    else:
        print("usage: python -m okwan_vault keygen", file=sys.stderr)
        sys.exit(1)
