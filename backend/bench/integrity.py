"""After-run checks on app.db and on the client-side booking record. Every count must be 0."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .load import Bookings


def check(app_db: Path, bookings: Bookings) -> dict:
    con = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        tickets = q("SELECT count(*) FROM tickets")
        out = {
            # B5: at most one ticket per quote
            "double_bookings": q("SELECT count(*) FROM (SELECT quote_id FROM tickets GROUP BY quote_id "
                                 "HAVING count(*) > 1)"),
            # B7: booked <=> exactly one ticket
            "booked_without_ticket": q("SELECT count(*) FROM quotes q WHERE status = 'booked' AND NOT EXISTS "
                                       "(SELECT 1 FROM tickets t WHERE t.quote_id = q.id)"),
            "ticket_quote_not_booked": q("SELECT count(*) FROM tickets t JOIN quotes q ON q.id = t.quote_id "
                                         "WHERE q.status != 'booked'"),
            # B3: every ticket used the nonce of the card that was clicked
            "ticket_without_used_nonce": q(
                "SELECT count(*) FROM tickets t WHERE NOT EXISTS (SELECT 1 FROM quote_cards c WHERE "
                "c.quote_id = t.quote_id AND c.message_id = t.confirmed_message_id AND c.nonce_used_at IS NOT NULL)"),
            "ticket_owner_mismatch": q("SELECT count(*) FROM tickets t JOIN quotes q ON q.id = t.quote_id "
                                       "WHERE q.user_id != t.user_id"),
            # client view: a 200 for a quote <=> that quote has a ticket; never two 200s
            "client_double_success": sum(1 for n in bookings.ok.values() if n > 1),
            "client_success_without_ticket": 0,
            "ticket_without_client_success": 0,
            # turns of one conversation never interleave (conversation lock): no user message directly
            # follows a user message that has no assistant answer
            "turn_interleavings": _interleavings(con),
        }
        booked = {r[0] for r in con.execute("SELECT quote_id FROM tickets")}
        succeeded = {k for k, n in bookings.ok.items() if n}
        out["client_success_without_ticket"] = len(succeeded - booked)
        out["ticket_without_client_success"] = len(booked - succeeded)
        out["tickets"] = tickets
        return out
    finally:
        con.close()


def _interleavings(con: sqlite3.Connection) -> int:
    bad = 0
    convs = [r[0] for r in con.execute("SELECT id FROM conversations")]
    for conv in convs:
        roles = [r[0] for r in con.execute("SELECT role FROM messages WHERE conversation_id = ? ORDER BY seq",
                                           (conv,))]
        bad += sum(1 for a, b in zip(roles, roles[1:]) if a == "user" and b == "user")
    return bad


def failures(result: dict) -> int:
    return sum(v for k, v in result.items() if k != "tickets")
