"""Shared scaffolding for the store / airline tests: fake clock, a small network, a world with
its own app.db and its own copy of a locations database, and a contract-fixture shape checker."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.airline import Airline
from app.core.network import NetworkProvider
from app.store import Store

from .conftest import REAL_DB, make_db

FIXTURES = Path(__file__).resolve().parents[2] / "contract" / "fixtures"
START = datetime(2026, 11, 23, 8, 0, 0, tzinfo=timezone.utc)
SECRET = b"test-secret-0123456789"

# H1(0,0) -- H2(30,0) -- H3(30,40) -- H4(90,40) hub chain, plus H1 -- H3 (50 km).
# A, B: non-hubs on H1.  C: non-hub on H3 and H4.  D: non-hub on H4.  U: no service.
# I1 -- I2: an island of two hubs, E: non-hub on I1.
SMALL_LOCATIONS = [
    ("H1", "0,0", 1, ""), ("H2", "30,0", 1, ""), ("H3", "30,40", 1, ""), ("H4", "90,40", 1, ""),
    ("A", "0,-10", 0, "H1"), ("B", "-10,0", 0, "H1"), ("C", "60,40", 0, "H3;H4"),
    ("D", "90,50", 0, "H4"), ("U", "5,5", 0, ""),
    ("I1", "500,500", 1, ""), ("I2", "503,504", 1, ""), ("E", "500,510", 0, "I1"),
]
SMALL_LINKS = [("H1", "H2"), ("H2", "H3"), ("H3", "H4"), ("H1", "H3"), ("I1", "I2")]


class FakeClock:
    def __init__(self, start: datetime = START):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 0, minutes: float = 0) -> None:
        self.now += timedelta(seconds=seconds, minutes=minutes)


class World:
    """One app.db, one private locations database, two users with a conversation each."""

    def __init__(self, tmp_path: Path, real: bool = False, airline_cls=Airline, **airline_kwargs):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.dir = tmp_path
        self.db = tmp_path / "locations.db"
        if real:
            shutil.copyfile(REAL_DB, self.db)
        else:
            make_db(self.db, SMALL_LOCATIONS, SMALL_LINKS)
        self._mtime = self.db.stat().st_mtime_ns
        self.clock = FakeClock()
        self.store = Store(tmp_path / "app.db", clock=self.clock)
        self.networks = NetworkProvider(self.db)
        airline_kwargs.setdefault("nonce_secret", SECRET)
        self.airline = airline_cls(self.store, self.networks, **airline_kwargs)
        self.alice = self.store.upsert_user("dev", "alice", "Alice")["id"]
        self.bob = self.store.upsert_user("dev", "bob", "Bob")["id"]
        self.conv = self.store.create_conversation(self.alice)["id"]
        self.bob_conv = self.store.create_conversation(self.bob)["id"]

    # --- shortcuts for the common flow ---
    def quote(self, origin="A", destination="D", user=None, conv=None):
        result = self.airline.quote_route(user or self.alice, conv or self.conv, origin, destination)
        assert result.ok, result
        return result

    def done(self, cards, user=None, conv=None, text="Here you go."):
        return self.airline.post_assistant_message(user or self.alice, conv or self.conv, text, cards)

    def presented(self, origin="A", destination="D", user=None, conv=None):
        """Quote and present it. Returns (quote_id, nonce)."""
        result = self.quote(origin, destination, user, conv)
        message = self.done(result.cards, user, conv)
        state = message["cards"][0]["state"]
        assert state["bookable"] and state["nonce"]
        return result.data["quote_id"], state["nonce"]

    # --- direct reads of app.db ---
    def sql(self, query, args=()):
        con = sqlite3.connect(self.store.path)
        try:
            return con.execute(query, args).fetchall()
        finally:
            con.close()

    def status(self, quote_id):
        return self.sql("select status from quotes where id = ?", (quote_id,))[0][0]

    def ticket_count(self):
        return self.sql("select count(*) from tickets")[0][0]

    # --- changes to the private locations database ---
    def edit_db(self, *statements):
        """Run SQL on the locations copy; each statement is a string or (sql, args)."""
        con = sqlite3.connect(self.db)
        try:
            for statement in statements:
                sql, args = (statement, ()) if isinstance(statement, str) else statement
                con.execute(sql, args)
            con.commit()
        finally:
            con.close()
        self.bump_mtime()

    def bump_mtime(self):
        """Give the file a strictly newer mtime, so change detection never depends on how fast
        the test runs (the same-mtime case has its own test)."""
        self._mtime = max(self._mtime + 1_000_000_000, self.db.stat().st_mtime_ns)
        os.utime(self.db, ns=(self._mtime, self._mtime))

    def replace_db(self, content: bytes):
        """Replace the file atomically (new inode), as a deploy would."""
        tmp = self.dir / "locations.db.new"
        tmp.write_bytes(content)
        os.replace(tmp, self.db)
        self.bump_mtime()


# --- contract fixture shapes -------------------------------------------------------------------
CARD_TYPES = ("quote", "booking_request", "ticket", "ticket_list", "no_service", "ambiguous_city",
              "unknown_city", "notice")
_FREE_FORM = {"filter"}  # Record<string, string>


def _learn(node, value):
    node = node or {"types": set(), "keys": None, "items": None}
    if isinstance(value, dict):
        node["types"].add("dict")
        if node["keys"] is None:
            node["keys"] = {}
        for key, sub in value.items():
            node["keys"][key] = _learn(node["keys"].get(key), sub)
        node.setdefault("keysets", set()).add(frozenset(value))
    elif isinstance(value, list):
        node["types"].add("list")
        for item in value:
            node["items"] = _learn(node["items"], item)
    else:
        node["types"].add(type(value).__name__)
    return node


def _walk(value):
    if isinstance(value, dict):
        yield value
        for sub in value.values():
            yield from _walk(sub)
    elif isinstance(value, list):
        for sub in value:
            yield from _walk(sub)


def _fixture_documents():
    for path in sorted(FIXTURES.glob("*.json")):
        yield path.name, json.loads(path.read_text(encoding="utf-8"))
    for line in (FIXTURES / "sse_quote_turn.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("data: "):
            yield "sse", json.loads(line[len("data: "):])


def fixture_schemas() -> dict:
    """name -> shape learned from every example in contract/fixtures: the eight card types plus
    `message`, `trace`, `booking_ok` and `error`."""
    schemas: dict = {}
    for name, doc in _fixture_documents():
        for obj in _walk(doc):
            if obj.get("type") in CARD_TYPES:
                # contract: BookingRequestCard = Omit<QuoteCard, "type"> & {type}, so both card
                # types learn from the examples of either
                name_ = "quote" if obj["type"] == "booking_request" else obj["type"]
                schemas[name_] = _learn(schemas.get(name_), obj)
            elif {"role", "text", "cards"} <= set(obj):
                message = {k: v for k, v in obj.items() if k != "cards"}
                schemas["message"] = _learn(schemas.get("message"), message)
        if name == "trace.json":
            schemas["trace"] = _learn(None, doc)
        elif name == "booking_ok.json":
            schemas["booking_ok"] = _learn(None, {k: None for k in doc})
        elif name == "booking_error_stale.json":
            schemas["error"] = _learn(None, doc)
    schemas["booking_request"] = schemas["quote"]
    return schemas


def assert_shape(node, value, path="$"):
    """`value` has exactly the keys of the fixture examples at every level, and every leaf has a
    type some example has there."""
    kind = "dict" if isinstance(value, dict) else "list" if isinstance(value, list) else type(value).__name__
    assert kind in node["types"], f"{path}: {kind} not in {sorted(node['types'])}"
    if kind == "dict":
        if path.rsplit(".", 1)[-1] in _FREE_FORM:
            assert all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()), path
            return
        assert frozenset(value) in node["keysets"], f"{path}: keys {sorted(value)} not in the contract"
        for key, sub in value.items():
            assert_shape(node["keys"][key], sub, f"{path}.{key}")
    elif kind == "list":
        for i, item in enumerate(value):
            assert node["items"] is not None, f"{path}: no fixture example for the items"
            assert_shape(node["items"], item, f"{path}[{i}]")


def assert_card(card: dict, schemas: dict | None = None):
    schemas = schemas or fixture_schemas()
    assert card["type"] in CARD_TYPES, card
    assert_shape(schemas[card["type"]], card, card["type"])


def assert_message(message: dict, schemas: dict | None = None):
    schemas = schemas or fixture_schemas()
    assert_shape(schemas["message"], {k: v for k, v in message.items() if k != "cards"}, "message")
    assert isinstance(message["cards"], list)
    for card in message["cards"]:
        assert_card(card, schemas)
