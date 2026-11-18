"""Password hashing and HMAC-signed auth tokens (no external deps)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .config import settings


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or base64.urlsafe_b64encode(hashlib.sha256(str(time.time_ns()).encode()).digest())[:16].decode()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_token(user_id: int, role: str) -> str:
    payload = {"uid": user_id, "role": role, "exp": int(time.time()) + settings.token_ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def content_hash(obj) -> str:
    """Stable sha256 over a JSON-serialisable object — used for reproducibility refs."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
