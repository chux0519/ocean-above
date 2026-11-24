"""Plays cases against an LLM and collects what happened. Every run gets its own app.db and a
private copy of locations.db; the airline, the agent loop and the ReplyGuard are the real ones."""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agent import Agent, CityIndex, ReplyGuard
from app.agent.context import build_context
from app.airline import Airline
from app.core.network import NetworkProvider
from app.store import Store

from . import pricing
from .cases import Case, Click, EditDb, Turn
from .checks import SPELLED_NUMBER, ToolRecord, TurnRun, invariant_violations, turn_failures
from .fake import ScriptedLLM

REPO_ROOT = Path(__file__).resolve().parents[2]
# the price of the production model (pricing.json); the default of the cost estimate
DEFAULT_PRICE = pricing.load().price("deepseek", "deepseek-flash")
# tool name -> the airline method behind it (for injected tool failures)
AIRLINE_METHOD = {"find_city": "find_city", "quote_route": "quote_route", "request_booking": "request_booking",
                  "list_my_tickets": "list_tickets"}
STREAM_EVENTS = {"user_message", "status", "card", "delta", "replace", "ping", "done", "error"}


@dataclass
class RunResult:
    case: str
    index: int
    turns: list[TurnRun] = field(default_factory=list)
    turn_failures: list[list[str]] = field(default_factory=list)  # per chat turn
    invariants: dict[str, list[str]] = field(default_factory=dict)
    crashed: str | None = None
    # provider-neutral token usage (pricing.USAGE_KEYS): input includes cached, output includes reasoning
    usage: dict[str, int] = field(default_factory=lambda: dict.fromkeys(pricing.USAGE_KEYS, 0))
    llm_calls: int = 0
    retries: int = 0  # LLM calls that failed (the loop retried them or gave up)
    llm_latency_ms: list[int] = field(default_factory=list)
    config: str = ""  # the benchmark configuration that played it ("" outside a benchmark)

    @property
    def passed(self) -> bool:
        return not self.crashed and not self.invariants and not any(self.turn_failures)

    @property
    def tokens(self) -> int:
        return self.usage["input"] + self.usage["output"]

    def cost(self, price: "pricing.Price | None" = DEFAULT_PRICE) -> float | None:
        """USD, or None when the price is unknown."""
        return pricing.cost(price, self.usage)


# per chat turn, deliberately high: 2 LLM calls of ~2.5k input and ~0.6k output tokens each
# (measured in T3: ~$0.0003 per turn at effort none, ~$0.0004 high)
EST_CALLS_PER_TURN, EST_INPUT_PER_CALL, EST_OUTPUT_PER_CALL = 2, 2500, 600


def estimate_tokens(cases: list[Case], n: int) -> int:
    """An upper-bound guess of the tokens of `n` runs of `cases` (input + output)."""
    turns = sum(isinstance(s, Turn) for c in cases for s in c.steps)
    return turns * n * EST_CALLS_PER_TURN * (EST_INPUT_PER_CALL + EST_OUTPUT_PER_CALL)


def estimate_cost(cases: list[Case], n: int, price: "pricing.Price | None" = DEFAULT_PRICE) -> float | None:
    """The estimate above at the cache-miss rate; None when the price is unknown."""
    if price is None:
        return None
    turns = sum(isinstance(s, Turn) for c in cases for s in c.steps)
    return turns * n * EST_CALLS_PER_TURN * (EST_INPUT_PER_CALL * price.input_cache_miss
                                             + EST_OUTPUT_PER_CALL * price.output) / 1e6


def _quote_ids(store: Store, user: str, conv: str) -> list[str]:
    ids = []
    for message in store.list_messages(user, conv):
        if message["role"] == "user":
            for call in store.list_tool_calls(user, message["id"]):
                if (call["result"] or {}).get("quote_id"):
                    ids.append(call["result"]["quote_id"])
    return ids


def _newest_bookable(airline: Airline, user: str, conv: str):
    for message in reversed(airline.messages(user, conv)):
        for card in message["cards"]:
            if (card.get("state") or {}).get("bookable"):
                return card["id"], card["state"]["nonce"]
    return None


def _stream_problem(events: list) -> str | None:
    kinds = [e.name for e in events]
    if not kinds or kinds[0] != "user_message":
        return f"stream does not start with user_message: {kinds[:3]}"
    if sum(k in ("done", "error") for k in kinds) != 1 or kinds[-1] not in ("done", "error"):
        return "stream does not end in exactly one done/error"
    if set(kinds) - STREAM_EVENTS:
        return f"unknown events {set(kinds) - STREAM_EVENTS}"
    return None


def _flaky(method, fails: int):
    """`method`, raising for its first `fails` calls (an injected tool failure)."""
    left = [fails]

    def call(*args, **kwargs):
        if left[0] > 0:
            left[0] -= 1
            raise RuntimeError("injected tool failure (eval)")
        return method(*args, **kwargs)
    return call


def _edit_db(path: Path, statements: tuple[str, ...]) -> None:
    con = sqlite3.connect(path)
    try:
        with con:
            for sql in statements:
                con.execute(sql)
    finally:
        con.close()


