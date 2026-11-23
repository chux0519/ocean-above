"""Card objects exactly as in docs/api/contract.md, built only from stored records.

Display values come from the core formatters applied to the stored high-precision strings; nothing
here computes a distance or a price.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping

from ..core.pricing import fmt_km, fmt_usd

TRACE_NOTE = (
    "Price is computed from the unrounded distance, so displayed distance × 0.13 can differ from "
    "the displayed price by one cent."
)
MESSAGES = {
    "same_city": "Origin and destination are the same city.",
    "quote_expired": "This quote has expired. Please ask for a new quote.",
    "stale": "Route data changed since this quote. Please ask for a new quote.",
}


def leg(stored: Mapping) -> dict:
    return {
        "from": stored["from"],
        "to": stored["to"],
        "distance_display": fmt_km(stored["distance"]),
        "distance": stored["distance"],
    }


def transfers(legs: list[Mapping]) -> list[str]:
    return [l["to"] for l in legs[:-1]]


def card_state(status: str, nonce: str | None = None, ticket_id: str | None = None) -> dict:
    """Contract `CardState`. A card is bookable exactly when it carries a nonce."""
    return {"status": status, "bookable": nonce is not None, "nonce": nonce, "ticket_id": ticket_id}


def quote_card(quote: Mapping, state: dict, kind: str = "quote") -> dict:
    """`QuoteCard`, or `BookingRequestCard` with kind="booking_request" (same fields)."""
    return {
        "type": kind,
        "id": quote["id"],
        "origin": quote["origin"],
        "destination": quote["destination"],
        "legs": [leg(l) for l in quote["legs"]],
        "transfers": transfers(quote["legs"]),
        "distance_display": fmt_km(quote["distance"]),
        "price_display": fmt_usd(quote["price"]),
        "distance": quote["distance"],
        "price": quote["price"],
        "expires_at": quote["expires_at"],
        "state": state,
    }


def ticket_card(ticket: Mapping) -> dict:
    legs = ticket["legs_snapshot"]
    return {
        "type": "ticket",
        "id": ticket["id"],
        "quote_id": ticket["quote_id"],
        "origin": ticket["origin"],
        "destination": ticket["destination"],
        "legs": [leg(l) for l in legs],
        "transfers": transfers(legs),
        "distance_display": fmt_km(ticket["distance"]),
        "price_display": fmt_usd(ticket["price"]),
        "booked_at": ticket["booked_at"],
    }


def ticket_list_card(tickets: Iterable[Mapping], filter_: Mapping[str, str]) -> dict:
    cards = [ticket_card(t) for t in tickets]
    return {"type": "ticket_list", "count": len(cards), "filter": dict(filter_), "tickets": cards}


def nearest(items: Iterable) -> list[dict]:
    """`places.Nearby` records -> [{city, distance_display}], order kept (nearest first)."""
    return [{"city": n.city, "distance_display": fmt_km(n.distance)} for n in items]


def no_service_card(city: str, nearest_items: list[dict]) -> dict:
    return {"type": "no_service", "city": city, "nearest": nearest_items}


def ambiguous_city_card(query: str, candidates: list[dict]) -> dict:
    return {"type": "ambiguous_city", "query": query, "candidates": candidates}


def unknown_city_card(query: str, suggestions: Iterable[str]) -> dict:
    return {"type": "unknown_city", "query": query, "suggestions": list(suggestions)}


def notice_card(code: str, message: str | None = None) -> dict:
    return {"type": "notice", "code": code, "message": message or MESSAGES[code]}


def _number(text: str) -> int | float:
    """A stored coordinate as a JSON number. locations.db holds integers; a fractional coordinate
    is shown as a float (display only, comparisons use the stored string)."""
    value = Decimal(text)
    return int(value) if value == value.to_integral_value() else float(value)


def trace(quote: Mapping, formula: str, algorithm: str) -> dict:
    return {
        "quote_id": quote["id"],
        "formula": formula,
        "algorithm": algorithm,
        "network_fingerprint": quote["network_fingerprint"],
        "legs": [
            {
                **leg(l),
                "from_coord": [_number(v) for v in l["from_coord"]],
                "to_coord": [_number(v) for v in l["to_coord"]],
                "source": l["source"],
            }
            for l in quote["legs"]
        ],
        "distance": quote["distance"],
        "price": quote["price"],
        "distance_display": fmt_km(quote["distance"]),
        "price_display": fmt_usd(quote["price"]),
        "note": TRACE_NOTE,
    }
