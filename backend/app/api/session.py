"""Signed session cookie: `<user_id>.<epoch>.<expires>.<hmac>`.

The signature covers user, epoch and expiry. `epoch` is the user's `session_epoch` at sign-in;
logout bumps it in app.db, so every cookie issued before is rejected (review #7). Whether the
user and its login method still exist is checked by `routes.current_user`.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

COOKIE = "oa_session"


@dataclass(frozen=True)
class Session:
    user_id: str
    epoch: int


def _sig(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:40]


def issue(secret: bytes, user_id: str, ttl: int, now: float | None = None, epoch: int = 0) -> str:
    payload = f"{user_id}.{int(epoch)}.{int((time.time() if now is None else now) + ttl)}"
    return f"{payload}.{_sig(secret, payload)}"


def verify(secret: bytes, value: str | None, now: float | None = None) -> Session | None:
    """User id and epoch of a valid, unexpired cookie, else None. Never raises on odd input
    (review #8: a non-ASCII cookie used to raise TypeError in compare_digest → 500)."""
    if not value or not value.isascii() or value.count(".") != 3:
        return None
    user_id, epoch, expires, sig = value.split(".")
    if not hmac.compare_digest(sig.encode(), _sig(secret, f"{user_id}.{epoch}.{expires}").encode()):
        return None
    if not (epoch.isdigit() and expires.isdigit()) or int(expires) <= (time.time() if now is None else now):
        return None
    return Session(user_id, int(epoch))
