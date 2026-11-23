"""The airline service: everything the airline side can do, behind one interface.

The agent's tools (`find_city`, `quote_route`, `request_booking`, `list_tickets`) and the HTTP
layer (`book`, `trace`, `tickets`, `messages`) both go through `Airline`. It calls the
deterministic core for every route, distance and price and keeps quotes and tickets in app.db.

Booking (design §6, docs/design/state_invariants.md B1-B7) has exactly one entry point,
`Airline.book(user_id, quote_id, nonce)`. A nonce exists only for a card that was stored with an
assistant message by `post_assistant_message` (the `done` moment), is derived from
(quote, user, message) with a server secret and is never part of a tool result, so nothing the
LLM sees or says can book.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from ..core import places
from ..core.network import Network, NetworkDataError, NetworkProvider
from ..core.pricing import FORMULA, InvalidPathError, price_path
from ..core.routing import ALGORITHM, Route, RouteError, cheapest_route
from ..store import NotFound, Store, iso, parse_iso, records
from . import cards
from .locks import ConversationBusy, ConversationLocks

QUOTE_TTL = timedelta(minutes=15)
BOOK_LOCK_TIMEOUT = 5.0  # seconds a click waits for a running chat turn of its conversation
MIN_SECRET_BYTES = 16
OPEN = ("quoted", "presented")  # the non-terminal quote states
_QUOTE_CARD_TYPES = ("quote", "booking_request")
_FILTER_KEYS = ("city", "ticket_id", "booked_after", "booked_before")


@dataclass(frozen=True)
class Result:
    """What a tool call produced.

    `code` is "ok" or a snake_case error code. `data` is the JSON-safe record for the LLM: names,
    ids and display values only, never a nonce. `cards` are contract `Card` objects for the UI;
    quote and booking-request cards in a Result are never bookable (state.nonce is null) - they
    become bookable when `post_assistant_message` stores them.
    """

    code: str
    data: dict = field(default_factory=dict)
    cards: tuple[dict, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code == "ok"


class BookingError(Exception):
    """A refused booking. `code` / `status` are the contract's booking errors."""

    _TEXT = {
        "not_found": (404, "Quote not found."),
        "invalid_nonce": (409, "This confirmation is not valid. Reload the conversation and try again."),
        "not_presented": (409, "This quote has not been shown to you yet."),
        "expired": (409, cards.MESSAGES["quote_expired"]),
        "already_booked": (409, "This quote is already booked."),
        "stale": (409, cards.MESSAGES["stale"]),
        "conversation_busy": (409, "A reply is still being generated. Try again when it has finished."),
        "data_unavailable": (503, "Route data could not be read right now. Please try again."),
    }

    def __init__(self, code: str, *, ticket_id: str | None = None, reason: str | None = None):
        self.code = code
        self.status, self.message = self._TEXT[code]
        self.ticket_id = ticket_id  # already_booked: the existing ticket
        self.reason = reason  # internal detail (stale, data_unavailable), not for the user
        super().__init__(f"{code}: {self.message}")

    def as_response(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class Airline:
    def __init__(self, store: Store, networks: NetworkProvider, *,
                 locks: ConversationLocks | None = None, nonce_secret: bytes | None = None,
                 quote_ttl: timedelta = QUOTE_TTL):
        """`nonce_secret`: key for the confirmation nonces. Default: random per process, which
        means a restart invalidates the nonces held by open pages (reloading gets working ones).
        Time comes from `store.clock`."""
        self.store = store
        self.networks = networks
        self.locks = locks or ConversationLocks()
        self.quote_ttl = quote_ttl
        if nonce_secret is None:
            nonce_secret = secrets.token_bytes(32)
        if not isinstance(nonce_secret, bytes) or len(nonce_secret) < MIN_SECRET_BYTES:
            raise ValueError(f"nonce_secret must be bytes, at least {MIN_SECRET_BYTES} long")
        self._secret = nonce_secret

    # ================================================================== tools (chat turn) ====
    def find_city(self, name: str) -> Result:
        """Resolve a city name. Codes: ok, unknown_city, ambiguous_city, no_service."""
        net = self.networks.current()
        res = places.resolve(net, name)
        if res.status == "unknown":
            return Result("unknown_city", {"query": res.query, "suggestions": list(res.candidates)},
                          (cards.unknown_city_card(res.query, res.candidates),))
        if res.status == "ambiguous":
            candidates = [_candidate(net, c) for c in res.candidates]
            return Result("ambiguous_city", {"query": res.query, "candidates": candidates},
                          (cards.ambiguous_city_card(res.query, candidates),))
        city = net.cities[res.city]
        data: dict[str, Any] = {
            "city": city.name,
            "served": net.is_served(city.name),
            "is_hub": city.is_hub,
            "connected_hubs": _connected_hubs(net, city.name),
            "similar_names": list(res.similar_names),
        }
        if data["served"]:
            return Result("ok", data)
        near = cards.nearest(places.nearest_served(net, city.name, k=3))
        data["nearest"] = near
        return Result("no_service", data, (cards.no_service_card(city.name, near),))

    def quote_route(self, user_id: str, conversation_id: str, origin: str, destination: str) -> Result:
        """Cheapest route between two cities, stored as an immutable quote (status `quoted`).
        Codes: ok, unknown_city, ambiguous_city, same_city, no_service, unreachable.
        Raises NotFound when the conversation is not the user's."""
        net = self.networks.current()
        route = cheapest_route(net, origin, destination)
        if isinstance(route, RouteError):
            with self.store.read() as con:
                self._require_conversation(con, user_id, conversation_id)
            return _route_error(net, route)

        legs = [
            {
                "from": leg.origin,
                "to": leg.destination,
                "distance": leg.distance,
                "from_coord": _coord(net, leg.origin),
                "to_coord": _coord(net, leg.destination),
                "source": _source(net, leg.origin, leg.destination),
            }
            for leg in route.legs
        ]
        with self.store.write() as con:
            now = _floor(self.store.clock())  # read after the write lock is ours
            self._require_conversation(con, user_id, conversation_id)
            quote = records.insert_quote(
                con, now=iso(now), expires_at=iso(now + self.quote_ttl),
                conversation_id=conversation_id, user_id=user_id,
                origin=route.origin, destination=route.destination, legs=legs,
                distance=route.distance, price=route.price, network_fingerprint=route.fingerprint,
            )
        data = _quote_data(quote, "quoted")
        if route.similar_names:
            data["similar_names"] = {k: list(v) for k, v in route.similar_names.items()}
        return Result("ok", data, (cards.quote_card(quote, cards.card_state("quoted"),
                                                      similar_names=_similar(self.networks, quote)),))

    def request_booking(self, user_id: str, conversation_id: str, quote_id: str) -> Result:
        """Ask for the confirm card of a quote. It never books and never returns a nonce.
        Codes: ok (booking_request card), not_found, quote_expired, stale, already_booked
        (with the ticket card)."""
        with self.store.write() as con:
            now = self.store.clock()  # after the write lock is ours, so the expiry check is current
            quote = records.get_quote(con, quote_id)
            if quote is None or quote["user_id"] != user_id or quote["conversation_id"] != conversation_id:
                return Result("not_found", {"quote_id": quote_id})
            status = self._settle(con, quote, now)
            if status == "booked":
                ticket = records.get_ticket_for_quote(con, quote_id)
                card = cards.ticket_card(ticket)
                return Result("already_booked", {"quote_id": quote_id, "ticket_id": ticket["id"]}, (card,))
            if status == "expired":
                return Result("quote_expired", {"quote_id": quote_id}, (cards.notice_card("quote_expired"),))
            if status == "stale":
                return Result("stale", {"quote_id": quote_id}, (cards.notice_card("stale"),))
            card = cards.quote_card(quote, cards.card_state(status), kind="booking_request",
                                    similar_names=_similar(self.networks, quote))
            return Result("ok", _quote_data(quote, status), (card,))

    def list_tickets(self, user_id: str, *, city: str | None = None, ticket_id: str | None = None,
                     booked_after: str | None = None, booked_before: str | None = None) -> Result:
        """The user's tickets, newest first, as a `ticket_list` card; `count` is computed here.

        Filters (all optional, combined with AND): `city` matches origin, destination or a
        transfer city, ignoring case and accents; `ticket_id` is exact; `booked_after` is
        inclusive and `booked_before` exclusive, ISO date or datetime (UTC when no offset).
        Codes: ok, bad_request (a time that cannot be parsed)."""
        given = {"city": city, "ticket_id": ticket_id, "booked_after": booked_after,
                 "booked_before": booked_before}
        filter_ = {k: str(given[k]) for k in _FILTER_KEYS if given[k] is not None}
        try:
            after = _parse_time(booked_after) if booked_after is not None else None
            before = _parse_time(booked_before) if booked_before is not None else None
        except ValueError as exc:
            return Result("bad_request", {"message": str(exc), "filter": filter_})
        key = places.fold(city) if city is not None else None
        with self.store.read() as con:
            rows = records.list_tickets(con, user_id)
        kept = []
        for t in rows:
            booked = parse_iso(t["booked_at"])
            if ticket_id is not None and t["id"] != ticket_id:
                continue
            if after is not None and booked < after:
                continue
            if before is not None and booked >= before:
                continue
            if key is not None and key not in {places.fold(c) for c in _cities(t["legs_snapshot"])}:
                continue
            kept.append(t)
        card = cards.ticket_list_card(kept, filter_)
        return Result("ok", {k: v for k, v in card.items() if k != "type"}, (card,))

    def post_assistant_message(self, user_id: str, conversation_id: str, text: str,
                               cards_: Iterable[dict] = (), guard_problems: Any = None) -> dict:
        """The `done` moment of a chat turn, in one transaction: store the assistant message and
        present its quote / booking-request cards (`quoted` -> `presented`, B2). Returns the
        contract `Message`; its quote cards now carry `state.nonce` when bookable. Send that
        message with the `done` event - the nonce must not reach the client earlier and must
        never be given to the LLM.

        Quote cards are stored by reference and re-rendered from the quote on every read.
        Raises NotFound (conversation not the user's) or ValueError (a quote card that does not
        belong to this user and conversation)."""
        with self.store.write() as con:
            now = self.store.clock()  # after the write lock is ours: a card is never presented late
            self._require_conversation(con, user_id, conversation_id)
            stored, quote_ids = [], []
            for card in cards_:
                if card.get("type") in _QUOTE_CARD_TYPES:
                    stored.append({"type": card["type"], "id": card["id"]})
                    quote_ids.append(card["id"])
                else:
                    stored.append(card)
            message = records.add_message(con, iso(now), conversation_id, "assistant", text,
                                          stored, guard_problems)
            for quote_id in dict.fromkeys(quote_ids):
                quote = records.get_quote(con, quote_id)
                if quote is None or quote["user_id"] != user_id or quote["conversation_id"] != conversation_id:
                    raise ValueError(f"card for a quote outside this conversation: {quote_id!r}")
                status = self._settle(con, quote, now)
                if status == "quoted":
                    records.set_quote_status(con, quote, "presented", iso(now))
                    status = "presented"
                if status == "presented":
                    records.add_quote_card(con, quote_id, message["id"], iso(now))
            return self._message_view(con, user_id, message, now)

    # ================================================================== HTTP layer ============
    def book(self, user_id: str, quote_id: str, nonce: str, *,
             lock_timeout: float | None = BOOK_LOCK_TIMEOUT) -> dict:
        """The only way a ticket is created: the user's click on a presented card.

        Returns `{"ticket": TicketCard, "message": Message}` (the system message appended to the
        conversation). Raises BookingError; checked in this order: not_found, conversation_busy,
        expired, already_booked, stale, not_presented, invalid_nonce, data_unavailable, stale
        (re-check). expired and stale are final for the quote; every other refusal changes
        nothing, so the same click can be repeated.

        Blocking: it takes the conversation lock itself (do not call it while holding that lock)
        and waits up to `lock_timeout` seconds for a running chat turn, then refuses with
        conversation_busy (None waits without limit). On the event loop use `book_async`.
        """
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
        if quote is None or quote["user_id"] != user_id:  # B1
            raise BookingError("not_found")
        try:
            with self.locks.hold(quote["conversation_id"], timeout=lock_timeout):
                with self.store.write() as con:  # B7: one BEGIN IMMEDIATE transaction
                    outcome = self._book(con, user_id, quote_id, nonce)
        except ConversationBusy:
            raise BookingError("conversation_busy") from None
        # A refusal may have changed the quote (expired / stale), so it is raised after COMMIT.
        if isinstance(outcome, BookingError):
            raise outcome
        return outcome

    async def book_async(self, user_id: str, quote_id: str, nonce: str, *,
                         lock_timeout: float | None = BOOK_LOCK_TIMEOUT) -> dict:
        """`book` for async callers: runs in a worker thread, so the event loop (and the chat
        turn that may hold the conversation lock) keeps running while the click waits."""
        return await asyncio.to_thread(self.book, user_id, quote_id, nonce, lock_timeout=lock_timeout)

    def _book(self, con: sqlite3.Connection, user_id: str, quote_id: str, nonce: str) -> dict | BookingError:
        now = self.store.clock()
        quote = records.get_quote(con, quote_id)
        if quote is None or quote["user_id"] != user_id:  # B1
            return BookingError("not_found")
        status = self._settle(con, quote, now)  # B4
        if status == "booked":  # B5
            ticket = records.get_ticket_for_quote(con, quote_id)
            return BookingError("already_booked", ticket_id=ticket["id"])
        if status in ("expired", "stale"):
            return BookingError(status, reason=quote["status_reason"])
        if status != "presented":  # B2
            return BookingError("not_presented")
        message_id = self._match_nonce(con, quote, nonce)  # B3
        if message_id is None:
            return BookingError("invalid_nonce")
        try:
            fingerprint, problem = self._recheck(quote)  # B6
        except NetworkDataError as exc:
            # Not readable is not "changed": nothing is written, the click can be repeated.
            return BookingError("data_unavailable", reason=str(exc))
        if not records.mark_nonce_used(con, quote_id, message_id, iso(now)):
            return BookingError("invalid_nonce")
        if problem is not None:
            records.set_quote_status(con, quote, "stale", iso(now), problem)
            return BookingError("stale", reason=problem)
        ticket = records.insert_ticket(con, now=iso(now), quote=quote, network_fingerprint=fingerprint,
                                       confirmed_message_id=message_id)
        records.set_quote_status(con, quote, "booked", iso(now))
        card = cards.ticket_card(ticket)
        message = records.add_message(con, iso(now), quote["conversation_id"], "system",
                                      "Ticket booked.", [card])
        return {"ticket": card, "message": self._message_view(con, user_id, message, now)}

    def trace(self, user_id: str, quote_id: str) -> dict:
        """Contract `Trace` ("how was this price computed") for one of the user's quotes."""
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
        if quote is None or quote["user_id"] != user_id:
            raise NotFound(quote_id)
        return cards.trace(quote, FORMULA, ALGORITHM)

    def tickets(self, user_id: str) -> list[dict]:
        """All `TicketCard`s of the user, newest first (GET /api/tickets)."""
        with self.store.read() as con:
            return [cards.ticket_card(t) for t in records.list_tickets(con, user_id)]

    def messages(self, user_id: str, conversation_id: str) -> list[dict]:
        """Contract `Message` list of a conversation, oldest first, quote cards with the current
        server state (and a nonce when bookable). Raises NotFound when not the user's."""
        now = self.store.clock()
        with self.store.read() as con:
            self._require_conversation(con, user_id, conversation_id)
            return [self._message_view(con, user_id, m, now)
                    for m in records.list_messages(con, conversation_id)]

    def get_quote(self, user_id: str, quote_id: str) -> dict | None:
        """The quote as a `QuoteCard` with its current status (never bookable: no message, no
        nonce), or None when it is not the user's."""
        now = self.store.clock()
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
            if quote is None or quote["user_id"] != user_id:
                return None
            return cards.quote_card(quote, self._state(con, quote, now),
                                    similar_names=_similar(self.networks, quote))

    # ================================================================== internals =============
    def _require_conversation(self, con: sqlite3.Connection, user_id: str, conversation_id: str) -> None:
        if records.conversation_owner(con, conversation_id) != user_id:
            raise NotFound(conversation_id)

    def _expired(self, quote: dict, now: datetime) -> bool:
        return quote["status"] in OPEN and now >= parse_iso(quote["expires_at"])

    def _settle(self, con: sqlite3.Connection, quote: dict, now: datetime) -> str:
        """Current status inside a write transaction; an elapsed quote is stored as `expired`."""
        if self._expired(quote, now):
            records.set_quote_status(con, quote, "expired", iso(now), "validity elapsed")
        return quote["status"]

    def _nonce(self, quote: dict, message_id: str) -> str:
        """The confirmation nonce of the card of `quote` in `message_id`: bound to quote, user
        and message (B3). Derived, not stored: app.db alone is not enough to produce one."""
        text = "\x1f".join((quote["id"], quote["user_id"], message_id)).encode()
        return "n_" + hmac.new(self._secret, text, hashlib.sha256).hexdigest()[:32]

    def _match_nonce(self, con: sqlite3.Connection, quote: dict, nonce: Any) -> str | None:
        """The message whose presented, not yet used card of this quote carries `nonce`."""
        if not isinstance(nonce, str):
            return None
        found = None
        for card in records.quote_cards(con, quote["id"]):
            # surrogatepass: a lone surrogate from JSON must compare unequal, not raise
            same = hmac.compare_digest(self._nonce(quote, card["message_id"]).encode(),
                                       nonce.encode("utf-8", "surrogatepass"))
            if same and card["nonce_used_at"] is None:
                found = card["message_id"]
        return found

    def _recheck(self, quote: dict) -> tuple[str, None] | tuple[None, str]:
        """B6. Recompute the quoted itinerary on the locations.db that is current right now
        (content hash checked, reloaded when it differs). Returns (fingerprint, None) when every
        city, coordinate, leg, distance and price equals the stored quote and the itinerary is
        still the cheapest one; otherwise (None, what differs). Raises NetworkDataError when
        the database cannot be read or is not valid right now."""
        net = self.networks.current(strict=True)
        legs = quote["legs"]
        itinerary = _cities(legs)
        for leg in legs:
            for name, coord in ((leg["from"], leg["from_coord"]), (leg["to"], leg["to_coord"])):
                city = net.cities.get(name)
                if city is None:
                    return None, f"city no longer exists: {name}"
                if (city.x, city.y) != (Decimal(coord[0]), Decimal(coord[1])):
                    return None, f"coordinate changed: {name}"
        try:
            priced = price_path(net, itinerary)
        except InvalidPathError as exc:
            return None, f"itinerary no longer valid: {exc}"
        fresh_legs = [(l.origin, l.destination, l.distance) for l in priced.legs]
        if fresh_legs != [(l["from"], l["to"], l["distance"]) for l in legs]:
            return None, "leg distances changed"
        if (priced.distance, priced.price) != (quote["distance"], quote["price"]):
            return None, "distance or price changed"
        best = cheapest_route(net, quote["origin"], quote["destination"])
        if not isinstance(best, Route):
            return None, f"route lookup now fails: {best.code}"
        if best.cities != tuple(itinerary) or best.price != quote["price"]:
            return None, "a different route is now the cheapest"
        return net.fingerprint.id, None

    def _state(self, con: sqlite3.Connection, quote: dict, now: datetime,
               message_id: str | None = None) -> dict:
        """Contract `CardState` of the quote's card in `message_id` (Q2: always from the server's
        current quote state). Without a message the card is never bookable."""
        status = "expired" if self._expired(quote, now) else quote["status"]
        ticket_id = nonce = None
        if status == "booked":
            ticket_id = records.get_ticket_for_quote(con, quote["id"])["id"]
        elif status == "presented" and message_id is not None:
            for card in records.quote_cards(con, quote["id"]):
                if card["message_id"] == message_id and card["nonce_used_at"] is None:
                    nonce = self._nonce(quote, message_id)
        return cards.card_state(status, nonce, ticket_id)

    def _message_view(self, con: sqlite3.Connection, user_id: str, message: dict, now: datetime) -> dict:
        out = []
        for card in message["cards"]:
            if card.get("type") in _QUOTE_CARD_TYPES:
                quote = records.get_quote(con, card["id"])
                if quote is None or quote["user_id"] != user_id:
                    continue  # cannot happen through post_assistant_message; never leak a quote
                state = self._state(con, quote, now, message["id"])
                card = cards.quote_card(quote, state, kind=card["type"],
                                        similar_names=_similar(self.networks, quote))
            out.append(card)
        return {"id": message["id"], "role": message["role"], "text": message["text"],
                "cards": out, "created_at": message["created_at"]}


# ---------------------------------------------------------------------- helpers ---------------
def _floor(moment: datetime) -> datetime:
    return parse_iso(iso(moment))


def _parse_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(text).strip())
    except ValueError:
        raise ValueError(f"not an ISO date or datetime: {text!r}") from None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _cities(legs: list[dict]) -> list[str]:
    return [legs[0]["from"]] + [leg["to"] for leg in legs]


