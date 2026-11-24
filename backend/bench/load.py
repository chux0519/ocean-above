"""Workload generators: closed-loop HTTP clients (httpx + asyncio), one seeded user per client.

Every request is recorded as a `Sample` (endpoint, HTTP status, error code, seconds). A chat turn
counts as done when its SSE stream ends; its `done` message carries a presented quote and nonce,
which goes to that user's booking pool."""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import httpx

from app.api import session

from .fake_llm import user_text

# 70 % reads / 20 % chat turns (messages, LLM + tool logs, quote) / 10 % bookings
MIX = (("history", 0.35), ("tickets", 0.20), ("conversations", 0.15), ("chat", 0.20), ("book", 0.10))


@dataclass
class Sample:
    op: str
    status: int
    code: str | None  # error code (HTTP body or SSE error event), None on success
    seconds: float
    end: float


@dataclass
class Client:
    user: dict
    pool: deque
    rng: random.Random
    headers: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.headers = {"Cookie": f"{session.COOKIE}={self.user['cookie']}"}


@dataclass
class Bookings:
    """Client-side record of booking outcomes, for the integrity check."""
    ok: Counter = field(default_factory=Counter)  # quote id -> 200 responses
    codes: Counter = field(default_factory=Counter)

    def add(self, quote_id: str, sample: Sample) -> None:
        if sample.status == 200:
            self.ok[quote_id] += 1
        self.codes[sample.code or "ok"] += 1


def clients_for(seed: dict, count: int, base_seed: int) -> list[Client]:
    users = seed["users"]
    assert count <= len(users), "one user per client"
    return [Client(u, deque((q["id"], q["nonce"]) for q in u["quotes"]), random.Random(base_seed * 1000 + i))
            for i, u in enumerate(users[:count])]


def _error_code(res: httpx.Response) -> str | None:
    if res.status_code < 400:
        return None
    try:
        return res.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return f"http_{res.status_code}"


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) >= 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "):
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


async def op_read(http: httpx.AsyncClient, c: Client, which: str) -> Sample:
    path = {"history": f"/api/conversations/{c.user['conversation']}", "tickets": "/api/tickets",
            "conversations": "/api/conversations"}[which]
    t = time.perf_counter()
    res = await http.get(path, headers=c.headers)
    end = time.perf_counter()
    return Sample(which, res.status_code, _error_code(res), end - t, end)


async def op_chat(http: httpx.AsyncClient, c: Client, pairs: list, conversation: str | None = None) -> Sample:
    origin, destination = c.rng.choice(pairs)
    t = time.perf_counter()
    res = await http.post(f"/api/conversations/{conversation or c.user['conversation']}/messages",
                          json={"text": user_text(origin, destination)}, headers=c.headers)
    end = time.perf_counter()
    code = _error_code(res)
    if res.status_code == 200:
        events = parse_sse(res.text)
        last = events[-1] if events else ("none", {})
        if last[0] == "error":
            code = last[1].get("code", "sse_error")
        elif last[0] != "done":
            code = "sse_truncated"
        else:
            for card in last[1]["message"]["cards"]:
                state = card.get("state") or {}
                if state.get("bookable") and state.get("nonce"):
                    c.pool.append((card["id"], state["nonce"]))
    return Sample("chat", res.status_code, code, end - t, end)


async def op_book(http: httpx.AsyncClient, c: Client, quote: tuple[str, str], bookings: Bookings) -> Sample:
    quote_id, nonce = quote
    t = time.perf_counter()
    res = await http.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}, headers=c.headers)
    end = time.perf_counter()
    sample = Sample("book", res.status_code, _error_code(res), end - t, end)
    bookings.add(quote_id, sample)
    return sample


async def mixed(http: httpx.AsyncClient, clients: list[Client], pairs: list, seconds: float,
                bookings: Bookings, counters: Counter, mix=MIX, before_book=None) -> list[Sample]:
    """Every client runs ops back to back (no think time) until `seconds` have passed.
    `before_book`: an async function awaited before every click (not timed)."""
    samples: list[Sample] = []
    deadline = time.perf_counter() + seconds
    names = [m[0] for m in mix]
    weights = [m[1] for m in mix]

    async def run(c: Client) -> None:
        while time.perf_counter() < deadline:
            op = c.rng.choices(names, weights)[0]
            if op == "book":
                if not c.pool:
                    counters["book_pool_empty"] += 1
                    op = "history"
                else:
                    if before_book is not None:
                        await before_book()
                    samples.append(await op_book(http, c, c.pool.popleft(), bookings))
                    continue
            if op == "chat":
                samples.append(await op_chat(http, c, pairs))
            else:
                samples.append(await op_read(http, c, op))

    await asyncio.gather(*(run(c) for c in clients))
    return samples


async def burst(http: httpx.AsyncClient, clients: list[Client], bookings: Bookings) -> list[Sample]:
    """Every client clicks one of its own quotes at the same moment (distinct users and quotes)."""
    ready = [c for c in clients if c.pool]
    return list(await asyncio.gather(*(op_book(http, c, c.pool.popleft(), bookings) for c in ready)))


async def double_clicks(http: httpx.AsyncClient, clients: list[Client], bookings: Bookings,
                        clicks: int = 4) -> tuple[list[Sample], dict]:
    """`clicks` identical clicks per quote, all at once, for one quote of every client."""
    jobs, quotes = [], []
    for c in clients:
        if c.pool:
            q = c.pool.popleft()
            quotes.append(q[0])
            jobs += [op_book(http, c, q, bookings) for _ in range(clicks)]
    samples = list(await asyncio.gather(*jobs))
    allowed = {None, "already_booked", "conversation_busy"}
    return samples, {
        "quotes": len(quotes), "clicks_per_quote": clicks,
        "quotes_with_more_than_one_success": sum(1 for q in quotes if bookings.ok[q] > 1),
        "quotes_with_no_success": sum(1 for q in quotes if bookings.ok[q] == 0),
        "unexpected_codes": dict(Counter(s.code for s in samples if s.code not in allowed)),
    }


async def concurrent_turns(http: httpx.AsyncClient, clients: list[Client], pairs: list,
                           per_conversation: int = 3) -> list[Sample]:
    """`per_conversation` chat turns at once in each client's conversation, plus one click there."""
    jobs = []
    for c in clients:
        jobs += [op_chat(http, c, pairs) for _ in range(per_conversation)]
    return list(await asyncio.gather(*jobs))


async def turn_and_click(http: httpx.AsyncClient, clients: list[Client], pairs: list,
                         bookings: Bookings) -> list[Sample]:
    """A chat turn and a booking click of the same conversation at the same moment."""
    jobs = []
    for c in clients:
        if c.pool:
            jobs += [op_chat(http, c, pairs), op_book(http, c, c.pool.popleft(), bookings)]
    return list(await asyncio.gather(*jobs))
