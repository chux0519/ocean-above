"""Runtime settings read from the environment (never committed; see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_APP_DB = Path(__file__).resolve().parents[2] / "data" / "app.db"
SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class Settings:
    app_db: Path = DEFAULT_APP_DB
    dev_user: str = ""
    dev_password: str = ""
    cookie_secure: bool = False
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    nonce_secret: bytes | None = None
    google_client_id: str | None = None
    ping_interval: float = 2.0  # /api/debug/sse-ping spacing, seconds

    @property
    def dev_login(self) -> bool:
        return bool(self.dev_user and self.dev_password)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        s = cls()
        if env.get("APP_DB"):
            s.app_db = Path(env["APP_DB"])
        s.dev_user = env.get("DEV_AUTH_USER", "")
        s.dev_password = env.get("DEV_AUTH_PASSWORD", "")
        s.cookie_secure = env.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")
        # Without SESSION_SECRET a restart signs everyone out (random key per process).
        if env.get("SESSION_SECRET"):
            s.session_secret = env["SESSION_SECRET"].encode()
        if env.get("NONCE_SECRET"):
            s.nonce_secret = env["NONCE_SECRET"].encode()
        s.google_client_id = env.get("GOOGLE_CLIENT_ID") or None
        return s
