"""Code-template replies, used when the LLM's text cannot be sent (ReplyGuard problem, LLM error,
too many steps). Every value in them is copied from a tool result of this turn, so a template
always passes the ReplyGuard built from the same results (tested)."""
from __future__ import annotations

from typing import Iterable

from .tools import Outcome

NO_ANSWER = ("Sorry, I couldn't put together a reliable answer for that. "
             "Please try rephrasing your request.")
CLICK_TO_BOOK = "Nothing is booked until you click **Confirm booking** on the card."
MAX_TICKET_LINES = 5


def _join(names: Iterable[str], last: str = "and") -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {last} " + names[-1]


def _route(d: dict) -> str:
    how = "is a direct flight" if d.get("direct") else f"goes via {_join(d['transfers'])}"
    return (f"{d['origin']} to {d['destination']} {how}: "
            f"{d['distance_display']}, {d['price_display']}")


def _nearest(items: list[dict]) -> str:
    return _join(f"{n['city']} ({n['distance_display']} away)" for n in items)


def _no_service(city: str, nearest: list[dict]) -> str:
    text = f"Ocean Above has no flights to or from {city}."
    if nearest:
        text += f" The nearest cities with service: {_nearest(nearest)}."
    return text


# The query is not repeated: it is what the LLM passed to the tool, not a record (it could be
# "roughly 4500 km"). The card shows the user what was looked up.
def _unknown(query: str, suggestions: list[str]) -> str:
    text = "I couldn't find one of the cities you asked about."
    if suggestions:
        text += f" Did you mean {_join(suggestions, 'or')}?"
    return text


def _ambiguous(query: str, candidates: list[dict]) -> str:
    return (f"That name matches more than one city: {_join(c['city'] for c in candidates)}. "
            "Which one do you mean?")


def _sentences(o: Outcome) -> list[str]:
    d, code = o.payload, o.code
    if code in ("invalid_arguments", "unknown_tool", "tool_budget_exceeded"):
        return []
    if code == "tool_failed":
        return ["Flight data is temporarily unavailable. Please try again in a moment."]
    if code == "unknown_city" and "query" in d:
        return [_unknown(d["query"], d.get("suggestions", []))]
    if code == "ambiguous_city" and "candidates" in d:
        return [_ambiguous(d["query"], d["candidates"])]
    if code == "no_service" and "city" in d:
        return [_no_service(d["city"], d.get("nearest", []))]
    if "issues" in d:  # quote_route: one entry per endpoint that could not be used
        out = []
        for issue in d["issues"]:
            if issue["code"] == "unknown_city":
                out.append(_unknown(issue["query"], issue.get("suggestions", [])))
            elif issue["code"] == "ambiguous_city":
                out.append(_ambiguous(issue["query"], issue["candidates"]))
            elif issue["code"] == "no_service":
                out.append(_no_service(issue["city"], issue.get("nearest", [])))
        return out
    if code == "same_city":
        return ["The origin and the destination are the same city."]
    if code == "unreachable":
        return [f"There is no route between {_join(d['cities'])}."]

    if o.name == "find_city" and code == "ok":
        return [f"Ocean Above flies to {d['city']}."]
    if o.name == "quote_route" and code == "ok":
        return [f"The cheapest route from {_route(d)}."]
    if o.name == "request_booking":
        if code == "ok":
            return [f"Please check the confirmation card: {_route(d)}."]
        if code == "already_booked":
            return [f"That quote is already booked (ticket {d['ticket_id']})."]
        if code == "quote_expired":
            return ["That quote has expired. Please ask for a new quote."]
        if code == "stale":
            return ["Route data changed since that quote. Please ask for a new quote."]
        return ["I couldn't find that quote. Please ask for a new quote."]
    if o.name == "list_my_tickets":
        if code != "ok":
            return ["I couldn't read that date. Please give it as YYYY-MM-DD."]
        matching = " matching that filter" if d.get("filter") else ""
        if d["count"] == 0:
            return [f"You have no tickets{matching}."]
        head = f"Tickets{matching}: {d['count']}."
        if d["count"] > MAX_TICKET_LINES or len(d["tickets"]) < d["count"]:
            return [head + " They are listed on the card."]
        lines = [f"- {t['id']}: {t['origin']} to {t['destination']}, {t['distance_display']}, "
                 f"{t['price_display']}, booked {t['booked_at']}" for t in d["tickets"]]
        return [head + "\n" + "\n".join(lines)]
    return []


def fallback_reply(outcomes: Iterable[Outcome]) -> str:
    """A reply made only of this turn's tool results, in the order they were produced."""
    outcomes = list(outcomes)
    parts: list[str] = []
    for o in outcomes:
        for sentence in _sentences(o):
            if sentence not in parts:
                parts.append(sentence)
    substantial = [o for o in outcomes if not (o.name == "find_city" and o.code == "ok")]
    if len(parts) > 1 and substantial:  # "flies to X" adds nothing next to a quote or an error
        parts = [p for p in parts if not p.startswith("Ocean Above flies to ")] or parts
    if not parts:
        return NO_ANSWER
    if any(o.code == "ok" and o.name in ("quote_route", "request_booking") for o in outcomes):
        parts.append(CLICK_TO_BOOK)
    return "\n\n".join(parts)
