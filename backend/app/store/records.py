"""Row-level operations on app.db. Every function takes an open connection, so several of them
compose inside one `Store.write()` transaction. Rows are returned as plain dicts with json columns
already decoded. `now` is always the caller's stored-form time string (`Store.now()`)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .db import NotFound, new_id, to_json

PREVIEW_CHARS = 120

_USER_COLS = "id, auth_provider, provider_sub, email, display_name, created_at, session_epoch"
_MESSAGE_COLS = "id, conversation_id, role, text, cards, guard_problems, created_at, status"
_QUOTE_COLS = (
    "id, conversation_id, user_id, origin, destination, legs, distance, price, network_fingerprint, "
    "status, status_reason, created_at, expires_at, presented_at, closed_at"
)
_TICKET_COLS = (
    "id, user_id, quote_id, origin, destination, legs_snapshot, distance, price, "
    "network_fingerprint, confirmed_message_id, booked_at"
)


def _row(row: sqlite3.Row | None, json_cols: Iterable[str] = ()) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for col in json_cols:
        if out[col] is not None:
            out[col] = json.loads(out[col])
    return out


# --- users ---------------------------------------------------------------------------------
def upsert_user(con, now: str, auth_provider: str, provider_sub: str, display_name: str,
                email: str | None = None) -> dict:
    """The user for (auth_provider, provider_sub); created on first sight, name/email refreshed."""
    con.execute(
        "INSERT INTO users (id, auth_provider, provider_sub, email, display_name, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (auth_provider, provider_sub) DO UPDATE SET "
        "email = excluded.email, display_name = excluded.display_name",
        (new_id("u"), auth_provider, provider_sub, email, display_name, now),
    )
    row = con.execute(
        f"SELECT {_USER_COLS} FROM users WHERE auth_provider = ? AND provider_sub = ?",
        (auth_provider, provider_sub),
    ).fetchone()
    return _row(row)


def get_user(con, user_id: str) -> dict | None:
    return _row(con.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)).fetchone())


def bump_session_epoch(con, user_id: str) -> None:
    con.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,))


# --- conversations -------------------------------------------------------------------------
_CONVERSATION_SELECT = (
    "SELECT c.id, c.title, c.created_at, c.updated_at, "
    "COALESCE((SELECT substr(m.text, 1, ?) FROM messages m WHERE m.conversation_id = c.id "
    "          AND m.text <> '' ORDER BY m.seq DESC LIMIT 1), '') AS preview "
    "FROM conversations c "
)


def create_conversation(con, now: str, user_id: str, title: str = "") -> dict:
    cid = new_id("c")
    con.execute(
        "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (cid, user_id, title, now, now),
    )
    return {"id": cid, "title": title, "created_at": now, "updated_at": now, "preview": ""}


def get_conversation(con, user_id: str, conversation_id: str) -> dict | None:
    """Contract `ConversationSummary`, or None when missing or owned by someone else."""
    row = con.execute(
        _CONVERSATION_SELECT + "WHERE c.id = ? AND c.user_id = ?",
        (PREVIEW_CHARS, conversation_id, user_id),
    ).fetchone()
    return _row(row)


def list_conversations(con, user_id: str) -> list[dict]:
    """Contract `ConversationSummary` list, most recently updated first."""
    rows = con.execute(
        _CONVERSATION_SELECT + "WHERE c.user_id = ? ORDER BY c.updated_at DESC, c.rowid DESC",
        (PREVIEW_CHARS, user_id),
    ).fetchall()
    return [_row(r) for r in rows]


def set_conversation_title(con, user_id: str, conversation_id: str, title: str) -> bool:
    cur = con.execute(
        "UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
        (title, conversation_id, user_id),
    )
    return cur.rowcount == 1


def first_user_text(con, conversation_id: str) -> str:
    """Text of the conversation's first non-blank user message, or "" (title fallback)."""
    row = con.execute(
        "SELECT text FROM messages WHERE conversation_id = ? AND role = 'user' AND trim(text) <> '' "
        "ORDER BY seq LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return "" if row is None else row["text"]


def conversation_owner(con, conversation_id: str) -> str | None:
    row = con.execute("SELECT user_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return None if row is None else row["user_id"]


def require_conversation(con, user_id: str, conversation_id: str) -> None:
    if conversation_owner(con, conversation_id) != user_id:
        raise NotFound(conversation_id)


def require_message(con, user_id: str, message_id: str) -> None:
    row = con.execute(
        "SELECT c.user_id FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ?",
        (message_id,),
    ).fetchone()
    if row is None or row["user_id"] != user_id:
        raise NotFound(message_id)


# --- messages ------------------------------------------------------------------------------
def add_message(con, now: str, conversation_id: str, role: str, text: str,
                cards: Iterable[dict] = (), guard_problems: Any = None, *, status: str = "completed") -> dict:
    """`status`: "completed", or "cancelled" for an assistant reply the user stopped (#48)."""
    if status not in ("completed", "cancelled"):
        raise ValueError(f"message status must be completed or cancelled, got {status!r}")
    mid = new_id("m")
    cards = list(cards)
    con.execute(
        "INSERT INTO messages (id, conversation_id, role, text, cards, guard_problems, created_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, conversation_id, role, text, to_json(cards),
         None if guard_problems is None else to_json(guard_problems), now, status),
    )
    con.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return {"id": mid, "conversation_id": conversation_id, "role": role, "text": text,
            "cards": cards, "guard_problems": guard_problems, "created_at": now, "status": status}


def list_messages(con, conversation_id: str) -> list[dict]:
    rows = con.execute(
        f"SELECT {_MESSAGE_COLS} FROM messages WHERE conversation_id = ? ORDER BY seq",
        (conversation_id,),
    ).fetchall()
    return [_row(r, ("cards", "guard_problems")) for r in rows]


# --- call logs -----------------------------------------------------------------------------
def add_llm_call(con, now: str, conversation_id: str, request: Any, response: Any,
                 latency_ms: int | None, tokens: int | None, model: str | None) -> str:
    lid = new_id("l")
    con.execute(
        "INSERT INTO llm_calls (id, conversation_id, request, response, latency_ms, tokens, model, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (lid, conversation_id, to_json(request), to_json(response), latency_ms, tokens, model, now),
    )
    return lid


def list_llm_calls(con, conversation_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT id, conversation_id, request, response, latency_ms, tokens, model, created_at "
        "FROM llm_calls WHERE conversation_id = ? ORDER BY seq",
        (conversation_id,),
    ).fetchall()
    return [_row(r, ("request", "response")) for r in rows]


def add_tool_call(con, now: str, message_id: str, name: str, args: Any, result: Any,
                  error: str | None, latency_ms: int | None) -> str:
    tid = new_id("tc")
    con.execute(
        "INSERT INTO tool_calls (id, message_id, name, args, result, error, latency_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, message_id, name, to_json(args), None if result is None else to_json(result),
         error, latency_ms, now),
    )
    return tid


