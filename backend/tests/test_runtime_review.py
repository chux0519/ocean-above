"""06 runtime review fixes (#43, report docs/iterations/06-hardening/review-runtime.md): one named
test (or a few) per finding P1-1 .. P2-9 and per listed test gap. The airline and app.db are real;
the LLM is the scripted fake."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agent import Agent, Completion, LLMError
from app.agent.budget import AgentBudget, BudgetTracker, estimate_tokens, reported_tokens
from app.airline import BookingError
from app.airline import service

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, first_data, first_sse, names, run_turn
from .airline_helpers import World


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


# ============================================================ P1-1 executor starvation =======
def test_p1_1_book_clicks_waiting_for_the_lock_hold_no_worker_thread(world, monkeypatch):
    """The review's scenario: a turn holds the conversation lock and more clicks than the
    executor has threads wait for it. Other `to_thread` work must not queue behind them."""
    monkeypatch.setattr(service, "BOOK_MAX_PENDING_PER_USER", 100)  # this test is about threads
    quote_id, nonce = world.presented()

    async def go():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=4))
        async with world.airline.locks.hold_async(world.conv):  # the running chat turn
            clicks = [asyncio.create_task(world.airline.book_async(world.alice, quote_id, "n_bogus",
                                                                   lock_timeout=1.0)) for _ in range(12)]
            await asyncio.sleep(0.2)  # every click is waiting for the lock now
            started = time.monotonic()
            await asyncio.to_thread(lambda: None)  # what every chat turn's tool / store call does
            lag = time.monotonic() - started
        results = await asyncio.gather(*clicks, return_exceptions=True)
        return lag, results

    lag, results = asyncio.run(go())
    assert lag < 0.1, f"a worker thread was free only after {lag:.2f}s"
    assert {r.code for r in results} == {"invalid_nonce"}  # every click got its normal answer
    assert world.status(quote_id) == "presented" and world.ticket_count() == 0


def test_p1_1_at_most_two_clicks_per_user_are_in_progress(world):
    quote_id, nonce = world.presented()
    bob_quote, bob_nonce = world.presented(user=world.bob, conv=world.bob_conv)

    async def go():
        async with world.airline.locks.hold_async(world.conv):
            waiting = [asyncio.create_task(world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=2))
                       for _ in range(2)]
            await asyncio.sleep(0.05)
            started = time.monotonic()
            with pytest.raises(BookingError) as third:
                await world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=2)
            refused_after = time.monotonic() - started
            bob = await world.airline.book_async(world.bob, bob_quote, bob_nonce)  # other users unaffected
        results = await asyncio.gather(*waiting, return_exceptions=True)
        return third.value.code, refused_after, bob, results

    code, refused_after, bob, results = asyncio.run(go())
    assert code == "conversation_busy" and refused_after < 0.5  # refused at once, nothing changed
    assert bob["ticket"]["quote_id"] == bob_quote
    booked = [r for r in results if isinstance(r, dict)]
    assert len(booked) == 1 and booked[0]["ticket"]["quote_id"] == quote_id  # the same nonce books once
    assert [r.code for r in results if isinstance(r, BookingError)] == ["already_booked"]
    assert world.airline._booking == {}  # the counter is back to zero


def test_p1_1_a_cancelled_click_keeps_the_lock_until_its_transaction_ended(world):
    quote_id, nonce = world.presented()
    entered, release = threading.Event(), threading.Event()
    real = world.airline._book_transaction

    def slow(*args):
        entered.set()
        release.wait(5)
        return real(*args)

    world.airline._book_transaction = slow

    async def go():
        click = asyncio.create_task(world.airline.book_async(world.alice, quote_id, nonce))
        await asyncio.to_thread(entered.wait, 5)
        click.cancel()
        await asyncio.sleep(0.05)
        locked_while_running = world.airline.locks.locked(world.conv)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await click
        return locked_while_running

    assert asyncio.run(go()) is True
    assert not world.airline.locks.locked(world.conv)
    assert world.status(quote_id) == "booked" and world.ticket_count() == 1  # the transaction completed


def test_p1_1_book_checks_keep_their_order(world):
    """not_found comes before conversation_busy, in both forms."""
    quote_id, nonce = world.presented()

    async def go():
        async with world.airline.locks.hold_async(world.conv):
            with pytest.raises(BookingError) as missing:
                await world.airline.book_async(world.alice, "q_missing", nonce, lock_timeout=0.05)
            with pytest.raises(BookingError) as foreign:
                await world.airline.book_async(world.bob, quote_id, nonce, lock_timeout=0.05)
            with pytest.raises(BookingError) as busy:
                await world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=0.05)
        return missing.value.code, foreign.value.code, busy.value.code

    assert asyncio.run(go()) == ("not_found", "not_found", "conversation_busy")
    assert world.airline.book(world.alice, quote_id, nonce)["ticket"]["quote_id"] == quote_id


def test_p1_1_the_default_executor_is_sized_at_startup(world, no_real_llm):
    from app.agent import TurnConcurrency
    from app.agent.concurrency import executor_workers

    assert executor_workers(TurnConcurrency()) == 32
    assert executor_workers(TurnConcurrency(max_active_turns=40)) == 96
    c, app = client_for(world, turn_concurrency=TurnConcurrency(max_active_turns=20))
    with c as client:
        assert app.state.executor_workers == 56
        size = client.portal.call(lambda: asyncio.get_running_loop()._default_executor._max_workers)
        assert size == 56


# ============================================================ helpers =======================
@pytest.fixture
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


def client_for(world, llm=None, **settings):
    from fastapi.testclient import TestClient

    from app.api import Settings
    from app.main import create_app

    from .agent_helpers import FakeLLM
    from .airline_helpers import SECRET
    from .test_http_api import SECRET_KEY

    base = dict(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    base.update(settings)
    app = create_app(world.db, Settings(**base), clock=world.clock, llm=llm or FakeLLM(default=["Hello."]))
    return TestClient(app), app


# ============================================================ P1-2 failed attempts ==========


class SizedUsageLLM(FakeLLM):
    """Reports a realistic usage: prompt tokens from the request size (3.5 characters per token),
    150 completion tokens."""

    async def stream(self, messages, tools, *, reasoning_effort=None):
        async for event in super().stream(messages, tools, reasoning_effort=reasoning_effort):
            if isinstance(event, Completion):
                prompt = -(-len(json.dumps(messages) + json.dumps(tools)) * 2 // 7)
                event.usage = {"prompt_tokens": prompt, "completion_tokens": 150, "total_tokens": prompt + 150}
            yield event


class UsageLLM(FakeLLM):
    """Reports the same given usage block for every call."""

    def __init__(self, *steps, usage, **kwargs):
        super().__init__(*steps, **kwargs)
        self.usage = usage

    async def stream(self, messages, tools, *, reasoning_effort=None):
        async for event in super().stream(messages, tools, reasoning_effort=reasoning_effort):
            if isinstance(event, Completion):
                event.usage = dict(self.usage)
            yield event


async def _no_sleep(seconds):
    pass


def long_history(world, turns=12, chars=1150):
    """About 27.6k characters of earlier messages (the review's scenario)."""
    for i in range(turns):
        world.store.add_message(world.alice, world.conv, "user", f"Question {chr(65 + i)}: " + "tell me more " * (chars // 13))
        world.store.add_message(world.alice, world.conv, "assistant", "Some long answer text. " * (chars // 23))


def test_p1_2_one_failed_attempt_does_not_turn_a_long_conversation_into_the_template(world):
    long_history(world)
    no_body = LLMError("LLM HTTP 503", retryable=True, status=503)
    llm = SizedUsageLLM(no_body, [Call("quote_route", origin="A", destination="D")], ["Here is your quote."])
    agent = Agent(world.airline, llm, sleep=_no_sleep)  # the default budget
    assert agent.budget.max_total_tokens_per_turn == AgentBudget().max_total_tokens_per_turn
    events = run_turn(agent, world.alice, world.conv, "Fly me from A to D")
    message = assert_stream(events)
    assert len(json.dumps(llm.requests[0])) > 27_000  # a realistic context
    stored = world.store.list_messages(world.alice, world.conv)[-1]
    assert stored["guard_problems"] is None and message["text"].startswith("Here is your quote.")
    failed, *rest = world.store.list_llm_calls(world.alice, world.conv)
    assert failed["response"]["budget"]["charged_tokens"] == 0 and failed["tokens"] == 0
    assert [c["response"]["budget"]["estimated"] for c in rest] == [False, False]
    # the old rule (request / 2 + 4096) would have charged the failure alone this much:
    assert estimate_tokens(failed["request"]["messages"], 4096) > 17_000


def test_p1_2_the_client_reports_what_a_failed_call_received(tmp_path):
    """What a failed call is charged follows from what it received (since ADR-001 checked on the
    stored llm_calls of a wire-level turn, test_agent_llm.Wire, instead of on the old client)."""
    import httpx2

    from .test_agent_llm import Wire, delta, sse

    def status(request, n):
        return httpx2.Response(503, json={"error": {"message": "overloaded"}})

    def refused(request, n):
        raise httpx2.ConnectError("no route", request=request)

    def cut(request, n):  # 7 characters of reasoning, 3 of text, 9 of a tool call, then the stream ends
        return httpx2.Response(200, content=sse(
            delta(reasoning_content="Think. "), delta(content="Hel"),
            delta(tool_calls=[{"index": 0, "id": "c1", "type": "function", "function": {"name": "find", "arguments": "{\"a\":"}}]),
            {"choices": [], "usage": {"total_tokens": 0}}, done=False))

    for handler, received in ((status, None), (refused, None), (cut, 19)):
        w = Wire(tmp_path / handler.__name__, handler, retries=0)
        logged = w.world.store.list_llm_calls(w.world.alice, w.world.conv)
        assert len(logged) == 1, handler.__name__
        budget = logged[0]["response"]["budget"]
        if received is None:  # no response body: the provider generated nothing
            assert budget == {"charged_tokens": 0, "estimated": False, "turn_tokens": 0}, handler.__name__
        else:  # charged from the 19 characters received; a total of 0 is not a usable report (P2-1)
            assert budget["estimated"] is True
            assert budget["charged_tokens"] == estimate_tokens(logged[0]["request"]["messages"], 4096, received)


# ============================================================ P2-1 zero usage ===============
def test_p2_1_a_reported_total_of_zero_is_not_free(world):
    assert reported_tokens({"total_tokens": 0}) is None
    assert reported_tokens({"prompt_tokens": 900, "completion_tokens": 200, "total_tokens": 0}) == 1100
    assert reported_tokens({"prompt_tokens": 900, "completion_tokens": 200}) == 1100
    assert reported_tokens({"prompt_tokens": 900, "completion_tokens": 200, "total_tokens": 1500}) == 1500
    finds = [Call("find_city", name="A")]
    for usage in ({"total_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}):
        w = World(world.dir / f"z{len(usage)}")
        llm = UsageLLM(default=finds, usage=usage)
        agent = Agent(w.airline, llm, sleep=_no_sleep, budget=AgentBudget(max_total_tokens_per_turn=1000))
        run_turn(agent, w.alice, w.conv, "where is A")
        logged = w.store.list_llm_calls(w.alice, w.conv)
        assert len(llm.requests) == 1 and logged[0]["response"]["budget"]["estimated"] is True
        assert w.store.list_messages(w.alice, w.conv)[-1]["guard_problems"] == [
            {"kind": "token_budget", "value": "1000"}]  # before the fix: 6 steps at 0 tokens


# ============================================================ P2-2 retries =================
def test_p2_2_no_retry_starts_once_the_token_cap_is_reached(world):
    """One reported call uses the cap; its follow-up step fails mid-stream and is not retried."""
    cut = ["Hel", LLMError("LLM transport error: ReadError", retryable=True)]
    # a full usage block: Pydantic AI reads prompt + completion tokens (a total alone is dropped)
    llm = UsageLLM([Call("find_city", name="A")], cut, ["never sent"],
                   usage={"prompt_tokens": 850, "completion_tokens": 50, "total_tokens": 900})
    agent = Agent(world.airline, llm, sleep=_no_sleep, budget=AgentBudget(max_total_tokens_per_turn=1000))
    events = run_turn(agent, world.alice, world.conv, "where is A")
    assert_stream(events)
    assert len(llm.requests) == 2  # the step and its failed attempt; no retry
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "token_budget", "value": "1000"}]
    tracker = BudgetTracker(AgentBudget(max_total_tokens_per_turn=1000))
    assert tracker.out_of_tokens() is None
    tracker.charge(None, [], no_response=True)
    assert tracker.tokens == 0 and tracker.estimated_calls == 0


# ============================================================ P2-4 interrupted call ========
def test_p2_4_a_call_cut_by_the_turn_limit_is_recorded_and_charged(world):
    agent = Agent(world.airline, FakeLLM(["Hel", Wait(seconds=5)]), sleep=_no_sleep,
                  turn_timeout=0.2, llm_timeout=5)
    events = run_turn(agent, world.alice, world.conv, "hi")
    assert names(events)[-1] == "error" and events[-1].data["code"] == "llm_unavailable"
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert len(logged) == 1
    response = logged[0]["response"]
    assert response["aborted"] == "turn_timeout" and response["content"] == "Hel"
    assert response["budget"]["estimated"] is True
    assert logged[0]["tokens"] == response["budget"]["charged_tokens"] == estimate_tokens(
        logged[0]["request"]["messages"], 4096)
    assert world.store.tokens_today() >= logged[0]["tokens"]  # the site's daily budget sees it


# ============================================================ P2-9e site budget ============
def test_p2_9_the_site_budget_counts_what_the_turn_was_charged(world):
    no_body = LLMError("LLM HTTP 503", retryable=True, status=503)
    agent = Agent(world.airline, FakeLLM(no_body, ["Hello."]), sleep=_no_sleep)
    run_turn(agent, world.alice, world.conv, "hi")
    failed, ok = world.store.list_llm_calls(world.alice, world.conv)
    assert (failed["tokens"], ok["tokens"]) == (0, 110)
    assert world.store.tokens_today() == 110  # before: NULL, counted as request / 3 + 4096


# ============================================================ P2-3 tool-call flood =========
def test_p2_3_a_completion_with_hundreds_of_calls_runs_none_and_stores_none(world):
    from app.agent.context import build_context

    ran = []
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (ran.append(name), find_city(name))[1]
    flood = [Call("find_city", name="A") for _ in range(300)]
    llm = FakeLLM([Call("find_city", name="A")], flood, default=["never sent"])
    agent = Agent(world.airline, llm, sleep=_no_sleep)
    events = run_turn(agent, world.alice, world.conv, "where is A")
    assert_stream(events)
    assert len(ran) == 1 and len(llm.requests) == 2  # only step 1's call ran
    rows = world.store.list_messages(world.alice, world.conv)
    assert rows[-1]["guard_problems"] == [{"kind": "tool_budget", "value": "300"}]
    assert len(world.store.list_tool_calls(world.alice, rows[-2]["id"])) == 1  # no 300 refusal rows
    assert world.store.list_llm_calls(world.alice, world.conv)[-1]["response"]["tool_calls_not_run"] is True
    world.store.add_message(world.alice, world.conv, "user", "and now?")
    context = build_context(world.store, world.alice, world.conv)
    assert len(json.dumps(context.messages)) < 5_000  # before: about 115k characters
    calls = [m for m in context.messages if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(calls) == 1 and len(calls[0]["tool_calls"]) == 1


def test_p2_3_up_to_four_times_the_step_limit_still_gets_per_call_refusals(world):
    llm = FakeLLM([Call("find_city", name="A") for _ in range(16)], ["Done."])
    agent = Agent(world.airline, llm, sleep=_no_sleep)
    run_turn(agent, world.alice, world.conv, "where is A")
    rows = world.store.list_messages(world.alice, world.conv)
    calls = world.store.list_tool_calls(world.alice, rows[-2]["id"])
    assert len(calls) == 16 and sum(c["error"] == "budget: per_step" for c in calls) == 12
    assert rows[-1]["guard_problems"] is None and rows[-1]["text"] == "Done."


# ============================================================ P2-5 limit on the row write ==
def test_p2_5_the_time_limit_on_add_tool_call_keeps_the_card_in_the_reply(world):
    add_tool_call = world.store.add_tool_call

    def slow(*args, **kwargs):
        time.sleep(0.5)  # the 0.2 s turn limit passes during this write
        return add_tool_call(*args, **kwargs)

    world.store.add_tool_call = slow
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], default=["never sent"])
    agent = Agent(world.airline, llm, sleep=_no_sleep, turn_timeout=0.2)
    events = run_turn(agent, world.alice, world.conv, "A to D")
    message = assert_stream(events)
    rows = world.store.list_messages(world.alice, world.conv)
    assert rows[-1]["guard_problems"] == [{"kind": "turn_timeout", "value": "0.2"}]
    stored = world.store.list_tool_calls(world.alice, rows[-2]["id"])
    assert [c["name"] for c in stored] == ["quote_route"]  # written while draining
    quote_id = stored[0]["result"]["quote_id"]
    assert [c.get("id") for c in message["cards"]] == [quote_id]  # before: no card, quote never shown
    assert message["cards"][0]["state"]["bookable"] is True and world.status(quote_id) == "presented"
    assert "A" in message["text"] and "D" in message["text"]  # the template names the quote


# ============================================================ P2-6 default token cap =======
def test_p2_6_a_three_step_turn_at_the_history_budget_fits_the_default_cap(world):
    long_history(world, turns=17, chars=1150)  # about 40k characters: the full-history budget
    llm = SizedUsageLLM([Call("find_city", name="A")], [Call("quote_route", origin="A", destination="D")],
                        ["Here is your quote."])
    agent = Agent(world.airline, llm, sleep=_no_sleep)
    run_turn(agent, world.alice, world.conv, "Fly me from A to D")
    assert len(json.dumps(llm.requests[0])) > 38_000
    stored = world.store.list_messages(world.alice, world.conv)[-1]
    assert stored["guard_problems"] is None
    turn_tokens = world.store.list_llm_calls(world.alice, world.conv)[-1]["response"]["budget"]["turn_tokens"]
    assert turn_tokens < AgentBudget().max_total_tokens_per_turn == 60_000


# ============================================================ P2-8 turns per user ==========
def test_p2_8_one_user_cannot_take_more_than_two_slots(world):
    from app.agent import TurnConcurrency
    from app.agent.loop import USER_BUSY

    convs = [world.store.create_conversation(world.alice)["id"] for _ in range(3)]

    async def go():
        gate = asyncio.Event()
        llm = FakeLLM([Wait(gate), "One."], [Wait(gate), "Two."], default=["Bob."])
        agent = Agent(world.airline, llm, concurrency=TurnConcurrency(8, 0.1))
        first = agent.stream_turn(world.alice, convs[0], "one")
        second = agent.stream_turn(world.alice, convs[1], "two")
        await asyncio.sleep(0.05)
        third = [e async for e in agent.stream_turn(world.alice, convs[2], "three")]
        bob = [e async for e in agent.stream_turn(world.bob, world.bob_conv, "hi")]
        gate.set()
        rest = [[e async for e in s] for s in (first, second)]
        await agent.wait_idle()
        return third, bob, rest, agent

    third, bob, rest, agent = asyncio.run(go())
    assert names(third) == ["error"] and first_data(third[0].data) == {"code": "conversation_busy", "message": USER_BUSY}
    assert world.store.list_messages(world.alice, convs[2]) == []  # nothing stored
    assert names(bob)[-1] == "done" and [names(r)[-1] for r in rest] == ["done", "done"]
    assert agent._user_turns == {} and agent.slots.active == 0


def test_p2_8_turns_per_user_from_env():
    from app.agent import TurnConcurrency

    assert TurnConcurrency().max_turns_per_user == 2
    assert TurnConcurrency.from_env({"AGENT_MAX_TURNS_PER_USER": "5"}).max_turns_per_user == 5
    for bad in ("0", "1.5", "x", "300"):
        with pytest.raises(ValueError, match="AGENT_MAX_TURNS_PER_USER"):
            TurnConcurrency.from_env({"AGENT_MAX_TURNS_PER_USER": bad})


# ============================================================ P2-9 small items =============
def test_p2_9_stream_turn_does_not_read_app_db_on_the_event_loop_when_the_owner_was_checked(world):
    reads = []
    get_conversation = world.store.get_conversation
    world.store.get_conversation = lambda *a: (reads.append(threading.current_thread()), get_conversation(*a))[1]

    async def go():
        agent = Agent(world.airline, FakeLLM(default=["Hi."]))
        loop_thread = threading.current_thread()
        agent.stream_turn(world.alice, world.conv, "hi", owner_checked=True)
        on_loop = [t for t in reads if t is loop_thread]
        await agent.wait_idle()
        return on_loop

    assert asyncio.run(go()) == []
    assert reads and all(t is not threading.main_thread() for t in reads)  # the turn reads in a worker


def test_p2_9_a_message_app_db_could_not_store_is_reported_as_not_saved_and_refunded(world, no_real_llm):
    from app.agent.loop import NOT_STORED
    from app.store import StoreUnavailable

    from .test_http_api import parse_sse
    from .test_limits import as_user, send

    c, app = client_for(world)
    with c as client:
        as_user(client, world.alice)
        add_message = app.state.store.add_message

        def failing(user_id, conversation_id, role, text, *args, **kwargs):
            if role == "user":
                raise StoreUnavailable("database is locked")
            return add_message(user_id, conversation_id, role, text, *args, **kwargs)

        app.state.store.add_message = failing
        events = parse_sse(send(client, world.conv).text)
        assert first_sse(events) == [("error", {"code": "internal_error", "message": NOT_STORED})]
        assert client.get("/api/me").json()["usage"]["messages_today"] == 0  # refunded
        assert world.store.list_messages(world.alice, world.conv) == []


def test_p2_9_an_app_db_timeout_says_busy_not_route_data(world, no_real_llm):
    import sqlite3

    from app.api.errors import STORE_BUSY

    from .test_limits import as_user

    quote_id, nonce = world.presented()
    c, app = client_for(world)
    with c as client:
        as_user(client, world.alice)
        app.state.store.busy_timeout = 0.1
        other = sqlite3.connect(world.store.path, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        try:
            res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})
        finally:
            other.execute("ROLLBACK")
            other.close()
    assert res.status_code == 503
    assert res.json()["error"] == {"code": "data_unavailable", "message": STORE_BUSY}


# ============================================================ test gaps ====================
def test_gap_1_a_booking_interrupted_after_the_nonce_was_marked_writes_nothing(world, monkeypatch):
    """The failure comes after `mark_nonce_used` ran inside the transaction: everything rolls
    back, so the nonce is unused, the quote still `presented`, and the same click books later."""
    from app.store import StoreUnavailable, records

    quote_id, nonce = world.presented()
    real = records.insert_ticket
    marked = []

    def interrupted(con, **kwargs):
        marked.append(con.execute("SELECT nonce_used_at FROM quote_cards WHERE quote_id = ?",
                                  (quote_id,)).fetchone()[0])
        raise StoreUnavailable("interrupted")

    monkeypatch.setattr(records, "insert_ticket", interrupted)
    with pytest.raises(StoreUnavailable):
        world.airline.book(world.alice, quote_id, nonce)
    assert marked and marked[0] is not None  # the partial write had happened
    assert world.status(quote_id) == "presented" and world.ticket_count() == 0
    assert world.sql("SELECT nonce_used_at FROM quote_cards WHERE quote_id = ?", (quote_id,))[0][0] is None
    assert not world.airline.locks.locked(world.conv)
    monkeypatch.setattr(records, "insert_ticket", real)
    assert world.airline.book(world.alice, quote_id, nonce)["ticket"]["quote_id"] == quote_id


def test_gap_4_the_next_context_is_well_formed_after_a_turn_cut_short(world):
    from app.agent.context import build_context

    from .test_agent_budget import assert_well_formed

    release = threading.Event()
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (release.wait(0.6), find_city(name))[1]
    for step2 in ([Call("find_city", name="A")], ["Hel", Wait(seconds=5)]):  # cut in a tool, cut in a stream
        conv = world.store.create_conversation(world.alice)["id"]
        llm = FakeLLM([Call("quote_route", origin="A", destination="D")], step2, default=["Next."])
        agent = Agent(world.airline, llm, sleep=_no_sleep, turn_timeout=0.3, llm_timeout=5)
        events = run_turn(agent, world.alice, conv, "A to D")
        assert names(events)[-2:] == ["replace", "done"]
        assert world.store.list_messages(world.alice, conv)[-1]["guard_problems"] == [
            {"kind": "turn_timeout", "value": "0.3"}]
        events = run_turn(agent, world.alice, conv, "and now?")
        assert names(events)[-1] == "done"
        assert_well_formed(llm.requests[-1])
        assert_well_formed(build_context(world.store, world.alice, conv).messages)


def test_gap_5_a_thread_outliving_the_drain_ends_the_turn_with_llm_unavailable(world, caplog):
    import logging

    block = threading.Event()
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (block.wait(5), find_city(name))[1]

    async def go():
        agent = Agent(world.airline, FakeLLM([Call("find_city", name="A")], default=["Done."]),
                      turn_timeout=0.1, drain_timeout=0.2)
        started = time.monotonic()
        events = [e async for e in agent.stream_turn(world.alice, world.conv, "where is A")]
        took = time.monotonic() - started
        locked_after = world.airline.locks.locked(world.conv)
        slots_after = agent.slots.active
        block.set()
        await agent.wait_idle()
        return events, took, locked_after, slots_after

    with caplog.at_level(logging.WARNING, logger="app.agent.loop"):
        events, took, locked_after, slots_after = asyncio.run(go())
    assert names(events) == ["user_message", "status", "error"]
    assert events[-1].data["code"] == "llm_unavailable"  # the cut-short tool produced nothing
    assert 0.25 < took < 2  # the 0.1 s limit, then the 0.2 s drain, not the thread's 5 s
    assert not locked_after and slots_after == 0
    assert "still running after 0.2s" in caplog.text
    assert [m["text"] for m in world.store.list_messages(world.alice, world.conv)] == ["where is A"]


def test_gap_6_server_busy_refunds_even_when_the_client_left(world):
    from app.agent import TurnConcurrency

    refunds = []

    async def go():
        gate = asyncio.Event()
        agent = Agent(world.airline, FakeLLM([Wait(gate), "One."]), concurrency=TurnConcurrency(1, 0.05))
        running = agent.stream_turn(world.alice, world.conv, "one")
        await asyncio.sleep(0.02)
        agent.stream_turn(world.bob, world.bob_conv, "two", on_not_stored=lambda: refunds.append("bob"))
        # nobody reads bob's stream: the client is gone
        await asyncio.sleep(0.2)
        gate.set()
        [e async for e in running]
        await agent.wait_idle()

    asyncio.run(go())
    assert refunds == ["bob"] and world.store.list_messages(world.bob, world.bob_conv) == []


def test_gap_6_http_server_busy_under_real_concurrency(world, no_real_llm):
    """Two real requests at once; the second finds the only slot taken by the first."""
    from app.agent import TurnConcurrency
    from app.agent.loop import SERVER_BUSY

    from .test_http_api import parse_sse
    from .test_limits import as_user, send

    other = world.store.create_conversation(world.alice)["id"]
    llm = FakeLLM([Wait(seconds=0.5), "First."], default=["Again."])
    c, app = client_for(world, llm=llm, turn_concurrency=TurnConcurrency(1, 0.05))
    with c as client:
        as_user(client, world.alice)
        first = {}
        worker = threading.Thread(target=lambda: first.setdefault("res", send(client, world.conv)))
        worker.start()
        for _ in range(100):  # until the first turn holds the slot
            if app.state.agent.slots.active:
                break
            time.sleep(0.01)
        busy = parse_sse(send(client, other).text)
        worker.join(5)
        assert first_sse(busy) == [("error", {"code": "server_busy", "message": SERVER_BUSY})]
        assert parse_sse(first["res"].text)[-1][0] == "done"
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1  # only the first counts
