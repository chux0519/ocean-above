"""app.db: the business database (users, conversations, messages, call logs, quotes, tickets).

Plain stdlib sqlite3, no ORM. Every operation opens its own connection, so a `Store` can be shared
between threads. Writes run inside `BEGIN IMMEDIATE` (`Store.write()`): the write lock is taken
before the first read, so a check-then-write sequence cannot interleave with another writer.

Times are ISO 8601 UTC strings with second precision (`2026-11-23T08:15:00Z`); sub-seconds are
truncated when a time is stored. The clock is injectable.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

Clock = Callable[[], datetime]

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    auth_provider TEXT NOT NULL,
    provider_sub  TEXT NOT NULL,
    email         TEXT,
    display_name  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (auth_provider, provider_sub)
);
CREATE TABLE conversations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX conversations_by_user ON conversations(user_id, updated_at);
CREATE TABLE messages (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,   -- insertion order within the database
    id              TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text            TEXT NOT NULL,
    cards           TEXT NOT NULL DEFAULT '[]',          -- json; quote cards without live state
    guard_problems  TEXT,                                -- json or NULL
    created_at      TEXT NOT NULL
);
CREATE INDEX messages_by_conversation ON messages(conversation_id, seq);
CREATE TABLE llm_calls (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    request         TEXT NOT NULL,                       -- json
    response        TEXT NOT NULL,                       -- json
    latency_ms      INTEGER,
    tokens          INTEGER,
    model           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX llm_calls_by_conversation ON llm_calls(conversation_id, seq);
CREATE TABLE tool_calls (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    message_id TEXT NOT NULL REFERENCES messages(id),    -- the user message that triggered the turn
    name       TEXT NOT NULL,
    args       TEXT NOT NULL,                            -- json
    result     TEXT,                                     -- json
    error      TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX tool_calls_by_message ON tool_calls(message_id, seq);
CREATE TABLE quotes (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id),
    user_id             TEXT NOT NULL REFERENCES users(id),
    origin              TEXT NOT NULL,
    destination         TEXT NOT NULL,
    legs                TEXT NOT NULL,                   -- json: from, to, distance, coords, source
    distance            TEXT NOT NULL,                   -- km, 30 dp decimal string
    price               TEXT NOT NULL,                   -- USD, 30 dp decimal string
    network_fingerprint TEXT NOT NULL,
    status              TEXT NOT NULL
                        CHECK (status IN ('quoted', 'presented', 'booked', 'expired', 'stale')),
    status_reason       TEXT,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    presented_at        TEXT,
    closed_at           TEXT                             -- when it became booked / expired / stale
);
CREATE INDEX quotes_by_conversation ON quotes(conversation_id);
-- One row per (quote, message) whose card was sent to the user with `done`. The one-time
-- confirmation nonce of that card is derived from (quote, user, message); it is never stored.
CREATE TABLE quote_cards (
    quote_id      TEXT NOT NULL REFERENCES quotes(id),
    message_id    TEXT NOT NULL REFERENCES messages(id),
    presented_at  TEXT NOT NULL,
    nonce_used_at TEXT,
    PRIMARY KEY (quote_id, message_id)
);
CREATE TABLE tickets (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users(id),
    quote_id             TEXT NOT NULL UNIQUE REFERENCES quotes(id),   -- B5: one ticket per quote
    origin               TEXT NOT NULL,
    destination          TEXT NOT NULL,
    legs_snapshot        TEXT NOT NULL,                  -- json, same form as quotes.legs
    distance             TEXT NOT NULL,
    price                TEXT NOT NULL,
    network_fingerprint  TEXT NOT NULL,                  -- locations.db verified at booking time
    confirmed_message_id TEXT NOT NULL REFERENCES messages(id),        -- the card that was clicked
    booked_at            TEXT NOT NULL
);
CREATE INDEX tickets_by_user ON tickets(user_id, booked_at);

-- Q3: booked / expired / stale never change again, whatever the application code does.
CREATE TRIGGER quotes_terminal_status BEFORE UPDATE OF status ON quotes
WHEN OLD.status IN ('booked', 'expired', 'stale') AND NEW.status <> OLD.status
BEGIN SELECT RAISE(ABORT, 'quote status is terminal'); END;
-- B2/B7: a ticket can only be written for a quote that is `presented` in the same transaction.
CREATE TRIGGER tickets_need_presented_quote BEFORE INSERT ON tickets
WHEN (SELECT status FROM quotes WHERE id = NEW.quote_id) IS NOT 'presented'
BEGIN SELECT RAISE(ABORT, 'ticket needs a presented quote'); END;
CREATE TRIGGER tickets_are_immutable BEFORE UPDATE ON tickets
BEGIN SELECT RAISE(ABORT, 'tickets are immutable'); END;
"""

