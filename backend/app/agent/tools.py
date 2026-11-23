"""The four tools the LLM can call: schemas, argument validation, dispatch to the airline.

Arguments are names and ids only - never a distance or a price - so the LLM cannot pass a number
it made up. Results go back to the LLM as display values; errors are structured records too.

There is no booking tool. `Toolbox` keeps only the four bound methods it dispatches to, not the
`Airline` object, so nothing in this module can reach `Airline.book` (design P4, rule M04).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

MAX_ARG_CHARS = 200
MAX_RAW_ARGUMENTS = 2000  # the whole JSON of one call
MAX_TICKETS_FOR_LLM = 20  # the card lists them all; `count` is always the full number


def _tool(name: str, description: str, properties: dict[str, str], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in properties.items()},
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _tool("find_city",
          "Look up one city: whether Ocean Above flies there, and if not, the nearest cities that "
          "have service. Use it when the user asks about a single city.",
          {"name": "City name as the user wrote it."}, ["name"]),
    _tool("quote_route",
          "Find the cheapest route between two cities and create a quote (legs, transfer cities, "
          "distance, price). Also reports unknown, ambiguous or unserved cities, with the nearest "
          "served cities. Shows a route card to the user.",
          {"origin": "Departure city as the user wrote it.",
           "destination": "Arrival city as the user wrote it."},
          ["origin", "destination"]),
    _tool("request_booking",
          "Show the user a confirmation card for an existing quote. Call it when the user wants to "
          "book a quoted route. It does NOT book: the ticket is issued only when the user clicks "
          "Confirm booking on the card.",
          {"quote_id": "The quote_id from a quote_route result (starts with q_)."}, ["quote_id"]),
    _tool("list_my_tickets",
          "List the tickets the user has booked, with the count. All filters are optional.",
          {"city": "Only tickets whose origin, destination or a transfer is this city.",
           "ticket_id": "Only this ticket (starts with t_).",
           "booked_after": "ISO date or datetime (UTC); tickets booked at or after it.",
           "booked_before": "ISO date or datetime (UTC); tickets booked before it."},
          []),
]
STATUS_TEXT = {
    "find_city": "Looking up the city…",
    "quote_route": "Looking up routes…",
    "request_booking": "Preparing your confirmation…",
    "list_my_tickets": "Looking up your tickets…",
}
DEFAULT_STATUS = "Working on it…"
_SPECS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOL_SCHEMAS}


@dataclass(frozen=True)
class Outcome:
    """One executed (or refused) tool call."""

    name: str
    args: Any  # parsed arguments, or the raw string when they were not valid JSON
    code: str
    payload: dict  # the JSON the LLM gets: {"code": ..., display values, names, ids}
    cards: tuple[dict, ...] = ()
    error: str | None = None  # set for calls that did not run or crashed
    latency_ms: int = 0

    def for_llm(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))


def slim(value: Any) -> Any:
    """Drop the 30-decimal `distance` / `price` strings where a display value sits next to them:
    the LLM only ever sees display values."""
    if isinstance(value, dict):
        return {k: slim(v) for k, v in value.items()
                if not (k in ("distance", "price") and f"{k}_display" in value)}
    if isinstance(value, (list, tuple)):
        return [slim(v) for v in value]
    return value


def _mark_nearest(item: Any) -> None:
    """On an unserved city with its `nearest` list (nearest first, as the airline ranked it): name
    the single nearest served city in `nearest_served_city`, so "use the nearest city" needs no
    comparison by the LLM. Left out when the first two are equally near at display precision
    (then the user has to choose)."""
    if not isinstance(item, dict):
        return
    near = item.get("nearest")
    if not isinstance(near, list) or not near or not all(isinstance(n, dict) for n in near):
        return
    if len(near) > 1 and near[0].get("distance_display") == near[1].get("distance_display"):
        return
    if isinstance(near[0].get("city"), str):
        item["nearest_served_city"] = near[0]["city"]


def _payload(code: str, data: dict) -> dict:
    out = {"code": code, **slim(data)}
    if code == "no_service":
        _mark_nearest(out)  # find_city
        for issue in out.get("issues") or ():  # quote_route
            _mark_nearest(issue)
    if isinstance(out.get("tickets"), list) and len(out["tickets"]) > MAX_TICKETS_FOR_LLM:
        out["tickets"] = out["tickets"][:MAX_TICKETS_FOR_LLM]  # newest first
        out["tickets_shown"] = MAX_TICKETS_FOR_LLM
    if isinstance(out.get("legs"), list) and isinstance(out.get("transfers"), list):
        # counted here so the LLM never counts (FSR: self-reported counts go wrong)
        out["leg_count"] = len(out["legs"])
        out["transfer_count"] = len(out["transfers"])
    return out


def validate(name: str, args: Any) -> tuple[dict | None, str | None]:
    """(clean arguments, None) or (None, what is wrong)."""
    spec = _SPECS[name]
    if not isinstance(args, dict):
        return None, "arguments must be a JSON object"
    unknown = sorted(set(args) - set(spec["properties"]))
    if unknown:
        return None, f"unknown argument(s): {', '.join(unknown)}; allowed: {', '.join(spec['properties'])}"
    clean: dict[str, str] = {}
    for key in spec["properties"]:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip() and key not in spec["required"]):
            continue
        if not isinstance(value, str):
            return None, f"{key} must be a string"
        value = value.strip()
        if not value:
            return None, f"{key} must not be empty"
        if len(value) > MAX_ARG_CHARS:
            return None, f"{key} is too long"
        clean[key] = value
    missing = [k for k in spec["required"] if k not in clean]
    if missing:
        return None, f"missing argument(s): {', '.join(missing)}"
    return clean, None


class Toolbox:
    def __init__(self, airline: Any):
        find_city, quote_route = airline.find_city, airline.quote_route
        request_booking, list_tickets = airline.request_booking, airline.list_tickets
        self._run: dict[str, Callable[[str, str, dict], Any]] = {
            "find_city": lambda user, conv, a: find_city(a["name"]),
            "quote_route": lambda user, conv, a: quote_route(user, conv, a["origin"], a["destination"]),
            "request_booking": lambda user, conv, a: request_booking(user, conv, a["quote_id"]),
            "list_my_tickets": lambda user, conv, a: list_tickets(user, **a),
        }

    def run(self, user_id: str, conversation_id: str, name: str, raw_arguments: str) -> Outcome:
        """Validate and execute one tool call. Never raises: every failure is an Outcome the LLM
        can read and correct."""
        started = time.monotonic()

        def refused(code: str, message: str, args: Any, **extra: Any) -> Outcome:
            return Outcome(name, args, code, {"code": code, "message": message, **extra},
                           error=message, latency_ms=int((time.monotonic() - started) * 1000))

        if name not in self._run:
            return refused("unknown_tool", f"there is no tool named {name!r}", raw_arguments,
                           available=sorted(self._run))
        if not isinstance(raw_arguments, str):
            raw_arguments = ""
        if len(raw_arguments) > MAX_RAW_ARGUMENTS:
            return refused("invalid_arguments", "arguments are too long", raw_arguments[:MAX_RAW_ARGUMENTS])
        try:
            args = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except (ValueError, RecursionError):
            return refused("invalid_arguments", "arguments are not valid JSON", raw_arguments)
        clean, problem = validate(name, args)
        if clean is None:
            return refused("invalid_arguments", problem or "invalid arguments", args)
        try:
            result = self._run[name](user_id, conversation_id, clean)
        except Exception as exc:  # NetworkDataError (locations.db unreadable), NotFound, bugs
            failed = refused("tool_failed", "the tool could not run; flight data may be unavailable", clean)
            return replace(failed, error=f"{type(exc).__name__}: {exc}"[:500])
        return Outcome(name, clean, result.code, _payload(result.code, result.data),
                       tuple(result.cards), latency_ms=int((time.monotonic() - started) * 1000))