def _coord(net: Network, name: str) -> list[str]:
    city = net.cities[name]
    return [str(city.x), str(city.y)]


def _source(net: Network, a: str, b: str) -> str:
    """Which locations.db data says this flight exists."""
    ca, cb = net.cities[a], net.cities[b]
    if ca.is_hub and cb.is_hub:
        return f"hub_links({a}, {b})"
    return f"locations.connected_hubs({b if ca.is_hub else a})"


def _connected_hubs(net: Network, name: str) -> list[str]:
    """Hubs with a direct flight to the city: its connected_hubs, or for a hub its hub links."""
    city = net.cities[name]
    if not city.is_hub:
        return list(city.connected_hubs)
    return sorted(n for n in net.neighbors[name] if net.cities[n].is_hub)


def _similar(networks: NetworkProvider, quote: dict) -> list[dict]:
    """Other cities whose accent-folded name equals an endpoint's, with their connected hubs.
    Display only: when locations.db is unreadable the card is still shown, without the hint."""
    try:
        net = networks.current()
    except NetworkDataError:
        return []
    out: list[dict] = []
    for endpoint in (quote["origin"], quote["destination"]):
        for name in net.fold_index.get(places.fold(endpoint), ()):
            if name != quote["origin"] and name != quote["destination"] and name not in {o["city"] for o in out}:
                out.append({"city": name, "connected_hubs": _connected_hubs(net, name)})
    return out