# v2 (L2 usage limits): one row per chat message the limits let through, written in the same
# transaction as the count that allowed it, so two racing requests cannot both take the last slot.
_SCHEMA_V2 = """
CREATE TABLE message_usage (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    user_id         TEXT NOT NULL REFERENCES users(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    created_at      TEXT NOT NULL
);
CREATE INDEX message_usage_by_user ON message_usage(user_id, created_at);
CREATE INDEX llm_calls_by_time ON llm_calls(created_at);
"""

_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """Stored / wire form of a time. Sub-seconds are dropped (floor)."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime; times must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def day_start(moment: datetime) -> datetime:
    """00:00 UTC of the moment's UTC day (usage limits count per UTC day)."""
    utc = moment.astimezone(timezone.utc)
    return datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    """Opaque id with a type prefix (`u_`, `c_`, `m_`, `q_`, `t_`, ...)."""
    return f"{prefix}_{secrets.token_hex(8)}"


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class NotFound(Exception):
    """The object does not exist or belongs to another user (the two are not distinguished)."""

    code = "not_found"
    status = 404


class Store:
    def __init__(self, path: str | Path, clock: Clock = utc_now):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.execute("PRAGMA journal_mode = WAL")  # readers are not blocked by a writer
            self._migrate(con)
        finally:
            con.close()

    def now(self) -> str:
        return iso(self.clock())

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: no implicit transactions; `write()` issues BEGIN IMMEDIATE itself.
        con = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _migrate(self, con: sqlite3.Connection) -> None:
        con.execute("BEGIN IMMEDIATE")
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} has schema {version}, this code knows {SCHEMA_VERSION}")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in _split_statements(_MIGRATIONS[target]):
                    con.execute(statement)
                con.execute(f"PRAGMA user_version = {target}")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A connection for reads; several statements see one snapshot."""
        con = self._connect()
        try:
            con.execute("BEGIN")
            try:
                yield con
            finally:
                con.execute("ROLLBACK")
        finally:
            con.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One `BEGIN IMMEDIATE` transaction: commit on normal exit, roll back on any exception."""
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                if con.in_transaction:  # sqlite rolls back by itself on some errors (disk full)
                    con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

    # Every method below that takes a conversation or message id also takes the user and checks
    # ownership in the same transaction; NotFound when it is missing or someone else's.
    # --- convenience wrappers: one transaction each (see records.py for the composable forms) ---
    def upsert_user(self, auth_provider: str, provider_sub: str, display_name: str,
                    email: str | None = None) -> dict:
        from . import records

        with self.write() as con:
            return records.upsert_user(con, self.now(), auth_provider, provider_sub, display_name, email)

    def get_user(self, user_id: str) -> dict | None:
        from . import records

        with self.read() as con:
            return records.get_user(con, user_id)

    def create_conversation(self, user_id: str, title: str = "") -> dict:
        from . import records

        with self.write() as con:
            return records.create_conversation(con, self.now(), user_id, title)

    def get_conversation(self, user_id: str, conversation_id: str) -> dict | None:
        """The conversation summary, or None when it does not exist or belongs to someone else."""
        from . import records

        with self.read() as con:
            return records.get_conversation(con, user_id, conversation_id)

    def list_conversations(self, user_id: str) -> list[dict]:
        from . import records

        with self.read() as con:
            return records.list_conversations(con, user_id)

    def set_conversation_title(self, user_id: str, conversation_id: str, title: str) -> bool:
        from . import records

        with self.write() as con:
            return records.set_conversation_title(con, user_id, conversation_id, title)

    def first_user_text(self, user_id: str, conversation_id: str) -> str:
        """The first non-blank user message of a conversation the user owns ("" when none)."""
        from . import records

        with self.read() as con:
            records.require_conversation(con, user_id, conversation_id)
            return records.first_user_text(con, conversation_id)

    def add_message(self, user_id: str, conversation_id: str, role: str, text: str,
                    cards: list | tuple = (), guard_problems: Any = None) -> dict:
        """Store a message as is. Assistant messages that carry quote or booking-request cards
        must go through `Airline.post_assistant_message`, which also presents those quotes."""
        from . import records

        with self.write() as con:
            records.require_conversation(con, user_id, conversation_id)
            return records.add_message(con, self.now(), conversation_id, role, text, cards, guard_problems)

    def list_messages(self, user_id: str, conversation_id: str) -> list[dict]:
        """Raw messages, oldest first. Quote cards carry no live state here; for the contract
        `Message` form use `Airline.messages`."""
        from . import records

        with self.read() as con:
            records.require_conversation(con, user_id, conversation_id)
            return records.list_messages(con, conversation_id)

    def add_llm_call(self, user_id: str, conversation_id: str, request: Any, response: Any, *,
                     latency_ms: int | None = None, tokens: int | None = None,
                     model: str | None = None) -> str:
        from . import records

        with self.write() as con:
            records.require_conversation(con, user_id, conversation_id)
            return records.add_llm_call(con, self.now(), conversation_id, request, response,
                                        latency_ms, tokens, model)

    def add_tool_call(self, user_id: str, message_id: str, name: str, args: Any, result: Any = None, *,
                      error: str | None = None, latency_ms: int | None = None) -> str:
        from . import records

        with self.write() as con:
            records.require_message(con, user_id, message_id)
            return records.add_tool_call(con, self.now(), message_id, name, args, result, error, latency_ms)

    def list_llm_calls(self, user_id: str, conversation_id: str) -> list[dict]:
        from . import records

        with self.read() as con:
            records.require_conversation(con, user_id, conversation_id)
            return records.list_llm_calls(con, conversation_id)

    def list_tool_calls(self, user_id: str, message_id: str) -> list[dict]:
        from . import records

        with self.read() as con:
            records.require_message(con, user_id, message_id)
            return records.list_tool_calls(con, message_id)

    # --- usage limits (L2) ---
    def reserve_message(self, user_id: str, conversation_id: str, *, daily_limit: int,
                        token_budget: int) -> tuple[str | None, str | None]:
        """Count and reserve in one `BEGIN IMMEDIATE` transaction: `(reservation_id, None)` when the
        message may go to the assistant, else `(None, scope)` with scope "user" (the user's messages
        today reached `daily_limit`) or "site" (today's `llm_calls.tokens` reached `token_budget`);
        nothing is written then. Today = the UTC day of the store clock."""
        from . import records

        now = self.clock()
        since = iso(day_start(now))
        with self.write() as con:
            records.require_conversation(con, user_id, conversation_id)
            if records.messages_since(con, user_id, since) >= daily_limit:
                return None, "user"
            if records.tokens_since(con, since) >= token_budget:
                return None, "site"
            return records.add_message_usage(con, iso(now), user_id, conversation_id), None

    def release_message(self, user_id: str, reservation_id: str) -> None:
        """Undo a reservation whose message was refused before anything was stored (bad text)."""
        from . import records

        with self.write() as con:
            records.delete_message_usage(con, user_id, reservation_id)

    def messages_today(self, user_id: str) -> int:
        from . import records

        with self.read() as con:
            return records.messages_since(con, user_id, iso(day_start(self.clock())))

    def tokens_today(self) -> int:
        from . import records

        with self.read() as con:
            return records.tokens_since(con, iso(day_start(self.clock())))


def _split_statements(script: str) -> list[str]:
    """Split a schema script into statements (executescript would commit our transaction).
    Trigger bodies contain `;`, so a statement ends only where sqlite says it is complete."""
    out: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError("incomplete statement at the end of the schema script")
    return out