async def play(case: Case, index: int, llm: Any, workdir: Path, *, effort: str | None = None,
               config: str = "") -> RunResult:
    result = RunResult(case.name, index, config=config)
    try:
        await _play(case, result, llm, workdir, effort)
    except Exception:  # a crash is a failed run, not a failed report
        result.crashed = traceback.format_exc(limit=6)
    return result


async def _play(case: Case, result: RunResult, llm: Any, workdir: Path, effort: str | None) -> None:
    db = workdir / "locations.db"
    shutil.copyfile(REPO_ROOT / "locations.db", db)
    store = Store(workdir / "app.db")
    airline = Airline(store, NetworkProvider(db))
    for tool, fails in case.tool_failures:
        method = AIRLINE_METHOD[tool]
        setattr(airline, method, _flaky(getattr(airline, method), fails))
    agent = Agent(airline, llm, budget=case.budget)
    user = store.upsert_user("eval", f"{case.name}-{result.index}", "Eval")["id"]
    conv = store.create_conversation(user)["id"]
    index = CityIndex(airline.networks.current().cities)
    clicks: list[tuple[int, bool, int]] = []

    for step_no, step in enumerate(case.steps):
        before = len(airline.tickets(user))
        if isinstance(step, Click):
            target = _newest_bookable(airline, user, conv)
            if target:
                await asyncio.to_thread(airline.book, user, *target)
            clicks.append((step_no, target is not None, len(airline.tickets(user)) - before))
            continue
        if isinstance(step, EditDb):  # the flight data changes between turns (a stale quote)
            await asyncio.to_thread(_edit_db, db, step.sql)
            continue
        quotes = _quote_ids(store, user, conv)
        turn = TurnRun(step.user, quote_before=quotes[-1] if quotes else None, tickets_before=before,
                       quotes_before=quotes)
        started = time.monotonic()
        events = []
        async for event in agent.stream_turn(user, conv, step.user, reasoning_effort=effort):
            if event.name == "delta" and turn.first_token_s is None:
                turn.first_token_s = time.monotonic() - started
            events.append(event)
        turn.message_id = events[0].data["id"]
        await agent.wait_idle()
        turn.latency_s = time.monotonic() - started
        turn.stream_problem = _stream_problem(events)
        turn.tickets_after = len(airline.tickets(user))
        last = events[-1]
        stored = store.list_messages(user, conv)[-1]
        if last.name == "error":
            turn.outcome, turn.error = "error", last.data.get("code")
        else:
            turn.text = last.data["message"]["text"]
            turn.card_types = [c["type"] for c in last.data["message"]["cards"]]
            turn.problems = stored["guard_problems"] or []
            turn.outcome = "fallback" if stored["guard_problems"] else "sent"
        for call in store.list_tool_calls(user, events[0].data["id"]):
            code = (call["result"] or {}).get("code", "tool_failed")
            turn.tools.append(ToolRecord(call["name"], call["args"], code, (call["result"] or {}).get("quote_id")))
        if turn.outcome == "sent":  # an independent pass: did what the user got really pass the guard?
            context = await asyncio.to_thread(build_context, store, user, conv)
            guard = ReplyGuard(index, records=context.records, user_text=step.user,
                               earlier_user_texts=context.user_texts[:-1], today=store.clock().date())
            turn.recheck = guard.check(turn.text)
        result.turns.append(turn)
        result.turn_failures.append(turn_failures(step, turn))

    result.invariants = invariant_violations(result.turns, clicks)
    calls = store.list_llm_calls(user, conv)
    by_turn = {t.message_id: t for t in result.turns}
    for call in calls:  # per-turn token charge (budget.turn_tokens grows within a turn)
        turn = by_turn.get((call["request"] or {}).get("turn"))
        charged = ((call["response"] or {}).get("budget") or {}).get("turn_tokens")
        if turn is not None:
            turn.llm_steps += 1
            turn.turn_tokens = max(turn.turn_tokens, charged or 0)
    for call in calls:
        response = call["response"] or {}
        for key, value in pricing.normalize_usage(response.get("usage")).items():
            result.usage[key] += value
        result.llm_calls += 1
        result.retries += "error" in response
        if call["latency_ms"] is not None:
            result.llm_latency_ms.append(call["latency_ms"])


def spelled_numbers(turns: list[TurnRun]) -> int:
    return sum(bool(SPELLED_NUMBER.search(t.text)) for t in turns if t.outcome == "sent")


def scripted(case: Case) -> ScriptedLLM:
    return ScriptedLLM([step for s in case.steps if isinstance(s, Turn) for step in s.fake])


async def run_all(cases: list[Case], n: int, *, mode: str, llm: Any = None, effort: str | None = None,
                  parallel: int = 1, progress=None) -> list[RunResult]:
    """`mode` fake: a scripted LLM per run. real: the shared `llm` client."""
    gate = asyncio.Semaphore(parallel)
    root = Path(tempfile.mkdtemp(prefix="oa-evals-"))

    async def one(case: Case, i: int) -> RunResult:
        async with gate:
            workdir = root / f"{case.name}-{i}"
            workdir.mkdir()
            res = await play(case, i, scripted(case) if mode == "fake" else llm, workdir, effort=effort)
            if progress:
                progress(res)
            return res

    try:
        return list(await asyncio.gather(*(one(c, i) for c in cases for i in range(n))))
    finally:
        shutil.rmtree(root, ignore_errors=True)
