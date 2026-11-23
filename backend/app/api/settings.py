"""Runtime settings read from the environment (never committed; see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from ..agent.budget import AgentBudget
from .logins import parse_codes, parse_networks

DEFAULT_APP_DB = Path(__file__).resolve().parents[2] / "data" / "app.db"
SESSION_TTL_SECONDS = 7 * 24 * 3600


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_db: Path = DEFAULT_APP_DB
    dev_user: str = ""
    dev_password: str = field(default="", repr=False)
    cookie_secure: bool = False
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    nonce_secret: bytes | None = field(default=None, repr=False)
    google_client_id: str | None = None
    invite_codes: list[str] = field(default_factory=list, repr=False)
    trusted_proxies: tuple = field(default_factory=lambda: parse_networks(None))
    trust_cf_connecting_ip: bool = False  # only behind Cloudflare (review #4)
    login_attempts_per_minute: int = 10
    daily_messages_per_user: int = 50  # chat messages per user per UTC day (limits.py)
    daily_token_budget: int = 2_000_000  # site-wide sum of llm_calls.tokens per UTC day (limits.py)
    agent_budget: AgentBudget = field(default_factory=AgentBudget)  # per-turn agent budgets (AGENT_*)
    ping_interval: float = 2.0  # /api/debug/sse-ping spacing, seconds

    @property
    def dev_login(self) -> bool:
        return bool(self.dev_user and self.dev_password)

    @property
    def invite_login(self) -> bool:
        return bool(self.invite_codes)

    def warnings(self) -> list[str]:
        """Configuration mistakes worth a log line at startup (review #12)."""
        out = []
        if (self.google_client_id or self.invite_codes) and not self.cookie_secure:
            out.append("public logins are enabled but COOKIE_SECURE is off: the session cookie may be sent "
                       "over plain HTTP; set COOKIE_SECURE=1 behind HTTPS")
        return out

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        s = cls()
        if env.get("APP_DB"):
            s.app_db = Path(env["APP_DB"])
        s.dev_user = env.get("DEV_AUTH_USER", "")
        s.dev_password = env.get("DEV_AUTH_PASSWORD", "")
        s.cookie_secure = _flag(env.get("COOKIE_SECURE"))
        # Without SESSION_SECRET a restart signs everyone out (random key per process).
        if env.get("SESSION_SECRET"):
            s.session_secret = env["SESSION_SECRET"].encode()
        if env.get("NONCE_SECRET"):
            s.nonce_secret = env["NONCE_SECRET"].encode()
        s.google_client_id = (env.get("GOOGLE_CLIENT_ID") or "").strip() or None
        s.invite_codes = parse_codes(env.get("INVITE_CODES"))
        s.trusted_proxies = parse_networks(env.get("TRUSTED_PROXIES"))
        s.trust_cf_connecting_ip = _flag(env.get("TRUST_CF_CONNECTING_IP"))
        if env.get("DAILY_MESSAGES_PER_USER", "").strip().isdigit():
            s.daily_messages_per_user = int(env["DAILY_MESSAGES_PER_USER"])
        if env.get("DAILY_TOKEN_BUDGET", "").strip().replace("_", "").isdigit():
            s.daily_token_budget = int(env["DAILY_TOKEN_BUDGET"].strip().replace("_", ""))
        s.agent_budget = AgentBudget.from_env(env)  # ValueError on a bad AGENT_* value: fail at startup
        return s
