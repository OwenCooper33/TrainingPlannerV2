from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 120000)
    return salt_bytes.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, candidate = hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, hash_hex)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry(days: int = 14) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
