#!/usr/bin/env python3
"""Generate a base64 token for a given user id.

Usage:
  AUTH_KEY_BASE64=<base64-key> python scripts/generate_auth_token.py 12345678

The script expects an environment variable `AUTH_KEY_BASE64` with a base64-urlsafe-encoded AES key (16/24/32 bytes).
"""
import os
import sys
import argparse
import base64
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from pathlib import Path

# Load .env from repo root and create AUTH_KEY_BASE64 if missing
_repo_root = Path(__file__).resolve().parents[1]
_env_path = _repo_root / ".env"
load_dotenv(dotenv_path=_env_path)

def _ensure_and_set_auth_key(env_path: Path):
    if os.getenv("AUTH_KEY_BASE64"):
        return
    key = secrets.token_bytes(32)
    key_b64 = base64.urlsafe_b64encode(key).decode()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with env_path.open("a", encoding="utf-8") as f:
        f.write("\n# Auto-generated AUTH_KEY_BASE64\n")
        f.write(f"AUTH_KEY_BASE64={key_b64}\n")
    os.environ["AUTH_KEY_BASE64"] = key_b64

_ensure_and_set_auth_key(_env_path)



def _get_auth_key() -> bytes:
    key_b64 = os.getenv("AUTH_KEY_BASE64")
    if not key_b64:
        sys.exit("AUTH_KEY_BASE64 env var is required")
    try:
        key = base64.urlsafe_b64decode(key_b64)
    except Exception:
        sys.exit("AUTH_KEY_BASE64 is not valid base64")
    if len(key) not in (16, 24, 32):
        sys.exit("AUTH_KEY_BASE64 must decode to 16/24/32 bytes")
    return key


def generate_token(user_id: str) -> str:
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(_get_auth_key())
    pt = f"bookbot:{user_id}".encode()
    ct = aesgcm.encrypt(nonce, pt, None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate auth token for bookbot")
    parser.add_argument("user_id", help="Telegram user id to bind the token to")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only print token")
    args = parser.parse_args()

    token = generate_token(args.user_id)
    if args.quiet:
        print(token)
    else:
        print(f"Token for user {args.user_id}:\n{token}")