def list_tool_calls(con, message_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT id, message_id, name, args, result, error, latency_ms, created_at "
        "FROM tool_calls WHERE message_id = ? ORDER BY seq",
        (message_id,),
    ).fetchall()
    return [_row(r, ("args", "result")) for r in rows]


# --- usage limits ------------------------------------------------------------------------
def add_message_usage(con, now: str, user_id: str, conversation_id: str) -> str:
    uid = new_id("mu")
    con.execute("INSERT INTO message_usage (id, user_id, conversation_id, created_at) VALUES (?, ?, ?, ?)",
                (uid, user_id, conversation_id, now))
    return uid


def delete_message_usage(con, user_id: str, usage_id: str) -> None:
    con.execute("DELETE FROM message_usage WHERE id = ? AND user_id = ?", (usage_id, user_id))


def messages_since(con, user_id: str, since: str) -> int:
    """Chat messages the user sent (let through the limits) at or after `since`."""
    return con.execute("SELECT COUNT(*) FROM message_usage WHERE user_id = ? AND created_at >= ?",
                       (user_id, since)).fetchone()[0]


# A call the upstream reported no usage for (stream error, cancel, a server without `usage`) still
# cost something (review #10): count it as its request size in tokens (≈ 3 chars per token) plus a
# full output allowance. Over-counting is the safe side of a budget.
CHARS_PER_TOKEN_ESTIMATE = 3
OUTPUT_TOKENS_ESTIMATE = 4096


