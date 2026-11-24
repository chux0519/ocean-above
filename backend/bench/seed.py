"""Seed an isolated app.db for one benchmark run, through the app's own code paths.

Every user gets one conversation with some history and `quotes_per_user` presented quotes (each
in its own assistant message, with the nonce the card got at `done`), so bookings can start at
once; chat turns during the run add more."""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from app.airline.service import Airline
from app.api import session
from app.core.network import NetworkProvider
from app.core.routing import Route, cheapest_route
from app.store import Store

from .common import FAKE_REPLY, NONCE_SECRET, SESSION_SECRET
from .fake_llm import user_text


def city_pairs(locations: Path, rng: random.Random, count: int = 200) -> list[tuple[str, str]]:
    """`count` (origin, destination) pairs of served cities that have a route, deterministic in `rng`."""
    net = NetworkProvider(locations).current()
    served = sorted(net.served)
    pairs: list[tuple[str, str]] = []
    while len(pairs) < count:
        a, b = rng.sample(served, 2)
        if isinstance(cheapest_route(net, a, b), Route):
            pairs.append((a, b))
    return pairs


def seed(work: Path, *, users: int, quotes_per_user: int, history_turns: int, pairs: list,
         rng: random.Random) -> dict:
    """Fresh app.db + locations.db copy in `work`; returns the client-side view of the seed."""
    work.mkdir(parents=True, exist_ok=True)
    for name in ("app.db", "app.db-wal", "app.db-shm"):
        (work / name).unlink(missing_ok=True)
    locations = work / "locations.db"
    if not locations.exists():
        raise FileNotFoundError(locations)
    store = Store(work / "app.db")
    airline = Airline(store, NetworkProvider(locations), nonce_secret=NONCE_SECRET)
    out = []
    for i in range(users):
        user = store.upsert_user("dev", f"bench-user-{i:03d}", f"Bench user {i}")
        conv = store.create_conversation(user["id"], f"bench {i}")["id"]
        for _ in range(history_turns):
            a, b = rng.choice(pairs)
            store.add_message(user["id"], conv, "user", user_text(a, b))
            store.add_message(user["id"], conv, "assistant", FAKE_REPLY)
        quotes = []
        for _ in range(quotes_per_user):
            a, b = rng.choice(pairs)
            result = airline.quote_route(user["id"], conv, a, b)
            assert result.code == "ok", (a, b, result.code)
            message = airline.post_assistant_message(user["id"], conv, FAKE_REPLY, result.cards)
            state = message["cards"][0]["state"]
            quotes.append({"id": message["cards"][0]["id"], "nonce": state["nonce"]})
        cookie = session.issue(SESSION_SECRET, user["id"], 24 * 3600, epoch=user["session_epoch"])
        out.append({"id": user["id"], "conversation": conv, "cookie": cookie, "quotes": quotes})
    return {"users": out}


def prepare_locations(work: Path, source: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    target = work / "locations.db"
    shutil.copyfile(source, target)
    return target
