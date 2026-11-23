"""06 P0b (#33, design §4.9): the server-wide limit on running chat turns (`server_busy`), worker
threads that outlive a turn's time limit, app.db busy / operation timeouts, and the booking path
next to all of that. The airline and app.db are real; the LLM is the scripted fake."""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings as hsettings, strategies as st

from app.agent import Agent, ServerBusy, TurnConcurrency
from app.agent.concurrency import Threads, TurnSlots
from app.agent.loop import DRAIN_TIMEOUT, SERVER_BUSY
from app.airline import BookingError
from app.api import Settings
from app.main import create_app
from app.store import Store, StoreUnavailable

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, final_text, names
from .airline_helpers import SECRET, World
from .test_http_api import SECRET_KEY, parse_sse
from .test_limits import as_user, send


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def conversations(world, n):
    return [world.store.create_conversation(world.alice)["id"] for _ in range(n)]


def stored_texts(world, conv):
    return [m["text"] for m in world.store.list_messages(world.alice, conv)]


# ============================================================ configuration ==================
def test_concurrency_defaults_and_env():
    assert TurnConcurrency() == TurnConcurrency(8, 5.0)
    assert TurnConcurrency.from_env({}) == TurnConcurrency()
    assert TurnConcurrency.from_env({"AGENT_MAX_ACTIVE_TURNS": "3", "AGENT_TURN_QUEUE_SECONDS": "0"}) \
        == TurnConcurrency(3, 0.0)
    assert Settings.from_env({"AGENT_MAX_ACTIVE_TURNS": "2"}).turn_concurrency.max_active_turns == 2


@pytest.mark.parametrize("env", [
    {"AGENT_MAX_ACTIVE_TURNS": "0"}, {"AGENT_MAX_ACTIVE_TURNS": "257"}, {"AGENT_MAX_ACTIVE_TURNS": "2.5"},
    {"AGENT_MAX_ACTIVE_TURNS": "many"}, {"AGENT_TURN_QUEUE_SECONDS": "-1"}, {"AGENT_TURN_QUEUE_SECONDS": "61"},
    {"AGENT_TURN_QUEUE_SECONDS": "nan"}, {"AGENT_TURN_QUEUE_SECONDS": "soon"},
])
def test_bad_concurrency_env_is_refused(env):
    with pytest.raises(ValueError):
        TurnConcurrency.from_env(env)
    with pytest.raises(ValueError):
        Settings.from_env(env)


@pytest.mark.parametrize("bad", [dict(max_active_turns=0), dict(max_active_turns=True),
                                 dict(queue_seconds=-0.1), dict(queue_seconds=float("inf"))])
def test_bad_concurrency_values_are_refused(bad):
    with pytest.raises(ValueError):
        TurnConcurrency(**bad)


# ============================================================ TurnSlots ======================
def test_slots_queue_in_order_and_time_out():
    async def go():
        slots = TurnSlots(TurnConcurrency(1, 0.5))
        order = []
        gate = asyncio.Event()

        async def turn(name, hold_until=None):
            async with slots.hold():
                order.append(name)
                if hold_until is not None:
                    await hold_until.wait()

        first = asyncio.create_task(turn("a", gate))
        await asyncio.sleep(0)
        waiters = [asyncio.create_task(turn(n)) for n in "bcd"]
        await asyncio.sleep(0.01)
        assert (slots.active, slots.waiting, order) == (1, 3, ["a"])
        gate.set()
        await asyncio.gather(first, *waiters)
        assert order == ["a", "b", "c", "d"] and (slots.active, slots.waiting) == (0, 0)

        # nobody leaves: the next one gives up after queue_seconds
        gate2 = asyncio.Event()
        holder = asyncio.create_task(turn("x", gate2))
        await asyncio.sleep(0)
        started = time.monotonic()
        with pytest.raises(ServerBusy):
            await turn("y")
        assert 0.45 <= time.monotonic() - started < 2
        assert (slots.active, slots.waiting) == (1, 0)
        gate2.set()
        await holder
        assert slots.active == 0

        # queue_seconds = 0: refuse at once
        zero = TurnSlots(TurnConcurrency(1, 0))
        async with zero.hold():
            with pytest.raises(ServerBusy):
                async with zero.hold():
                    pass
        assert zero.active == 0

    asyncio.run(go())


