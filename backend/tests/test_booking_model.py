"""Model-based test of the booking rules B1-B7 and Q1-Q3 (docs/design/state_invariants.md).

Seeded random event sequences run against the real `Airline` (own app.db, own copy of a locations
database, fake clock) and against a small reference model kept here. After EVERY event:

- the outcome of the event equals what the model predicted (error code, card state), and
- every invariant is checked on the rows of app.db.

The model decides independently of app code: time with its own arithmetic, freshness (B6) with
tests/reference.py (own sqlite read, own Bellman-Ford, own Decimal arithmetic) on the database file
as it is at click time. Seeds are fixed, so every run executes the same sequences; a failing seed
is shrunk and printed as a list that can be pasted into REGRESSIONS.

Events are plain tuples with small integers that are resolved against the current state ("the
k-th most recent quote"), so any sub-sequence is still a valid sequence (needed for shrinking).
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from app.airline import Airline, BookingError
from app.airline import cards as airline_cards
from app.core.network import NetworkDataError
from app.store import parse_iso, records

from .airline_helpers import World, assert_card, assert_message, fixture_schemas
from .reference import RefGraph

SCHEMAS = fixture_schemas()
TTL = timedelta(minutes=15)
OPEN = ("quoted", "presented")
TERMINAL = ("booked", "expired", "stale")
# which rule a wrong answer breaks, by what the model expected
RULE = {"not_found": "B1", "not_presented": "B2", "invalid_nonce": "B3", "expired": "B4",
        "already_booked": "B5", "stale": "B6", "data_unavailable": "B6", "booked": "B7",
        "quoted": "B2", "presented": "B2"}
ALLOWED = {("quoted", "presented"), ("quoted", "expired"), ("presented", "booked"),
           ("presented", "expired"), ("presented", "stale")}
USER_TEXTS = ["yes, book it", "ok confirm", "book the ticket now", "no, do not book", "is it cheaper via H2?",
              "how far is that?", "go ahead", "cancel"]
CLOCK_STEPS = [1, 1, 5, 30, 30, 60, 60, 120, 300, 600, 840, 899, 900, 901, 0.5, 0.5]

SMALL_SEEDS = range(60)
SMALL_LENGTH = 120
REAL_SEEDS = range(10)
REAL_LENGTH = 100


# ------------------------------------------------------------------------------- events -------
def gen_event(rng: random.Random) -> tuple:
    kind = rng.choices(
        ["quote", "done", "drop", "user_message", "llm_request_booking", "click", "click_concurrent",
         "replay", "other_user_click", "clock", "db_change", "reconnect", "stop_turn"],
        weights=[14, 16, 2, 5, 8, 20, 5, 4, 3, 9, 6, 8, 4],
    )[0]
    r = lambda: rng.randrange(1000)  # noqa: E731
    if kind == "quote":
        return (kind, r(), r(), r())
    if kind in ("done", "drop", "reconnect", "stop_turn"):
        return (kind, r())
    if kind == "user_message":
        return (kind, r(), r())
    if kind == "llm_request_booking":
        return (kind, r(), r())
    if kind == "click":
        return (kind, r(), rng.choice(["valid", "valid", "valid", "valid", "garbage", "other_quote", "empty", "none",
                                       "surrogate"]), r())
    if kind == "click_concurrent":
        return (kind, r(), rng.choice([2, 3, 4]), r())
    if kind in ("replay", "other_user_click"):
        return (kind, r(), r())
    if kind == "clock":
        return (kind, rng.choice(CLOCK_STEPS))
    return (kind, rng.choices(["leg_removed", "coord_moved", "unrelated", "file_replaced", "restore", "broken"],
                              weights=[4, 4, 4, 2, 4, 1])[0], r(), r())


def gen_sequence(seed: int, length: int) -> list[tuple]:
    rng = random.Random(seed)
    return [gen_event(rng) for _ in range(length)]


# ------------------------------------------------------------------------------- the model ----
class MQuote:
    def __init__(self, qid, user, conv, created, cities, coords, distance, price):
        self.id, self.user, self.conv = qid, user, conv
        self.created = created  # floored to the second
        self.expires = created + TTL
        self.cities, self.coords, self.distance, self.price = cities, coords, distance, price
        self.status = "quoted"
        self.presented_at = None
        self.nonces: dict[str, str] = {}  # message id -> nonce the client was given
        self.ticket = None
        self.booked_by = None  # (event index, event kind, user index, clock)


class Sim:
    """The system under test plus the reference model, driven by the same events."""

    def __init__(self, tmp_path, real=False, airline_cls=Airline, **airline_kwargs):
        self.w = World(tmp_path, real=real, airline_cls=airline_cls, **airline_kwargs)
        w = self.w
        self.air = w.airline
        self.users = [w.alice, w.bob]
        self.convs = [(0, w.conv), (1, w.bob_conv),
                      (0, w.store.create_conversation(w.alice)["id"]),
                      (1, w.store.create_conversation(w.bob)["id"])]
        self.original = w.db.read_bytes()
        self.version = 0  # bumped on every change of the locations file
        self._ref: tuple[int, RefGraph] | None = None
        self._best: dict = {}
        ref = self.ref()
        if real:
            spokes = [c for c in ref.xy if c not in ref.hubs and ref.served(c)]
            hubs = [c for c in ref.xy if c in ref.hubs]
            unserved = [c for c in ref.xy if not ref.served(c)]
            self.names = (spokes[:5] + hubs[:3]) * 3 + unserved[:1] + ["Nowhere"]
        else:
            # mostly the connected part (A B C D H1-H4), now and then U, the island, a typo
            self.names = [n for n in ref.xy if n in "ABCD" or n.startswith("H")] * 3 + list(ref.xy) + ["Nowhere"]
        self.broken = False
        self.quotes: list[MQuote] = []
        self.by_id: dict[str, MQuote] = {}
        self.pending: dict[int, list] = {i: [] for i in range(len(self.convs))}
        self.raw_status: dict[str, str] = {}
        self.final: dict[str, str] = {}  # quote id -> terminal status once seen (Q3)
        self.index = -1
        self.kind = ""
        self.con = sqlite3.connect(w.store.path)  # one read connection for the invariant checks

    def sql(self, query, args=()):
        return self.con.execute(query, args).fetchall()

    def close(self):
        self.con.close()

    # --- independent facts about the locations file ---
    def ref(self) -> RefGraph:
        if self._ref is None or self._ref[0] != self.version:
            self._ref = (self.version, RefGraph.from_db(self.w.db))
            self._best = {}
        return self._ref[1]

    def best(self, origin, dest):
        ref = self.ref()
        if origin not in self._best:
            self._best[origin] = ref.bellman_ford_from(origin) if origin in ref.xy else {}
        return self._best[origin].get(dest)

    def fresh(self, q: MQuote) -> bool:
        """B6, decided without app code: same coordinates, every leg still a flight, transfers
        still hubs, and the reference's cheapest route is this itinerary at this price."""
        if self.broken:
            return False
        ref = self.ref()
        for city in q.cities:
            if city not in ref.xy or ref.xy[city] != q.coords[city]:
                return False
        if any(b not in ref.flights_from(a) for a, b in zip(q.cities, q.cities[1:])):
            return False
        if any(c not in ref.hubs for c in q.cities[1:-1]):
            return False
        best = self.best(q.cities[0], q.cities[-1])
        return (best is not None and best["cities"] == tuple(q.cities)
                and best["price"] == q.price and best["distance"] == q.distance)

    def mstatus(self, q: MQuote) -> str:
        if q.status in OPEN and self.w.clock.now >= q.expires:
            q.status = "expired"
        return q.status

    def expect_click(self, q: MQuote, user: int, nonce) -> str:
        if q.user != user:
            return "not_found"
        status = self.mstatus(q)
        if status == "booked":
            return "already_booked"
        if status in ("expired", "stale"):
            return status
        if status == "quoted":
            return "not_presented"
        if nonce not in q.nonces.values():
            return "invalid_nonce"
        if self.broken:
            return "data_unavailable"  # not readable is not "changed": the quote stays as it is
        if not self.fresh(q):
            q.status = "stale"
            return "stale"
        q.status = "booked"
        return "booked"

    # --- picking things ---
    def pick_quote(self, k: int) -> MQuote | None:
        if not self.quotes:
            return None
        return self.quotes[-1 - (k % min(len(self.quotes), 5))]

    def known_nonce(self, q: MQuote, k: int):
        values = list(q.nonces.values())
        return values[k % len(values)] if values else "n_never_given"

    # --- events ---
    def apply(self, index: int, event: tuple) -> None:
        self.index, self.kind = index, event[0]
        tickets_before = self.ticket_rows()
        getattr(self, "ev_" + event[0])(*event[1:])
        self.check_invariants(tickets_before)

    def ev_quote(self, c, o, d):
        user, conv = self.convs[c % len(self.convs)]
        origin, dest = self.names[o % len(self.names)], self.names[d % len(self.names)]
        before = self.raw_rows()
        try:
            result = self.air.quote_route(self.users[user], conv, origin, dest)
        except NetworkDataError:
            assert self.broken, "quote_route failed on a valid database"
            assert self.raw_rows() == before
            return
        assert not self.broken, "quote_route answered from a broken database"
        after = self.raw_rows()
        for card in result.cards:
            assert_card(card, SCHEMAS)
        assert "nonce\": \"" not in json.dumps([result.data, result.cards])
        if not result.ok:
            assert after == before, "a failed quote changed app.db"
            self.pending[c % len(self.convs)] += result.cards
            return
        card = result.cards[0]
        qid = card["id"]
        # Q1: creating a quote changes no other quote
        assert {k: v for k, v in after.items() if k != qid} == before, "Q1: another quote changed"
        assert after[qid] == "quoted", f"B2: a new quote is {after[qid]} before done"
        assert card["state"] == {"status": "quoted", "bookable": False, "nonce": None, "ticket_id": None}, card
        ref = self.ref()
        cities = [card["legs"][0]["from"]] + [leg["to"] for leg in card["legs"]]
        best = self.best(cities[0], cities[-1])
        assert best is not None and best["cities"] == tuple(cities), (cities, best)
        assert (card["price"], card["distance"]) == (best["price"], best["distance"])
        created = self.w.clock.now.replace(microsecond=0)
        q = MQuote(qid, user, c % len(self.convs), created, cities,
                   {city: ref.xy[city] for city in cities}, card["distance"], card["price"])
        assert parse_iso(card["expires_at"]) == q.expires
        trace = self.air.trace(self.users[user], qid)
        for leg in trace["legs"]:
            assert tuple(map(Decimal, map(str, leg["from_coord"]))) == q.coords[leg["from"]]
            assert tuple(map(Decimal, map(str, leg["to_coord"]))) == q.coords[leg["to"]]
        self.quotes.append(q)
        self.by_id[qid] = q
        self.pending[q.conv].append(card)

    def ev_done(self, c):
        ci = c % len(self.convs)
        user, conv = self.convs[ci]
        cards, self.pending[ci] = self.pending[ci], []
        expected = {}
        for card in cards:
            if card["type"] in ("quote", "booking_request"):
                q = self.by_id[card["id"]]
                if self.mstatus(q) == "quoted":
                    q.status = "presented"
                    q.presented_at = self.w.clock.now
                expected[q.id] = q.status
        message = self.air.post_assistant_message(self.users[user], conv, "Here is what I found.", cards)
        assert_message(message, SCHEMAS)
        assert len(message["cards"]) == len(cards)
        self.see_cards(message)

    def ev_stop_turn(self, c):
        """The user stopped the turn (#48, R6): its cards are stored with a `cancelled` message and
        none of them is presented; no quote changes status."""
        ci = c % len(self.convs)
        user, conv = self.convs[ci]
        cards, self.pending[ci] = self.pending[ci], []
        before = self.raw_rows()
        message = self.air.post_assistant_message(self.users[user], conv, "Let me", cards,
                                                  [{"kind": "cancelled", "value": ""}],
                                                  present=False, status="cancelled")
        assert_message(message, SCHEMAS)
        assert message["status"] == "cancelled" and len(message["cards"]) == len(cards)
        # nothing changes status (a lazy expiry is settled on the next read, as everywhere)
        assert self.raw_rows() == before, "R6: a stopped turn changed a quote"
        self.see_cards(message)

    def ev_drop(self, c):
        """The stream died before `done`: its cards were never presented."""
        self.pending[c % len(self.convs)] = []

    def ev_user_message(self, c, t):
        user, conv = self.convs[c % len(self.convs)]
        self.w.store.add_message(self.users[user], conv, "user", USER_TEXTS[t % len(USER_TEXTS)])

    def ev_llm_request_booking(self, c, k):
        ci = c % len(self.convs)
        user, conv = self.convs[ci]
        q = self.pick_quote(k)
        qid = q.id if q else "q_invented_by_the_llm"
        if q is None or q.user != user or q.conv != ci:
            expected = "not_found"
        else:
            expected = {"booked": "already_booked", "expired": "quote_expired", "stale": "stale",
                        "quoted": "ok", "presented": "ok"}[self.mstatus(q)]
        result = self.air.request_booking(self.users[user], conv, qid)
        assert result.code == expected, (result.code, expected)
        for card in result.cards:
            assert_card(card, SCHEMAS)
            if "state" in card:
                assert card["state"]["nonce"] is None and card["state"]["bookable"] is False
        if q is not None:
            for nonce in q.nonces.values():
                assert nonce not in json.dumps([result.data, result.cards]), "B3: a tool result leaked a nonce"
        self.pending[ci] += result.cards

    def ev_click(self, k, nonce_kind, n, user=None):
        q = self.pick_quote(k)
        if q is None:
            return
        if nonce_kind == "valid":
            nonce = self.known_nonce(q, n)
        elif nonce_kind == "other_quote":
            others = [v for o in self.quotes if o is not q and o.user == q.user for v in o.nonces.values()]
            nonce = others[n % len(others)] if others else "n_no_other_quote"
        else:
            nonce = {"garbage": f"n_{n:032x}", "empty": "", "none": None, "surrogate": "n_\ud800"}[nonce_kind]
        self.click(q, q.user if user is None else user, nonce)

    def ev_replay(self, k, n):
        """Click again with a nonce of a quote that is already booked."""
        booked = [q for q in self.quotes if q.status == "booked"]
        if booked:
            q = booked[k % len(booked)]
            self.click(q, q.user, self.known_nonce(q, n))

    def ev_other_user_click(self, k, n):
        q = self.pick_quote(k)
        if q is not None:
            self.click(q, 1 - q.user, self.known_nonce(q, n))

    def click(self, q: MQuote, user: int, nonce):
        expected = self.expect_click(q, user, nonce)
        try:
            out = self.air.book(self.users[user], q.id, nonce)
            got = "booked"
        except BookingError as exc:
            got = exc.code
            if got == "already_booked":
                assert exc.ticket_id == q.ticket
        assert got == expected, f"{RULE[expected]}: click on {q.id}: got {got}, the model says {expected}"
        if got == "booked":
            self.booked(q, user, out)

    def booked(self, q: MQuote, user: int, out: dict):
        ticket = out["ticket"]
        assert_card(ticket, SCHEMAS)
        assert_message(out["message"], SCHEMAS)
        q.ticket = ticket["id"]
        q.booked_by = (self.index, self.kind, user, self.w.clock.now)
        # B6: the ticket equals an independent recomputation on the file as it is right now
        best = self.best(q.cities[0], q.cities[-1])
        assert [ticket["legs"][0]["from"]] + [leg["to"] for leg in ticket["legs"]] == list(best["cities"])
        row = self.sql("select distance, price, network_fingerprint from tickets where id = ?", (ticket["id"],))
        digest = hashlib.sha256(self.w.db.read_bytes()).hexdigest()
        assert row == [(best["distance"], best["price"], f"sha256:{digest}")], "B6"

    def ev_click_concurrent(self, k, threads, n):
        q = self.pick_quote(k)
        if q is None:
            return
        nonce = self.known_nonce(q, n)
        expected = self.expect_click(q, q.user, nonce)
        barrier = threading.Barrier(threads)
        outcomes: list = [None] * threads

        def go(i):
            barrier.wait(10)
            try:
                outcomes[i] = self.air.book(self.users[q.user], q.id, nonce)
            except BookingError as exc:
                outcomes[i] = exc.code
            except Exception as exc:  # pragma: no cover - shown by the assertion below
                outcomes[i] = repr(exc)

        pool = [threading.Thread(target=go, args=(i,)) for i in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join(30)
        wins = [o for o in outcomes if isinstance(o, dict)]
        codes = sorted(o for o in outcomes if not isinstance(o, dict))
        if expected == "booked":
            # the root cause first: a ticket was written, so the quote must be booked (B7) - the
            # losers' codes (B5) follow from that
            if len(wins) == 1:
                assert self.raw_rows()[q.id] == "booked", ("B7", q.id, self.raw_rows()[q.id], outcomes)
            assert len(wins) == 1 and codes == ["already_booked"] * (threads - 1), ("B5", outcomes)
            self.booked(q, q.user, wins[0])
        else:
            assert not wins and codes == [expected] * threads, (RULE[expected], outcomes, expected)

    def ev_clock(self, seconds):
        self.w.clock.advance(seconds=seconds)

    def ev_reconnect(self, c):
        """The client reloads the conversation: what it sees is the server state (Q2)."""
        user, conv = self.convs[c % len(self.convs)]
        for message in self.air.messages(self.users[user], conv):
            assert_message(message, SCHEMAS)
            self.see_cards(message)

    def see_cards(self, message):
        """Q2: every quote card a client receives carries the model's status; it is bookable
        exactly when the quote is presented, always with the same nonce for the same card."""
        for card in message["cards"]:
            if card["type"] not in ("quote", "booking_request"):
                continue
            q = self.by_id[card["id"]]
            status = self.mstatus(q)
            state = card["state"]
            assert state["status"] == status, f"Q2: card says {state['status']}, model {status}"
            bookable = status == "presented" and message.get("status") != "cancelled"
            assert state["bookable"] is bookable, f"Q2 / R6: bookable on a {status} quote ({message.get('status')})"
            assert (state["nonce"] is not None) is state["bookable"]
            assert state["ticket_id"] == (q.ticket if status == "booked" else None)
            if state["nonce"] is not None:
                assert q.nonces.setdefault(message["id"], state["nonce"]) == state["nonce"], "nonce changed"
                for other in self.quotes:
                    assert other is q or state["nonce"] not in other.nonces.values(), "nonce reused"

    def ev_db_change(self, kind, a, b):
        w = self.w
        if kind == "restore":
            w.replace_db(self.original)
            self.broken = False
        elif kind == "file_replaced":
            w.replace_db(w.db.read_bytes())
        elif kind == "broken":
            w.edit_db("update locations set coordinate = 'oops' where rowid = 1")
            self.broken = True
        elif not self.broken:
            ref = self.ref()
            live = [q for q in self.quotes if q.status in OPEN]
            on_route = a % 5 < 3 and live
            if kind == "leg_removed":
                if on_route:
                    q = live[b % len(live)]
                    legs = list(zip(q.cities, q.cities[1:]))
                else:
                    legs = sorted((x, y) for x in ref.xy for y in ref.flights_from(x) if x < y)
                if legs:
                    self.remove_leg(*legs[(a // 5) % len(legs)])
            elif kind == "coord_moved":
                names = live[b % len(live)].cities if on_route else list(ref.xy)
                name = names[(a // 5) % len(names)]
                x, y = ref.xy[name]
                w.edit_db(("update locations set coordinate = ? where city_name = ?",
                           (f"{x + 1 + b % 3},{y}", name)))
            else:  # unrelated: a city without service moves; no route can be affected
                unserved = [c for c in ref.xy if not ref.served(c)]
                if unserved:
                    name = unserved[a % len(unserved)]
                    x, y = ref.xy[name]
                    w.edit_db(("update locations set coordinate = ? where city_name = ?", (f"{x},{y + 1}", name)))
                    self.version += 1
                    assert all(self.fresh(q) for q in live if self.was_fresh.get(q.id)), "unrelated change"
        self.version += 1

    def remove_leg(self, a, b):
        ref = self.ref()
        if a in ref.hubs and b in ref.hubs:
            self.w.edit_db(("delete from hub_links where (hub_a = ? and hub_b = ?) or (hub_a = ? and hub_b = ?)",
                            (a, b, b, a)))
        else:
            spoke, hub = (b, a) if a in ref.hubs else (a, b)
            left = ";".join(h for h in ref.spokes[spoke] if h != hub)
            self.w.edit_db(("update locations set connected_hubs = ? where city_name = ?", (left, spoke)))

    # --- invariants, checked on app.db after every event ---
    def raw_rows(self) -> dict:
        return dict(self.sql("select id, status from quotes"))

    def ticket_rows(self) -> dict:
        rows = self.sql("select quote_id, id, user_id, legs_snapshot, distance, price, booked_at, "
                          "confirmed_message_id from tickets")
        assert len({r[0] for r in rows}) == len(rows), "B5: two tickets for one quote"
        return {r[0]: r[1:] for r in rows}

    def check_invariants(self, tickets_before: dict) -> None:
        now = self.w.clock.now
        tickets = self.ticket_rows()  # B5 inside
        quotes = {r[0]: r for r in self.sql(
            "select id, user_id, status, legs, distance, price, created_at, expires_at, presented_at from quotes")}
        assert set(quotes) == set(self.by_id), "quotes in app.db differ from the model"

        # B3: tickets appear only through a click, and exactly where the model says so
        new = set(tickets) - set(tickets_before)
        assert not (set(tickets_before) - set(tickets)), "a ticket disappeared"
        assert all(tickets[k] == tickets_before[k] for k in tickets_before), "a ticket changed"
        if self.kind not in ("click", "click_concurrent", "replay", "other_user_click"):
            assert not new, f"B3: {self.kind} created a ticket"
        assert set(tickets) == {q.id for q in self.quotes if q.status == "booked"}, "B3: tickets differ from the model"
        # B7: booked <=> exactly one ticket
        assert {qid for qid, row in quotes.items() if row[2] == "booked"} == set(tickets), "B7"

        for qid, (tid, user_id, legs, distance, price, booked_at, message_id) in tickets.items():
            q, row = self.by_id[qid], quotes[qid]
            index, kind, clicker, clicked_at = q.booked_by
            assert tid == q.ticket and kind in ("click", "click_concurrent", "replay"), "B3"
            assert user_id == row[1] == self.users[q.user] == self.users[clicker], "B1"
            assert q.presented_at is not None and q.presented_at <= clicked_at and row[8] is not None, "B2"
            assert message_id in q.nonces, "B3: not confirmed through a card the client was given"
            assert clicked_at < q.expires and parse_iso(booked_at) < parse_iso(row[7]), "B4"
            assert parse_iso(booked_at) - parse_iso(row[6]) < TTL, "B4"
            assert (json.loads(legs), distance, price) == (json.loads(row[3]), row[4], row[5]), "B6: ticket != quote"
            assert (distance, price) == (q.distance, q.price), "B6"

        for qid, row in quotes.items():
            q = self.by_id[qid]
            raw = row[2]
            # status only moves along the edges of the design's state machine; terminal stays (Q3)
            previous = self.raw_status.get(qid, "quoted")
            assert raw == previous or (previous, raw) in ALLOWED, f"Q3: {previous} -> {raw}"
            self.raw_status[qid] = raw
            effective = "expired" if raw in OPEN and now >= parse_iso(row[7]) else raw
            expected = self.mstatus(q)
            assert effective == expected, f"{RULE[expected]}: {qid}: app.db says {effective}, the model {expected}"
            if qid in self.final:
                assert effective == self.final[qid], f"Q3: {self.final[qid]} became {effective}"
            elif effective in TERMINAL:
                self.final[qid] = effective
        # the API view agrees with the rows (Q2), for the quotes a client is most likely to hold
        for q in self.quotes[-1:]:
            card = self.air.get_quote(self.users[q.user], q.id)
            assert card["state"]["status"] == q.status, \
                f"Q2/{RULE[q.status]}: view says {card['state']['status']}, model {q.status}"
            assert card["state"]["nonce"] is None, "a nonce outside a presented message card"
            assert self.air.get_quote(self.users[1 - q.user], q.id) is None, "another user can read the quote"

    @property
    def was_fresh(self) -> dict:
        return self._was_fresh

    def remember_freshness(self):
        self._was_fresh = {} if self.broken else {q.id: self.fresh(q) for q in self.quotes if q.status in OPEN}


def _apply_with_memory(sim: Sim, index: int, event: tuple) -> None:
    if event[0] == "db_change" and event[1] == "unrelated":
        sim.remember_freshness()
    sim.apply(index, event)


def run(sequence, tmp_path, **sim_kwargs) -> Sim:
    sim = Sim(tmp_path, **sim_kwargs)
    try:
        for index, event in enumerate(sequence):
            try:
                _apply_with_memory(sim, index, event)
            except AssertionError as exc:
                raise AssertionError(f"event {index} {event}: {exc}") from exc
    finally:
        sim.close()
    return sim


def shrink(sequence, fails):
    """Remove chunks, then single events, while the sequence still fails (tool/model_check.py)."""
    n = 2
    while len(sequence) >= 2:
        chunk = max(1, len(sequence) // n)
        for start in range(0, len(sequence), chunk):
            candidate = sequence[:start] + sequence[start + chunk:]
            if fails(candidate):
                sequence, n = candidate, max(n - 1, 2)
                break
        else:
            if chunk == 1:
                break
            n = min(n * 2, len(sequence))
    return sequence


def check(seeds, length, dirs, **sim_kwargs):
    """Run every seed; on the first failure shrink it and report a pasteable regression case."""
    def failure(sequence):
        try:
            run(sequence, dirs.mktemp("model"), **sim_kwargs)
        except Exception as exc:  # any crash of the system is a failure too
            return exc
        return None

    for seed in seeds:
        sequence = gen_sequence(seed, length)
        if failure(sequence) is None:
            continue
        small = shrink(sequence, lambda s: failure(s) is not None)
        raise AssertionError(f"seed {seed} fails: {failure(small)!r}\nkeep as a named case in REGRESSIONS:\n{small!r}")


# ------------------------------------------------------------------------------- the tests ----
def test_booking_invariants_on_a_small_network(tmp_path_factory):
    check(SMALL_SEEDS, SMALL_LENGTH, tmp_path_factory)


def test_booking_invariants_on_a_copy_of_the_real_locations_db(tmp_path_factory):
    check(REAL_SEEDS, REAL_LENGTH, tmp_path_factory, real=True)


def test_the_sequences_reach_every_outcome(tmp_path_factory):
    """The fixed seeds are only worth something if they visit the interesting states."""
    seen = {"tickets": 0, "status": set(), "kinds": set(), "booked_by": set()}
    refusals = set()
    original = Sim.click

    def spying(self, q, user, nonce):
        refusals.add(self.expect_click_preview(q, user, nonce))
        return original(self, q, user, nonce)

    def preview(self, q, user, nonce):
        saved = q.status
        code = self.expect_click(q, user, nonce)
        q.status = saved
        return code

    Sim.expect_click_preview = preview
    Sim.click = spying
    try:
        for seed in list(SMALL_SEEDS)[:25]:
            sim = run(gen_sequence(seed, SMALL_LENGTH), tmp_path_factory.mktemp("cover"))
            seen["tickets"] += sim.w.ticket_count()
            seen["status"] |= set(sim.raw_status.values())
            seen["booked_by"] |= {q.booked_by[1] for q in sim.quotes if q.booked_by}
    finally:
        Sim.click = original
        del Sim.expect_click_preview
    assert seen["status"] == {"quoted", "presented", "booked", "expired", "stale"}
    assert refusals == {"booked", "not_found", "already_booked", "expired", "stale", "not_presented",
                        "invalid_nonce", "data_unavailable"}
    assert seen["booked_by"] == {"click", "click_concurrent"} and seen["tickets"] >= 30


# Named cases: hand-written sequences for the rules, plus any shrunk failure of a seed.
REGRESSIONS = {
    "confirm words and request_booking never book (B3)": (
        [("quote", 0, 4, 7), ("done", 0), ("user_message", 0, 0), ("llm_request_booking", 0, 0), ("done", 0),
         ("user_message", 0, 1), ("llm_request_booking", 0, 0), ("llm_request_booking", 0, 0), ("reconnect", 0)],
        {"tickets": 0, "status": ["presented"]}),
    "double click and concurrent clicks give one ticket (B5)": (
        [("quote", 0, 4, 7), ("done", 0), ("click", 0, "valid", 0), ("click", 0, "valid", 0),
         ("click_concurrent", 0, 4, 0), ("replay", 0, 0), ("reconnect", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "concurrent first click gives one ticket (B5, B7)": (
        [("quote", 0, 4, 7), ("done", 0), ("click_concurrent", 0, 4, 0)],
        {"tickets": 1, "status": ["booked"]}),
    "not presented when the stream died before done (B2)": (
        [("quote", 0, 4, 7), ("drop", 0), ("click", 0, "garbage", 1), ("llm_request_booking", 0, 0), ("drop", 0),
         ("click", 0, "valid", 0)],
        {"tickets": 0, "status": ["quoted"]}),
    "the other user cannot book with a valid nonce (B1)": (
        [("quote", 0, 4, 7), ("done", 0), ("other_user_click", 0, 0), ("reconnect", 0)],
        {"tickets": 0, "status": ["presented"]}),
    "one second before the limit books (B4)": (
        [("quote", 0, 4, 7), ("done", 0), ("clock", 899), ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "exactly fifteen minutes is too late (B4)": (
        [("quote", 0, 4, 7), ("done", 0), ("clock", 899), ("clock", 1), ("click", 0, "valid", 0), ("reconnect", 0)],
        {"tickets": 0, "status": ["expired"]}),
    "quote made mid-second expires on the floored second (B4)": (
        [("clock", 0.5), ("quote", 0, 4, 7), ("done", 0), ("clock", 899), ("clock", 0.5), ("click", 0, "valid", 0)],
        {"tickets": 0, "status": ["expired"]}),
    "a removed leg makes the quote stale, for good (B6, Q3)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "leg_removed", 0, 0), ("click", 0, "valid", 0),
         ("db_change", "restore", 0, 0), ("click", 0, "valid", 0), ("reconnect", 0)],
        {"tickets": 0, "status": ["stale"]}),
    "a leg removed and restored before the click books (B6)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "leg_removed", 0, 0), ("db_change", "restore", 0, 0),
         ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "a moved city on the route makes the quote stale (B6)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "coord_moved", 0, 0), ("click", 0, "valid", 0)],
        {"tickets": 0, "status": ["stale"]}),
    "an unrelated row or a replaced identical file does not block (B6)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "unrelated", 0, 0), ("db_change", "file_replaced", 0, 0),
         ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "an unreadable locations.db books nothing and keeps the quote (B6)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "broken", 0, 0), ("quote", 0, 4, 7),
         ("click_concurrent", 0, 3, 0), ("click", 0, "valid", 0), ("reconnect", 0)],
        {"tickets": 0, "status": ["presented"]}),
    "the same click books once the data is readable again (B6)": (
        [("quote", 0, 4, 7), ("done", 0), ("db_change", "broken", 0, 0), ("click", 0, "valid", 0),
         ("db_change", "restore", 0, 0), ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "a lone surrogate as nonce is just an invalid nonce (B3)": (
        [("quote", 0, 4, 7), ("done", 0), ("click", 0, "surrogate", 0), ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "a second quote leaves the first bookable (Q1)": (
        [("quote", 0, 4, 7), ("done", 0), ("quote", 0, 7, 4), ("done", 0), ("click", 1, "valid", 0),
         ("click", 0, "valid", 0)],
        {"tickets": 2, "status": ["booked", "booked"]}),
    "a stopped turn's quote is never presented; a new quote is bookable (R6, B2)": (
        [("quote", 0, 4, 7), ("stop_turn", 0), ("click", 0, "valid", 0), ("click", 0, "garbage", 0),
         ("reconnect", 0), ("quote", 0, 4, 7), ("done", 0), ("click", 1, "valid", 0), ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["quoted", "booked"]}),
    "a quote presented earlier stays bookable on its own card after a stopped turn shows it (R6, Q2)": (
        [("quote", 0, 4, 7), ("done", 0), ("llm_request_booking", 0, 0), ("stop_turn", 0), ("reconnect", 0),
         ("click", 0, "valid", 0)],
        {"tickets": 1, "status": ["booked"]}),
    "a nonce of another quote is refused (B3)": (
        [("quote", 0, 4, 7), ("done", 0), ("quote", 0, 7, 4), ("done", 0), ("click", 0, "other_quote", 0),
         ("click", 1, "other_quote", 0)],
        {"tickets": 0, "status": ["presented", "presented"]}),
}


@pytest.mark.parametrize("name", REGRESSIONS)
def test_named_cases(tmp_path, name):
    sequence, expected = REGRESSIONS[name]
    sim = run(sequence, tmp_path)
    assert sim.w.ticket_count() == expected["tickets"]
    assert [q.status for q in sim.quotes] == expected["status"]


# ------------------------------------------------------------------------------- teeth --------
# Each mutant breaks one rule on purpose. The same seeds must catch every one of them; otherwise
# the model test would pass on a broken implementation.
class NoRecheck(Airline):  # B6
    def _recheck(self, quote):
        return quote["network_fingerprint"], None


class ExpiryOffByOne(Airline):  # B4: bookable at exactly 15:00
    def _expired(self, quote, now):
        return quote["status"] in OPEN and now > parse_iso(quote["expires_at"])


class AnyNonce(Airline):  # B3
    def _match_nonce(self, con, quote, nonce):
        cards = records.quote_cards(con, quote["id"])
        return cards[0]["message_id"] if cards else None


class LlmBooks(Airline):  # B3: the tool books by itself
    def request_booking(self, user_id, conversation_id, quote_id):
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
            cards = records.quote_cards(con, quote_id) if quote else []
        if quote and cards and quote["user_id"] == user_id:
            try:
                self.book(user_id, quote_id, self._nonce(quote, cards[0]["message_id"]))
            except BookingError:
                pass
        return super().request_booking(user_id, conversation_id, quote_id)


class Supersedes(Airline):  # Q1: a new quote closes the older ones of the conversation
    def quote_route(self, user_id, conversation_id, origin, destination):
        result = super().quote_route(user_id, conversation_id, origin, destination)
        if result.ok:
            with self.store.write() as con:
                con.execute("update quotes set status = 'expired' where conversation_id = ? and id <> ? "
                            "and status in ('quoted', 'presented')", (conversation_id, result.data["quote_id"]))
        return result


class PresentsOnQuote(Airline):  # B2: bookable before the card went out with done
    def quote_route(self, user_id, conversation_id, origin, destination):
        result = super().quote_route(user_id, conversation_id, origin, destination)
        if result.ok:
            with self.store.write() as con:
                con.execute("update quotes set status = 'presented' where id = ?", (result.data["quote_id"],))
        return result


class NoOwnerCheck(Airline):  # B1: anybody holding the nonce can book
    def book(self, user_id, quote_id, nonce, **kwargs):
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
        return super().book(quote["user_id"] if quote else user_id, quote_id, nonce, **kwargs)


class NonceLeaksToTheTool(Airline):  # B3: the LLM gets to see the nonce
    def request_booking(self, user_id, conversation_id, quote_id):
        result = super().request_booking(user_id, conversation_id, quote_id)
        with self.store.read() as con:
            quote = records.get_quote(con, quote_id)
            cards = records.quote_cards(con, quote_id) if quote else []
        if cards:
            result.data["debug"] = self._nonce(quote, cards[0]["message_id"])
        return result


class ForgetsToStoreStale(Airline):  # B6/Q3: a stale quote stays presented, bookable after a restore
    def _recheck(self, quote):
        fingerprint, problem = super()._recheck(quote)
        if problem is not None:
            raise _Refuse()
        return fingerprint, None

    def book(self, *args, **kwargs):
        try:
            return super().book(*args, **kwargs)
        except _Refuse:
            raise BookingError("stale") from None


class _Refuse(Exception):
    pass


class UnreadableMeansFresh(Airline):  # B6: books from the stored quote when the data cannot be read
    def _recheck(self, quote):
        try:
            return super()._recheck(quote)
        except NetworkDataError:
            return quote["network_fingerprint"], None


class SkipsBookedCheck(Airline):  # B5: a second click on a booked quote is answered as a booking
    def _book(self, con, user_id, quote_id, nonce):
        quote = records.get_quote(con, quote_id)
        if quote and quote["user_id"] == user_id and quote["status"] == "booked":
            ticket = records.get_ticket_for_quote(con, quote_id)
            last = records.list_messages(con, quote["conversation_id"])[-1]
            return {"ticket": airline_cards.ticket_card(ticket),
                    "message": self._message_view(con, user_id, last, self.store.clock())}
        return super()._book(con, user_id, quote_id, nonce)


class TicketWithoutStatusChange(Airline):  # B7: writes the ticket, leaves the quote presented
    def _book(self, con, user_id, quote_id, nonce):
        real = records.set_quote_status

        def skip_booked(con_, quote, status, now, reason=None):
            if status != "booked":
                real(con_, quote, status, now, reason)

        records.set_quote_status = skip_booked  # inside the write transaction: one thread at a time
        try:
            return super()._book(con, user_id, quote_id, nonce)
        finally:
            records.set_quote_status = real


class PresentsStoppedTurns(Airline):  # R6: a stopped turn's cards become bookable
    def post_assistant_message(self, *args, present=True, **kwargs):
        return super().post_assistant_message(*args, present=True, **kwargs)


# mutant -> the rule its first detected failure must name
MUTANTS = {
    NoRecheck: "B6", ExpiryOffByOne: "B4", AnyNonce: "B3", LlmBooks: "B3", Supersedes: "Q1",
    PresentsOnQuote: "B2", NoOwnerCheck: "B1", NonceLeaksToTheTool: "B3", ForgetsToStoreStale: "B6",
    UnreadableMeansFresh: "B6", SkipsBookedCheck: "B5", TicketWithoutStatusChange: "B7",
    PresentsStoppedTurns: "R6",
}


@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.__name__)
def test_the_model_test_catches_a_broken_implementation(tmp_path_factory, mutant):
    """Caught, and for the right reason: the failure names the rule the mutant breaks."""
    for seed in SMALL_SEEDS:
        try:
            run(gen_sequence(seed, SMALL_LENGTH), tmp_path_factory.mktemp("mutant"), airline_cls=mutant)
        except AssertionError as exc:
            assert MUTANTS[mutant] in str(exc), f"{mutant.__name__} was caught, but not as {MUTANTS[mutant]}: {exc}"
            return
    pytest.fail(f"{mutant.__name__} survived every seed: the model test has a blind spot")
