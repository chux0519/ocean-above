"""Daily usage limits (L2, contract v1.2): messages per user and the site's LLM token budget.

Checked before the message stream starts. The count and the reservation are one `BEGIN IMMEDIATE`
transaction (`Store.reserve_message`), so two messages racing for the last slot cannot both pass.
A refused message stores nothing. Booking, tickets and history are never limited.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..store import Store, day_start, iso
from .errors import ApiError
from .settings import Settings


def resets_at(now: datetime) -> str:
    """Next 00:00 UTC (wire form)."""
    return iso(day_start(now) + timedelta(days=1))


def usage_view(store: Store, settings: Settings, user_id: str) -> dict:
    """The contract's `Usage` for /api/me."""
    return {"messages_today": store.messages_today(user_id), "daily_limit": settings.daily_messages_per_user,
            "resets_at": resets_at(store.clock())}


def reserve_message(store: Store, settings: Settings, user_id: str, conversation_id: str) -> str:
    """Take one of today's message slots or raise 429 `rate_limited` (scope user / site).
    Returns the reservation id (to release it if the message is then refused as invalid)."""
    reservation, scope = store.reserve_message(
        user_id, conversation_id, daily_limit=settings.daily_messages_per_user,
        token_budget=settings.daily_token_budget)
    if reservation is not None:
        return reservation
    when = resets_at(store.clock())
    if scope == "user":
        message = (f"You have used today's {settings.daily_messages_per_user} messages. "
                   "The limit resets at 00:00 UTC.")
    else:
        message = ("The assistant has reached today's usage budget for this site. "
                   "It resets at 00:00 UTC. Your bookings and tickets are still available.")
    raise ApiError(429, "rate_limited", message, extra={"scope": scope, "resets_at": when})