def test_a_cancelled_waiter_leaves_no_slot_behind():
    async def go():
        slots = TurnSlots(TurnConcurrency(1, 10))
        gate = asyncio.Event()

        async def hold():
            async with slots.hold():
                await gate.wait()

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0)
        waiter = asyncio.create_task(hold())
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (slots.active, slots.waiting) == (1, 0)
        gate.set()
        await holder
        assert slots.active == 0
        async with slots.hold():  # still usable
            assert slots.active == 1

    asyncio.run(go())


@hsettings(deadline=None, max_examples=40)
@given(limit=st.integers(1, 4), turns=st.lists(st.tuples(st.integers(0, 3), st.booleans()), min_size=1, max_size=14),
       seed=st.integers(0, 2**16))
def test_slots_model(limit, turns, seed):
    """Model check: random arrival / hold / cancel; never more than `limit` inside, every turn
    either runs or is refused / cancelled, and the count returns to zero."""
    rnd = random.Random(seed)

    async def go():
        slots = TurnSlots(TurnConcurrency(limit, 0.02))
        inside = peak = ran = refused = 0

        async def one(ticks):
            nonlocal inside, peak, ran, refused
            try:
                async with slots.hold():
                    inside += 1
                    try:
                        peak = max(peak, inside)
                        assert slots.active <= limit
                        for _ in range(ticks):
                            await asyncio.sleep(0.005)
                        ran += 1
                    finally:
                        inside -= 1
            except ServerBusy:
                refused += 1

        tasks = []
        for ticks, cancel in turns:
            tasks.append((asyncio.create_task(one(ticks)), cancel))
            await asyncio.sleep(rnd.choice([0, 0, 0.003]))
        for task, cancel in tasks:
            if cancel:
                task.cancel()
        results = await asyncio.gather(*(t for t, _ in tasks), return_exceptions=True)
        cancelled = sum(isinstance(r, asyncio.CancelledError) for r in results)
        assert peak <= limit
        assert ran + refused + cancelled == len(turns)
        assert (slots.active, slots.waiting) == (0, 0)

    asyncio.run(go())


# ============================================================ N + 1 turns ====================
def test_n_plus_one_turns_the_last_gets_server_busy_and_nothing_is_stored(world):
    n = 3
    convs = conversations(world, n + 1)
    refunds = []

    async def go():
        gate = asyncio.Event()
        agent = Agent(world.airline, FakeLLM(default=[Wait(gate), "Hello."]),
                      concurrency=TurnConcurrency(n, 0.1, max_turns_per_user=n + 1))
        streams = [agent.stream_turn(world.alice, c, "hi") for c in convs[:n]]
        firsts = [await s.__anext__() for s in streams]  # each holds its slot now
        assert [e.name for e in firsts] == ["user_message"] * n and agent.slots.active == n
        late = [e async for e in agent.stream_turn(world.alice, convs[n], "hi",
                                                  on_server_busy=lambda: refunds.append(1))]
        gate.set()
        rest = [[e async for e in s] for s in streams]
        await agent.wait_idle()
        return late, rest, agent

    late, rest, agent = asyncio.run(go())
    assert names(late) == ["error"] and late[0].data == {"code": "server_busy", "message": SERVER_BUSY}
    assert refunds == [1]
    assert stored_texts(world, convs[n]) == []  # nothing stored, no title either
    assert world.store.get_conversation(world.alice, convs[n])["title"] == ""
    for c, events in zip(convs, rest):
        assert final_text(events) == "Hello." and stored_texts(world, c) == ["hi", "Hello."]
    assert agent.slots.active == 0


def test_a_queued_turn_runs_when_a_slot_frees_up(world):
    convs = conversations(world, 3)

    async def go():
        gates = [asyncio.Event(), asyncio.Event()]
        steps = [[Wait(gates[0]), "First."], [Wait(gates[1]), "Second."]]
        agent = Agent(world.airline, FakeLLM(*steps, default=["Third."]), concurrency=TurnConcurrency(2, 5, max_turns_per_user=3))
        a = agent.stream_turn(world.alice, convs[0], "one")
        await a.__anext__()
        b = agent.stream_turn(world.alice, convs[1], "two")
        await b.__anext__()
        c = agent.stream_turn(world.alice, convs[2], "three")
        await asyncio.sleep(0.05)
        assert agent.slots.waiting == 1 and stored_texts(world, convs[2]) == []  # queued: not stored yet
        gates[0].set()
        c_events = [e async for e in c]
        gates[1].set()
        await agent.wait_idle()
        return c_events

    events = asyncio.run(go())
    assert_stream(events)
    assert final_text(events) == "Third." and stored_texts(world, convs[2]) == ["three", "Third."]


