"""Stateless signed session cookie: `<user_id>.<expires>.<hmac>`. Logout clears the cookie."""
from __future__ import annotations

import hashlib
import hmac
import time

COOKIE = "oa_session"


def _sig(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:40]


def issue(secret: bytes, user_id: str, ttl: int, now: float | None = None) -> str:
    payload = f"{user_id}.{int((time.time() if now is None else now) + ttl)}"
    return f"{payload}.{_sig(secret, payload)}"


def verify(secret: bytes, value: str | None, now: float | None = None) -> str | None:
    """The user id in a valid, unexpired cookie, else None."""
    if not value or value.count(".") != 2:
        return None
    user_id, expires, sig = value.split(".")
    if not hmac.compare_digest(sig, _sig(secret, f"{user_id}.{expires}")):
        return None
    if not expires.isdigit() or int(expires) <= (time.time() if now is None else now):
        return None
    return user_id
