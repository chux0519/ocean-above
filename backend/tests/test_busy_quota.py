"""#55 decision 2 (design §4.9, invariant U1): the daily message limit counts user messages that were
*stored*. Every refusal that stores nothing and starts no LLM or tool work gives back exactly its own
reservation, once: `conversation_busy` (the same conversation's lock timed out), the per-user turn
cap (also `conversation_busy`, USER_BUSY text), `server_busy`, Stop while queued. Checked over HTTP:
the counter in /api/me, the message_usage rows and the stored messages, not only the codes."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.agent import Agent, TurnConcurrency
from app.agent.loop import BUSY, SERVER_BUSY, USER_BUSY
from app.api import Settings
from app.main import create_app

from .agent_helpers import FakeLLM, Wait, first_data, names
from .airline_helpers import SECRET, World
from .test_http_api import SECRET_KEY, parse_sse
from .test_limits import as_user, send


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def client_for(world, llm, agent_kwargs=None, **settings):
    base = dict(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    base.update(settings)
    app = create_app(world.db, Settings(**base), clock=world.clock, llm=llm, agent_kwargs=agent_kwargs)
    return TestClient(app), app


def usage_rows(world, user):
    with world.store.read() as con:
        return [tuple(r) for r in con.execute(
            "SELECT id, conversation_id FROM message_usage WHERE user_id = ? ORDER BY seq", (user,))]


def user_texts(world, conv):
    return [m["text"] for m in world.store.list_messages(world.alice, conv) if m["role"] == "user"]


def first_sse(events):
    return [(name, first_data(data)) for name, data in events]


def busy_while_first_turn_runs(world, app, client, first_conv, second_conv, text="second"):
    """Send a turn to `first_conv` that waits in the LLM; while it runs, send `text` to
    `second_conv`. Returns (the second response's events, the first response)."""
    first = {}
    worker = threading.Thread(target=lambda: first.setdefault("res", send(client, first_conv, "first")))
    worker.start()
    for _ in range(300):  # until the first turn stored its message (it holds lock, user count, slot)
        if user_texts(world, first_conv) == ["first"]:
            break
        time.sleep(0.01)
    assert user_texts(world, first_conv) == ["first"]
    second = parse_sse(send(client, second_conv, text).text)
    worker.join(10)
    assert not worker.is_alive()
    return second, first["res"]


def test_normal_turn_counts_one_stored_message(world):
    c, _ = client_for(world, FakeLLM(default=["Hello."]))
    with c as client:
        as_user(client, world.alice)
        events = parse_sse(send(client, world.conv, "hi").text)
        assert [e[0] for e in events][-1] == "done"
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1
        assert user_texts(world, world.conv) == ["hi"]
        assert [conv for _, conv in usage_rows(world, world.alice)] == [world.conv]


def test_same_conversation_lock_timeout_gives_its_slot_back(world):
    """conversation_busy (BUSY): the second message of a working conversation waited for the lock
    and gave up. Old policy: it kept its slot; now it is refunded and only the first counts."""
    llm = FakeLLM([Wait(seconds=0.8), "First."], default=["Again."])
    c, app = client_for(world, llm, agent_kwargs={"lock_timeout": 0.1})
    with c as client:
        as_user(client, world.alice)
        busy, first = busy_while_first_turn_runs(world, app, client, world.conv, world.conv)
        assert first_sse(busy) == [("error", {"code": "conversation_busy", "message": BUSY})]
        assert parse_sse(first.text)[-1][0] == "done"
        assert user_texts(world, world.conv) == ["first"]
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1
        assert len(usage_rows(world, world.alice)) == 1


def test_per_user_turn_cap_gives_its_slot_back(world):
    """conversation_busy (USER_BUSY): the user already has max_turns_per_user turns running."""
    other = world.store.create_conversation(world.alice)["id"]
    llm = FakeLLM([Wait(seconds=0.8), "First."], default=["Again."])
    c, app = client_for(world, llm, turn_concurrency=TurnConcurrency(4, 0.05, max_turns_per_user=1))
    with c as client:
        as_user(client, world.alice)
        busy, first = busy_while_first_turn_runs(world, app, client, world.conv, other)
        assert first_sse(busy) == [("error", {"code": "conversation_busy", "message": USER_BUSY})]
        assert parse_sse(first.text)[-1][0] == "done"
        assert user_texts(world, other) == []
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1
        assert [conv for _, conv in usage_rows(world, world.alice)] == [world.conv]  # the first one's row stays


def test_server_busy_gives_its_slot_back(world):
    other = world.store.create_conversation(world.alice)["id"]
    llm = FakeLLM([Wait(seconds=0.8), "First."], default=["Again."])
    c, app = client_for(world, llm, turn_concurrency=TurnConcurrency(1, 0.05, max_turns_per_user=2))
    with c as client:
        as_user(client, world.alice)
        busy, first = busy_while_first_turn_runs(world, app, client, world.conv, other)
        assert first_sse(busy) == [("error", {"code": "server_busy", "message": SERVER_BUSY})]
        assert parse_sse(first.text)[-1][0] == "done"
        assert user_texts(world, other) == []
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1
        assert [conv for _, conv in usage_rows(world, world.alice)] == [world.conv]


def test_a_busy_refusal_then_a_retry_counts_once(world):
    """After the refusal, the retry is let through and counted: one stored message, one slot."""
    llm = FakeLLM([Wait(seconds=0.8), "First."], default=["Again."])
    c, app = client_for(world, llm, agent_kwargs={"lock_timeout": 0.1})
    with c as client:
        as_user(client, world.alice)
        busy_while_first_turn_runs(world, app, client, world.conv, world.conv)
        assert parse_sse(send(client, world.conv, "second").text)[-1][0] == "done"
        assert user_texts(world, world.conv) == ["first", "second"]
        assert client.get("/api/me").json()["usage"]["messages_today"] == 2


def test_a_busy_refusal_at_the_daily_limit_frees_the_slot_for_a_retry(world):
    """With a limit of 2: first turn + a refused second must not lock the user out of a retry."""
    llm = FakeLLM([Wait(seconds=0.8), "First."], default=["Again."])
    c, app = client_for(world, llm, agent_kwargs={"lock_timeout": 0.1}, daily_messages_per_user=2)
    with c as client:
        as_user(client, world.alice)
        busy_while_first_turn_runs(world, app, client, world.conv, world.conv)
        res = send(client, world.conv, "second")
        assert res.status_code == 200 and parse_sse(res.text)[-1][0] == "done"
        assert send(client, world.conv, "third").status_code == 429


# --- exactly that one reservation, exactly once --------------------------------------------
@pytest.mark.parametrize("path", ["lock", "user", "slots"])
def test_each_busy_refusal_runs_the_refund_exactly_once(world, path):
    refunds = []

    async def go():
        gate = asyncio.Event()
        agent = Agent(world.airline, FakeLLM([Wait(gate), "First."], default=["unused"]),
                      concurrency=TurnConcurrency(1 if path == "slots" else 4, 0.05,
                                                  max_turns_per_user=1 if path == "user" else 4),
                      lock_timeout=0.05)
        other = world.store.create_conversation(world.alice)["id"]
        running = agent.stream_turn(world.alice, world.conv, "first",
                                    on_not_stored=lambda: refunds.append("first"))
        seen_first = asyncio.ensure_future(_all(running))
        while not agent._running:
            await asyncio.sleep(0.01)
        target = world.conv if path == "lock" else other
        refused = [e async for e in agent.stream_turn(world.alice, target, "second",
                                                      on_not_stored=lambda: refunds.append("second"))]
        gate.set()
        await seen_first
        await agent.wait_idle()
        return refused

    refused = asyncio.run(go())
    code = "server_busy" if path == "slots" else "conversation_busy"
    assert names(refused) == ["error"] and first_data(refused[0].data)["code"] == code
    assert refunds == ["second"]


def test_a_stop_during_a_busy_refund_is_too_late_and_refunds_once(world):
    """The refusal is giving the slot back (in a worker thread) when Stop arrives: Stop answers
    `finishing`, the refund runs once and the stream ends with the one busy error (R4, R8)."""
    gate, entered = threading.Event(), threading.Event()
    refunds = []

    def slow_refund():
        entered.set()
        assert gate.wait(10)
        refunds.append(1)

    async def go():
        hold = asyncio.Event()
        agent = Agent(world.airline, FakeLLM([Wait(hold), "First."], default=["unused"]), lock_timeout=0.05)
        running = agent.stream_turn(world.alice, world.conv, "first")
        seen_first = asyncio.ensure_future(_all(running))
        while not agent._running:
            await asyncio.sleep(0.01)
        events = agent.stream_turn(world.alice, world.conv, "second", on_not_stored=slow_refund)
        seen = asyncio.ensure_future(_all(events))
        await asyncio.to_thread(entered.wait, 5)
        answer = agent.cancel_run(world.alice, world.conv, events.run_id)
        again = agent.cancel_run(world.alice, world.conv, events.run_id)
        gate.set()
        out = await seen
        hold.set()
        await seen_first
        await agent.wait_idle()
        return answer, again, out, agent.run_state(events.run_id)

    answer, again, seen, state = asyncio.run(go())
    assert answer == "finishing" and again == "finishing"
    assert names(seen) == ["error"] and first_data(seen[0].data)["code"] == "conversation_busy"
    assert state == "failed" and refunds == [1]


async def _all(events):
    return [e async for e in events]