def test_a_second_message_of_a_busy_conversation_takes_no_slot(world):
    """Order: conversation lock first, then the slot."""
    other = conversations(world, 1)[0]

    async def go():
        gate = asyncio.Event()
        agent = Agent(world.airline, FakeLLM([Wait(gate), "One."], default=["Two."]),
                      concurrency=TurnConcurrency(2, 0.1), lock_timeout=5)
        a = agent.stream_turn(world.alice, world.conv, "one")
        await a.__anext__()
        same = agent.stream_turn(world.alice, world.conv, "again")  # waits for the lock
        await asyncio.sleep(0.05)
        assert agent.slots.active == 1
        # the other conversation still gets the second slot
        b = [e async for e in agent.stream_turn(world.alice, other, "elsewhere")]
        gate.set()
        rest = [e async for e in same]
        await agent.wait_idle()
        return b, rest

    b, rest = asyncio.run(go())
    assert final_text(b) == "Two." and final_text(rest) == "Two."


def test_the_event_loop_stays_responsive_while_turns_wait_and_threads_block(world):
    block = threading.Event()
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (block.wait(5), find_city(name))[1]
    convs = conversations(world, 3)

    async def go():
        agent = Agent(world.airline, FakeLLM([Call("find_city", name="A")], default=["Done."]),
                      concurrency=TurnConcurrency(1, 0.3))
        a = agent.stream_turn(world.alice, convs[0], "where is A")
        b = agent.stream_turn(world.alice, convs[1], "hi")  # will wait for the slot, then give up
        lags = []
        for _ in range(20):  # 0.4 s of ticks while a thread blocks and a turn queues
            t = time.monotonic()
            await asyncio.sleep(0.02)
            lags.append(time.monotonic() - t - 0.02)
        b_events = [e async for e in b]
        block.set()
        a_events = [e async for e in a]
        await agent.wait_idle()
        return lags, a_events, b_events

    lags, a_events, b_events = asyncio.run(go())
    assert max(lags) < 0.1, lags
    assert names(b_events) == ["error"] and b_events[0].data["code"] == "server_busy"
    assert names(a_events)[-1] == "done"


# ============================================================ threads outliving the limit ===
def test_a_turn_cut_short_waits_for_its_tool_thread_before_storing_and_unlocking(world):
    """The turn's time limit passes while a tool thread runs. The thread is not killed; the turn
    keeps the conversation lock and its slot until the thread ended, then stores the template
    reply. A booking click meanwhile gets conversation_busy."""
    quote_id, nonce = world.presented()
    block, entered = threading.Event(), threading.Event()
    find_city = world.airline.find_city

    def slow_find_city(name):
        entered.set()
        block.wait(5)
        return find_city(name)

    world.airline.find_city = slow_find_city

    async def go():
        llm = FakeLLM([Call("quote_route", origin="A", destination="D")], [Call("find_city", name="A")],
                      default=["Done."])
        agent = Agent(world.airline, llm, turn_timeout=0.2, concurrency=TurnConcurrency(1, 0))
        stream = agent.stream_turn(world.alice, world.conv, "where is A")
        await asyncio.to_thread(entered.wait, 5)
        await asyncio.sleep(0.4)  # well past the 0.2 s limit
        assert world.airline.locks.locked(world.conv) and agent.slots.active == 1
        assert stored_texts(world, world.conv)[-1] == "where is A"  # no reply stored yet
        with pytest.raises(BookingError) as busy:
            await world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=0.05)
        assert busy.value.code == "conversation_busy"
        block.set()
        events = [e async for e in stream]
        await agent.wait_idle()
        return events, agent

    events, agent = asyncio.run(go())
    assert names(events)[-2:] == ["replace", "done"]
    rows = world.store.list_messages(world.alice, world.conv)
    assert rows[-1]["guard_problems"] == [{"kind": "turn_timeout", "value": "0.2"}]
    assert events[-1].data["message"]["cards"][0]["state"]["bookable"] is True  # the quote of step 1
    calls = world.store.list_tool_calls(world.alice, rows[-2]["id"])
    assert [c["name"] for c in calls] == ["quote_route"]  # the cut-short find_city has no row
    assert not world.airline.locks.locked(world.conv) and agent.slots.active == 0
    # the booking path is unaffected: the same click books now
    assert world.airline.book(world.alice, quote_id, nonce)["ticket"]["quote_id"] == quote_id