def tokens_since(con, since: str) -> int:
    """Site-wide LLM tokens (`llm_calls.tokens`, all users) at or after `since`; a NULL count is
    replaced by an estimate (request chars / 3 + 4096)."""
    return con.execute(
        "SELECT COALESCE(SUM(COALESCE(tokens, length(request) / ? + ?)), 0) FROM llm_calls "
        "WHERE created_at >= ?",
        (CHARS_PER_TOKEN_ESTIMATE, OUTPUT_TOKENS_ESTIMATE, since)).fetchone()[0]


# --- quotes --------------------------------------------------------------------------------
def insert_quote(con, *, now: str, expires_at: str, conversation_id: str, user_id: str,
                 origin: str, destination: str, legs: list[dict], distance: str, price: str,
                 network_fingerprint: str) -> dict:
    qid = new_id("q")
    con.execute(
        "INSERT INTO quotes (id, conversation_id, user_id, origin, destination, legs, distance, "
        "price, network_fingerprint, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'quoted', ?, ?)",
        (qid, conversation_id, user_id, origin, destination, to_json(legs), distance, price,
         network_fingerprint, now, expires_at),
    )
    return get_quote(con, qid)


def get_quote(con, quote_id: str) -> dict | None:
    row = con.execute(f"SELECT {_QUOTE_COLS} FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    return _row(row, ("legs",))


def set_quote_status(con, quote: dict, status: str, now: str, reason: str | None = None) -> None:
    """Move a quote to `status` and update the dict in place. `presented` stamps presented_at;
    the terminal states stamp closed_at. The row must still hold the status the caller read."""
    column = "presented_at" if status == "presented" else "closed_at"
    cur = con.execute(
        f"UPDATE quotes SET status = ?, status_reason = ?, {column} = ? WHERE id = ? AND status = ?",
        (status, reason, now, quote["id"], quote["status"]),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"quote {quote['id']} changed underneath us")
    quote.update({"status": status, "status_reason": reason, column: now})


def add_quote_card(con, quote_id: str, message_id: str, now: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO quote_cards (quote_id, message_id, presented_at) VALUES (?, ?, ?)",
        (quote_id, message_id, now),
    )


def quote_cards(con, quote_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT quote_id, message_id, presented_at, nonce_used_at FROM quote_cards "
        "WHERE quote_id = ? ORDER BY rowid",
        (quote_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_nonce_used(con, quote_id: str, message_id: str, now: str) -> bool:
    """True when this call consumed the nonce; False when it had been used before."""
    cur = con.execute(
        "UPDATE quote_cards SET nonce_used_at = ? "
        "WHERE quote_id = ? AND message_id = ? AND nonce_used_at IS NULL",
        (now, quote_id, message_id),
    )
    return cur.rowcount == 1


# --- tickets -------------------------------------------------------------------------------
def insert_ticket(con, *, now: str, quote: dict, network_fingerprint: str,
                  confirmed_message_id: str) -> dict:
    tid = new_id("t")
    con.execute(
        "INSERT INTO tickets (id, user_id, quote_id, origin, destination, legs_snapshot, distance, "
        "price, network_fingerprint, confirmed_message_id, booked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, quote["user_id"], quote["id"], quote["origin"], quote["destination"],
         to_json(quote["legs"]), quote["distance"], quote["price"], network_fingerprint,
         confirmed_message_id, now),
    )
    return get_ticket_for_quote(con, quote["id"])


def get_ticket_for_quote(con, quote_id: str) -> dict | None:
    row = con.execute(f"SELECT {_TICKET_COLS} FROM tickets WHERE quote_id = ?", (quote_id,)).fetchone()
    return _row(row, ("legs_snapshot",))


def list_tickets(con, user_id: str) -> list[dict]:
    """The user's tickets, newest first."""
    rows = con.execute(
        f"SELECT {_TICKET_COLS} FROM tickets WHERE user_id = ? ORDER BY booked_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [_row(r, ("legs_snapshot",)) for r in rows]