def _candidate(net: Network, name: str) -> dict:
    return {"city": name, "is_hub": net.cities[name].is_hub, "connected_hubs": _connected_hubs(net, name)}


def _quote_data(quote: dict, status: str) -> dict:
    card = cards.quote_card(quote, cards.card_state(status))
    return {
        "quote_id": quote["id"],
        "origin": quote["origin"],
        "destination": quote["destination"],
        "legs": [{k: l[k] for k in ("from", "to", "distance_display")} for l in card["legs"]],
        "transfers": card["transfers"],
        "direct": len(card["legs"]) == 1,
        "distance_display": card["distance_display"],
        "price_display": card["price_display"],
        "expires_at": quote["expires_at"],
        "status": status,
    }


def _route_error(net: Network, err: RouteError) -> Result:
    if err.code == "same_city":
        return Result("same_city", {"cities": list(err.cities)}, (cards.notice_card("same_city"),))
    if err.code == "unreachable":
        a, b = err.cities
        text = f"There is no route between {a} and {b}."
        return Result("unreachable", {"cities": [a, b]}, (cards.notice_card("unreachable", text),))
    issues, out = [], []
    for issue in err.issues:
        item: dict[str, Any] = {"role": issue.role, "code": issue.code, "query": issue.query}
        if issue.code == "unknown_city":
            item["suggestions"] = list(issue.candidates)
            out.append(cards.unknown_city_card(issue.query, issue.candidates))
        elif issue.code == "ambiguous_city":
            item["candidates"] = [_candidate(net, c) for c in issue.candidates]
            out.append(cards.ambiguous_city_card(issue.query, item["candidates"]))
        else:  # no_service
            item["city"] = issue.city
            item["nearest"] = cards.nearest(issue.nearest_served)
            out.append(cards.no_service_card(issue.city, item["nearest"]))
        issues.append(item)
    return Result(err.code, {"issues": issues}, tuple(out))