def test_a_thread_that_outlives_the_drain_limit_is_logged_and_the_turn_ends(world, caplog):
    block = threading.Event()
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (block.wait(5), find_city(name))[1]

    async def go():
        agent = Agent(world.airline, FakeLLM([Call("find_city", name="A")], default=["Done."]),
                      turn_timeout=0.1, drain_timeout=0.2)
        events = [e async for e in agent.stream_turn(world.alice, world.conv, "where is A")]
        locked_after = world.airline.locks.locked(world.conv)
        block.set()
        await agent.wait_idle()
        return events, locked_after

    with caplog.at_level(logging.WARNING, logger="app.agent.loop"):
        events, locked_after = asyncio.run(go())
    assert names(events)[-1] in ("done", "error") and not locked_after
    assert "still running after 0.2s" in caplog.text


def test_threads_tracks_a_cancelled_call_until_it_ends():
    async def go():
        threads, release = Threads(), threading.Event()
        task = asyncio.create_task(threads.run(release.wait, 5))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert threads.running == 1
        assert await threads.drain(0.05) is False
        release.set()
        assert await threads.drain(2) is True and threads.running == 0

    asyncio.run(go())


def test_drain_default():
    assert DRAIN_TIMEOUT == 30.0


# ============================================================ app.db timeouts ===============
def test_a_write_that_waits_too_long_for_the_lock_is_store_unavailable(tmp_path):
    store = Store(tmp_path / "app.db", busy_timeout=0.1)
    user = store.upsert_user("dev", "u", "U")["id"]
    other = sqlite3.connect(store.path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        with pytest.raises(StoreUnavailable):
            store.create_conversation(user)
        assert time.monotonic() - started < 2
        assert isinstance(StoreUnavailable("x"), sqlite3.OperationalError)
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert store.list_conversations(user) == []  # nothing written
    store.create_conversation(user)  # works again


def test_a_long_operation_is_interrupted_and_rolled_back(tmp_path):
    store = Store(tmp_path / "app.db", operation_timeout=0.1)
    user = store.upsert_user("dev", "u", "U")["id"]
    endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    for ctx in (store.write, store.read):
        started = time.monotonic()
        with pytest.raises(StoreUnavailable, match="interrupted"):
            with ctx() as con:
                if ctx == store.write:
                    con.execute("UPDATE users SET display_name = 'changed' WHERE id = ?", (user,))
                con.execute(endless).fetchone()
        assert time.monotonic() - started < 3
    assert store.get_user(user)["display_name"] == "U"  # rolled back
    # a fresh operation has its own deadline
    with store.write() as con:
        con.execute("UPDATE users SET display_name = 'ok' WHERE id = ?", (user,))
    assert store.get_user(user)["display_name"] == "ok"


def test_store_timeout_defaults(tmp_path):
    store = Store(tmp_path / "app.db")
    assert (store.busy_timeout, store.operation_timeout) == (10.0, 10.0)


# ============================================================ HTTP ==========================
@pytest.fixture
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


def client_for(world, **settings):
    base = dict(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    base.update(settings)
    app = create_app(world.db, Settings(**base), clock=world.clock, llm=FakeLLM(default=["Hello."]))
    return TestClient(app), app


def test_http_server_busy_gives_the_message_slot_back(world, no_real_llm):
    c, app = client_for(world, turn_concurrency=TurnConcurrency(1, 0))
    with c as client:
        as_user(client, world.alice)
        app.state.agent.slots.active = 1  # another user's turn is running
        res = send(client, world.conv)
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(res.text)
        assert [e[0] for e in events] == ["error"]
        assert events[0][1] == {"code": "server_busy", "message": SERVER_BUSY}
        assert client.get("/api/me").json()["usage"]["messages_today"] == 0  # refunded
        assert stored_texts(world, world.conv) == []
        app.state.agent.slots.active = 0
        events = parse_sse(send(client, world.conv).text)
        assert events[-1][0] == "done"
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1


def test_http_booking_on_a_locked_database_is_503_and_can_be_retried(world, no_real_llm):
    quote_id, nonce = world.presented()
    c, app = client_for(world)
    with c as client:
        as_user(client, world.alice)
        app.state.store.busy_timeout = 0.1
        other = sqlite3.connect(world.store.path, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")  # another writer holds app.db
        try:
            res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})
        finally:
            other.execute("ROLLBACK")
            other.close()
        assert res.status_code == 503, res.text
        assert res.json()["error"]["code"] == "data_unavailable"
        assert world.status(quote_id) == "presented" and world.ticket_count() == 0
        res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})  # same click again
        assert res.status_code == 200, res.text
        assert res.json()["ticket"]["quote_id"] == quote_id and world.ticket_count() == 1
