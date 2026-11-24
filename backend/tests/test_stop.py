"""Stop (#48): the user cancels a running turn. Invariants R1-R8 in docs/design/state_invariants.md.

One named test per race boundary of the issue (before the user message is stored, queued for a
slot, during model streaming, between model and tool, during sync tool / DB work, while
persisting, after completion, repeated Stop, two tabs, disconnect without Stop, cross-user, wrong
run_id), a seeded model-based test that stops at random points of a turn and checks every
invariant (Stop/finish race: exactly one terminal outcome), and the booking side (R6).
"""
from __future__ import annotations

import asyncio
import random
import threading

import pytest

from app.agent import Agent, LLMError, TurnConcurrency
from app.airline import BookingError

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, first_data, names
from .airline_helpers import World


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


@pytest.fixture
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


def stored(world, user=None, conv=None):
    return world.store.list_messages(user or world.alice, conv or world.conv)


async def until(events, name, seen):
    async for e in events:
        seen.append(e)
        if e.name == name:
            return e
    raise AssertionError(f"no {name} event in {names(seen)}")


async def rest(events, seen):
    seen += [e async for e in events]
    return seen


def released(agent: Agent, world: World, conv=None) -> None:
    """R3 / C2 after the run ended: lock, slot and the per-user count are free, the run retired."""
    assert not world.airline.locks.locked(conv or world.conv)
    assert agent.slots.active == 0 and agent.slots.waiting == 0
    assert agent._user_turns == {} and agent._runs == {} and agent._running == {}


def blocking(fn, when=lambda *a, **k: True):
    """`fn` that waits on `gate` (a threading.Event) when `when(...)`; `entered` is set first."""
    gate, entered = threading.Event(), threading.Event()

    def wrapped(*args, **kwargs):
        if when(*args, **kwargs):
            entered.set()
            assert gate.wait(10), "test gate never opened"
        return fn(*args, **kwargs)
    return wrapped, gate, entered


# ============================================================ before anything is stored ======
def test_stop_before_the_run_task_begins_stores_nothing_and_refunds(world):
    llm = FakeLLM(default=["unused"])
    refunds = []

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi", on_not_stored=lambda: refunds.append(1))
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        seen = [e async for e in events]
        await agent.wait_idle()
        released(agent, world)
        return agent, events.run_id, seen

    agent, run_id, seen = asyncio.run(go())
    assert names(seen) == ["cancelled"] and seen[0].data == {"run_id": run_id, "message": None}
    assert refunds == [1] and stored(world) == [] and llm.bodies == []
    assert agent.run_state(run_id) == "cancelled"


def test_stop_while_waiting_for_the_conversation_lock_stores_nothing_and_refunds(world):
    gate = asyncio.Event()
    llm = FakeLLM(["First. ", Wait(gate), "Done."], default=["unused"])
    refunds = []

    async def go():
        agent = Agent(world.airline, llm)
        first = agent.stream_turn(world.alice, world.conv, "one")
        a = []
        await until(first, "delta", a)
        second = agent.stream_turn(world.alice, world.conv, "two", on_not_stored=lambda: refunds.append(1))
        await asyncio.sleep(0.05)  # second waits for the lock
        assert agent.run_state(second.run_id) == "queued"
        assert agent.cancel_run(world.alice, world.conv, second.run_id) == "cancelling"
        b = [e async for e in second]
        assert world.airline.locks.locked(world.conv)  # still the first turn's
        gate.set()
        await rest(first, a)
        await agent.wait_idle()
        released(agent, world)
        return a, b

    a, b = asyncio.run(go())
    assert names(b) == ["cancelled"] and first_data(b[0].data) == {"message": None}
    assert refunds == [1]
    assert names(a)[-1] == "done"  # R1: the other run is untouched
    assert [m["text"] for m in stored(world)] == ["one", "First. Done."]


def test_stop_while_queued_for_a_slot_stores_nothing_and_frees_the_queue(world):
    gate = asyncio.Event()
    llm = FakeLLM(["Busy. ", Wait(gate), "Done."], ["Second."], default=["unused"])
    refunds = []

    async def go():
        agent = Agent(world.airline, llm, concurrency=TurnConcurrency(1, 30))
        first = agent.stream_turn(world.alice, world.conv, "one")
        a = []
        await until(first, "delta", a)
        queued = agent.stream_turn(world.bob, world.bob_conv, "two", on_not_stored=lambda: refunds.append(1))
        await asyncio.sleep(0.05)
        assert agent.slots.waiting == 1 and agent.run_state(queued.run_id) == "queued"
        assert agent.cancel_run(world.bob, world.bob_conv, queued.run_id) == "cancelling"
        b = [e async for e in queued]
        assert agent.slots.waiting == 0 and agent.slots.active == 1
        gate.set()
        await rest(first, a)
        await agent.wait_idle()
        released(agent, world)
        released(agent, world, world.bob_conv)
        return a, b

    a, b = asyncio.run(go())
    assert names(b) == ["cancelled"] and refunds == [1]
    assert stored(world, world.bob, world.bob_conv) == []
    assert names(a)[-1] == "done"


def test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step(world):
    original = world.store.add_message
    world.store.add_message, gate, entered = blocking(original, lambda u, c, role, *a, **k: role == "user")
    llm = FakeLLM(default=["unused"])
    refunds = []

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi", on_not_stored=lambda: refunds.append(1))
        await asyncio.to_thread(entered.wait, 5)
        assert agent.run_state(events.run_id) == "running"
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        gate.set()
        seen = [e async for e in events]
        await agent.wait_idle()
        released(agent, world)
        return seen

    seen = asyncio.run(go())
    assert names(seen) == ["user_message", "cancelled"]
    message = assert_stream(seen)
    assert (message["text"], message["cards"], message["status"]) == ("", [], "cancelled")
    assert llm.bodies == [] and refunds == []  # the message was stored: its slot stays used (U1)
    assert [(m["role"], m["status"]) for m in stored(world)] == [("user", "completed"), ("assistant", "cancelled")]


# ============================================================ model stream ===================
def test_stop_during_an_endless_model_stream_closes_it_and_keeps_the_released_text(world):
    gate = asyncio.Event()  # never set: the model streams "forever"
    llm = FakeLLM(["Let me check that. ", "Still thinking", Wait(gate)], ["Next turn works."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = []
        await until(events, "delta", seen)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        await rest(events, seen)
        assert llm.streams == ["closed early"] and len(llm.bodies) == 1  # closed, no new request
        await agent.wait_idle()
        released(agent, world)
        second = [e async for e in agent.stream_turn(world.alice, world.conv, "again")]
        await agent.wait_idle()
        return seen, second

    seen, second = asyncio.run(go())
    message = assert_stream(seen)
    # only the sentence the guard released; the unfinished one never reached the client
    assert (message["text"], message["status"]) == ("Let me check that.", "cancelled")
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert logged[0]["response"]["aborted"] == "cancelled"
    assert assert_stream(second)["text"] == "Next turn works."
    # the next request saw the stopped reply and the note, nothing of the aborted call
    request = llm.requests[-1]
    assert {"role": "assistant", "content": "Let me check that."} in request
    assert any(m["role"] == "system" and "stopped" in m["content"] for m in request)


def test_stop_during_the_retry_backoff_makes_no_further_request(world):
    entered = asyncio.Event()

    async def slow_sleep(seconds):
        entered.set()
        await asyncio.sleep(3600)

    llm = FakeLLM(LLMError("boom", status=503, retryable=True), ["unused"])

    async def go():
        agent = Agent(world.airline, llm, sleep=slow_sleep)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = []
        await until(events, "user_message", seen)
        await entered.wait()
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        await rest(events, seen)
        await agent.wait_idle()
        released(agent, world)
        return seen

    seen = asyncio.run(go())
    assert names(seen)[-1] == "cancelled" and len(llm.bodies) == 1  # no retry after Stop (R2)


# ============================================================ tools ==========================
def test_stop_between_model_and_tool_runs_no_tool_and_keeps_the_history_well_formed(world):
    ran = []
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (ran.append(name), find_city(name))[1]
    original = world.store.add_llm_call
    world.store.add_llm_call, gate, entered = blocking(
        original, lambda u, c, request, response, **k: bool(response.get("tool_calls")))
    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B")], ["Fine."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A and B?")
        seen = asyncio.ensure_future(rest(events, []))
        await asyncio.to_thread(entered.wait, 5)  # the completion is being logged
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        await asyncio.sleep(0.1)
        assert not seen.done() and world.airline.locks.locked(world.conv)  # waits for the write (R3)
        gate.set()
        out = await seen
        await agent.wait_idle()
        released(agent, world)
        world.store.add_llm_call = original
        second = [e async for e in agent.stream_turn(world.alice, world.conv, "again")]
        await agent.wait_idle()
        return out, second

    seen, second = asyncio.run(go())
    assert names(seen)[-1] == "cancelled" and "status" not in names(seen) and ran == []
    rows = world.store.list_tool_calls(world.alice, seen[0].data["id"])
    assert [r["error"] for r in rows] == ["cancelled: not run"] * 2  # R7
    assert names(second)[-1] == "done"
    # the next context: the logged completion with its two calls, each answered `cancelled`
    request = llm.requests[-1]
    calls = [c for m in request if m["role"] == "assistant" for c in m.get("tool_calls") or []]
    answers = {m["tool_call_id"]: m["content"] for m in request if m["role"] == "tool"}
    assert len(calls) == 2 and all('"code":"cancelled"' in answers[c["id"]] for c in calls)


def test_stop_during_sync_tool_work_waits_for_the_thread_before_releasing(world):
    find_city = world.airline.find_city
    world.airline.find_city, gate, entered = blocking(find_city)
    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B")], default=["unused"])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A and B?")
        seen = asyncio.ensure_future(rest(events, []))
        await asyncio.to_thread(entered.wait, 5)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        await asyncio.sleep(0.2)
        # R3: the thread still runs: nothing stored yet, lock, slot and per-user count held
        assert not seen.done() and world.airline.locks.locked(world.conv)
        assert agent.slots.active == 1 and agent._user_turns == {world.alice: 1}
        assert agent.run_state(events.run_id) == "cancelling"
        gate.set()
        out = await seen
        await agent.wait_idle()
        released(agent, world)
        return out

    seen = asyncio.run(go())
    assert names(seen)[-1] == "cancelled" and "card" not in names(seen)
    assert len(llm.bodies) == 1
    rows = world.store.list_tool_calls(world.alice, seen[0].data["id"])
    assert [r["error"] for r in rows] == ["cancelled: interrupted", "cancelled: not run"]


def test_a_stop_after_a_finished_tool_keeps_its_card_but_never_makes_it_bookable(world):
    gate = asyncio.Event()
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["The route is ready. ", Wait(gate)],
                  [Call("quote_route", origin="A", destination="D")], ["Here it is again."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A to D")
        seen = []
        await until(events, "delta", seen)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
        await rest(events, seen)
        await agent.wait_idle()
        again = [e async for e in agent.stream_turn(world.alice, world.conv, "A to D again")]
        await agent.wait_idle()
        return seen, again

    seen, again = asyncio.run(go())
    message = assert_stream(seen)
    [card] = message["cards"]
    assert card["state"] == {"status": "quoted", "bookable": False, "nonce": None, "ticket_id": None}
    # on reload, too (R6)
    reloaded = world.airline.messages(world.alice, world.conv)[1]
    assert reloaded["status"] == "cancelled" and reloaded["cards"][0]["state"]["bookable"] is False
    for nonce in ("", "n_guess", "x" * 40):
        with pytest.raises(BookingError) as refused:
            world.airline.book(world.alice, card["id"], nonce)
        assert refused.value.code == "not_presented"
    assert world.ticket_count() == 0
    # a new quote in a later, completed turn is bookable as usual
    [new] = assert_stream(again)["cards"]
    assert new["id"] != card["id"] and new["state"]["bookable"] is True
    world.airline.book(world.alice, new["id"], new["state"]["nonce"])
    assert world.ticket_count() == 1
    assert world.airline.get_quote(world.alice, card["id"])["state"]["status"] == "quoted"


# ============================================================ late Stop ======================
def test_stop_while_the_reply_is_being_stored_changes_nothing(world):
    post = world.airline.post_assistant_message
    world.airline.post_assistant_message, gate, entered = blocking(post)
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["Done."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A to D")
        seen = asyncio.ensure_future(rest(events, []))
        await asyncio.to_thread(entered.wait, 5)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "finishing"
        gate.set()
        out = await seen
        await agent.wait_idle()
        released(agent, world)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "completed"
        return out, agent.run_state(events.run_id)

    seen, state = asyncio.run(go())
    message = assert_stream(seen)
    assert names(seen)[-1] == "done" and state == "completed"
    assert message["status"] == "completed" and message["cards"][0]["state"]["bookable"] is True


def test_stop_after_completion_reports_completed_and_changes_nothing(world):
    llm = FakeLLM(["Hello."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = [e async for e in events]
        await agent.wait_idle()
        before = stored(world)
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "completed"
        assert agent.cancel_run(world.alice, world.conv, events.run_id) == "completed"
        assert stored(world) == before
        return seen

    assert names(asyncio.run(go()))[-1] == "done"


def test_repeated_stop_is_idempotent_and_ends_once(world):
    gate = asyncio.Event()
    llm = FakeLLM(["One. ", Wait(gate)])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = []
        await until(events, "delta", seen)
        answers = [agent.cancel_run(world.alice, world.conv, events.run_id) for _ in range(3)]
        await rest(events, seen)
        await agent.wait_idle()
        answers.append(agent.cancel_run(world.alice, world.conv, events.run_id))
        return answers, seen

    answers, seen = asyncio.run(go())
    assert answers == ["cancelling", "cancelling", "cancelling", "cancelled"]
    assert names(seen).count("cancelled") == 1 and len(stored(world)) == 2


# ============================================================ ownership ======================
def test_another_user_another_conversation_or_an_unknown_run_cannot_stop(world):
    gate = asyncio.Event()
    llm = FakeLLM(["One. ", Wait(gate), "Two."])
    other = world.store.create_conversation(world.alice)["id"]

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = []
        await until(events, "delta", seen)
        assert agent.cancel_run(world.bob, world.conv, events.run_id) is None
        assert agent.cancel_run(world.bob, world.bob_conv, events.run_id) is None
        assert agent.cancel_run(world.alice, other, events.run_id) is None
        assert agent.cancel_run(world.alice, world.conv, "r_0000000000000000") is None
        assert agent.run_state(events.run_id) == "running"
        gate.set()
        await rest(events, seen)
        await agent.wait_idle()
        return seen

    assert names(asyncio.run(go()))[-1] == "done"


def test_two_tabs_an_old_run_id_never_stops_the_newer_run(world):
    gate = asyncio.Event()
    llm = FakeLLM(["Old. ", Wait(gate)], ["New. ", Wait(gate), "Done."])

    async def go():
        agent = Agent(world.airline, llm)
        old = agent.stream_turn(world.alice, world.conv, "one")  # tab 1
        a = []
        await until(old, "delta", a)
        assert agent.cancel_run(world.alice, world.conv, old.run_id) == "cancelling"
        await rest(old, a)
        await agent.wait_idle()
        new = agent.stream_turn(world.alice, world.conv, "two")  # tab 2
        b = []
        await until(new, "delta", b)
        # tab 1 repeats its Stop with its own (old) run id
        assert agent.cancel_run(world.alice, world.conv, old.run_id) == "cancelled"
        assert agent.run_state(new.run_id) == "running"
        gate.set()
        await rest(new, b)
        await agent.wait_idle()
        return a, b

    a, b = asyncio.run(go())
    assert names(a)[-1] == "cancelled" and names(b)[-1] == "done"
    assert [m["status"] for m in stored(world)] == ["completed", "cancelled", "completed", "completed"]


def test_a_disconnect_without_stop_lets_the_run_finish_and_store(world):
    gate = asyncio.Event()
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["One. ", Wait(gate), "Two."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A to D")
        await until(events, "delta", [])
        await events.aclose()  # the client went away (closed tab, network loss)
        gate.set()
        await agent.wait_idle()
        released(agent, world)
        return agent.run_state(events.run_id)

    assert asyncio.run(go()) == "completed"
    reloaded = world.airline.messages(world.alice, world.conv)
    assert reloaded[-1]["status"] == "completed" and reloaded[-1]["text"].startswith("One. Two.")
    assert reloaded[-1]["cards"][0]["state"]["bookable"] is True


# ============================================================ model-based ====================
STOP_POINTS = ("before_start", "after_event", "after_sleep")
SEEDS = range(40)


def _script(gate: asyncio.Event):
    return FakeLLM(
        ["Looking. ", Call("quote_route", origin="A", destination="D"), Call("find_city", name="B")],
        ["The route is ready. ", Wait(seconds=0.01), "Shall I book it? ", Call("request_booking", quote_id="q_x")],
        ["Done."], default=["Fallback."])


def _stop_run(world: World, seed: int):
    """Stop one turn at a random point; returns what the invariants need."""
    rng = random.Random(seed)
    point = rng.choice(STOP_POINTS)
    k = rng.randrange(12)
    llm = _script(asyncio.Event())
    out: dict = {"point": point, "k": k}

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A to D")
        seen = []
        answer = None
        if point == "before_start":
            answer = agent.cancel_run(world.alice, world.conv, events.run_id)
            out["bodies_at_stop"] = len(llm.bodies)
        async for e in events:
            seen.append(e)
            if answer is None and point == "after_event" and len(seen) > k:
                answer = agent.cancel_run(world.alice, world.conv, events.run_id)
                out["bodies_at_stop"] = len(llm.bodies)
            if answer is None and point == "after_sleep" and len(seen) == 1:
                for _ in range(k * 3):
                    await asyncio.sleep(0)
                answer = agent.cancel_run(world.alice, world.conv, events.run_id)
                out["bodies_at_stop"] = len(llm.bodies)
        if answer is None:  # the turn ended before the chosen point
            answer = agent.cancel_run(world.alice, world.conv, events.run_id)
            out["bodies_at_stop"] = len(llm.bodies)
        await agent.wait_idle()
        released(agent, world)
        out.update(answer=answer, events=seen, state=agent.run_state(events.run_id), bodies=len(llm.bodies))

    asyncio.run(go())
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_stop_at_a_random_point_keeps_every_invariant(tmp_path, seed):
    world = World(tmp_path / "w")
    out = _stop_run(world, seed)
    seen, answer = out["events"], out["answer"]
    kinds = names(seen)
    # R4: exactly one terminal outcome, and the registry agrees with it
    assert sum(k in ("done", "cancelled", "error") for k in kinds) == 1 and kinds[-1] in ("done", "cancelled")
    assert out["state"] == {"done": "completed", "cancelled": "cancelled"}[kinds[-1]]
    # an accepted Stop ends `cancelled`; a late one changes nothing
    assert (answer == "cancelling") == (kinds[-1] == "cancelled"), (answer, kinds)
    assert answer in ("cancelling", "finishing", "completed")
    message = assert_stream(seen)
    if kinds[-1] == "cancelled":
        assert out["bodies"] == out["bodies_at_stop"]  # R2: no LLM request after the Stop
        messages = world.airline.messages(world.alice, world.conv)
        if message is None:  # R5: nothing stored
            assert messages == []
        else:
            assert messages[-1]["status"] == "cancelled" and messages[-1]["text"] == message["text"]
            # R6: no quote of a stopped turn is presented or bookable
            assert {r[0] for r in world.sql("SELECT status FROM quotes")} <= {"quoted"}
            assert world.sql("SELECT COUNT(*) FROM quote_cards")[0][0] == 0
    # R7: every tool call of every logged completion has exactly one tool_calls row
    if seen and seen[0].name == "user_message":
        rows = world.store.list_tool_calls(world.alice, seen[0].data["id"])
        logged = [c["response"] for c in world.store.list_llm_calls(world.alice, world.conv)]
        calls = sum(len(r.get("tool_calls") or []) for r in logged if "finish_reason" in r)
        assert len(rows) == calls, (len(rows), calls, out["point"], out["k"])
    assert world.ticket_count() == 0  # Stop never books


def test_the_seeds_reach_every_stop_boundary(tmp_path_factory):
    """The fixed seeds are only worth something if they stop at the interesting places."""
    outcomes = set()
    for seed in SEEDS:
        out = _stop_run(World(tmp_path_factory.mktemp("cover")), seed)
        kinds = names(out["events"])
        stored_user = kinds[0] == "user_message"
        tool_before_stop = "card" in kinds
        outcomes.add((out["answer"], stored_user, tool_before_stop))
    assert ("cancelling", False, False) in outcomes  # before the user message was stored
    assert ("cancelling", True, False) in outcomes  # before any tool finished
    assert ("cancelling", True, True) in outcomes  # after a tool finished
    assert any(a in ("finishing", "completed") for a, _, _ in outcomes)  # too late


# ============================================================ HTTP ===========================
def _http(world, llm):
    from fastapi.testclient import TestClient

    from app.api import Settings
    from app.main import create_app

    from .airline_helpers import SECRET
    from .test_http_api import SECRET_KEY

    settings = Settings(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                        session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    app = create_app(world.db, settings, clock=world.clock, llm=llm)
    return TestClient(app), app


def _cancel(client, conv, run_id, body=None):
    return client.post(f"/api/conversations/{conv}/runs/{run_id}/cancel", json={} if body is None else body)


def test_http_stop_ends_the_stream_cancelled_and_the_reload_shows_it(world, no_real_llm):
    from .test_http_api import parse_sse
    from .test_limits import as_user

    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B")], default=["unused"])
    c, app = _http(world, llm)
    with c as client:
        as_user(client, world.alice)
        store = app.state.store  # block while the completion with the tool calls is logged
        store.add_llm_call, gate, entered = blocking(
            store.add_llm_call, lambda u, c, request, response, **k: bool(response.get("tool_calls")))
        stop: dict = {}

        def stopper():
            entered.wait(5)
            stop["res"] = _cancel(client, world.conv, stop["run_id"])
            gate.set()

        with client.stream("POST", f"/api/conversations/{world.conv}/messages", json={"text": "A and B?"}) as res:
            stop["run_id"] = run_id = res.headers["x-run-id"]
            worker = threading.Thread(target=stopper)
            worker.start()
            body = "".join(res.iter_text())
        worker.join(5)
        assert stop["res"].status_code == 200 and stop["res"].json() == {"run_id": run_id, "status": "cancelling"}
        events = parse_sse(body)
        assert events[0][0] == "user_message" and events[0][1]["run_id"] == run_id
        assert events[-1][0] == "cancelled" and events[-1][1]["run_id"] == run_id
        assert [e[0] for e in events].count("cancelled") == 1
        conversation = client.get(f"/api/conversations/{world.conv}").json()
        assert [m["status"] for m in conversation["messages"]] == ["completed", "cancelled"]
        # repeated Stop, after the end: the final state, nothing changes (R8)
        again = _cancel(client, world.conv, run_id)
        assert again.status_code == 200 and again.json()["status"] == "cancelled"
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1  # the message was stored
        # the next message works at once
        assert parse_sse(client.post(f"/api/conversations/{world.conv}/messages",
                                     json={"text": "hi"}).text)[-1][0] == "done"


def test_http_stop_is_owner_only_and_404_for_unknown_runs(world, no_real_llm):
    from .test_http_api import parse_sse
    from .test_limits import as_user

    c, app = _http(world, FakeLLM(default=["Hello."]))
    with c as client:
        as_user(client, world.alice)
        res = client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"})
        run_id = res.headers["x-run-id"]
        assert run_id.startswith("r_") and parse_sse(res.text)[0][1]["run_id"] == run_id
        assert _cancel(client, world.conv, run_id).json() == {"run_id": run_id, "status": "completed"}
        other = client.post("/api/conversations", json={}).json()["id"]
        for conv, rid in ((other, run_id), (world.conv, "r_0000000000000000"), (world.conv, "x" * 500),
                          ("c_nope", run_id)):
            refused = _cancel(client, conv, rid)
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "not_found"
        assert _cancel(client, world.conv, run_id, body=[1]).status_code == 400
        as_user(client, world.bob)
        for conv in (world.conv, world.bob_conv):
            refused = _cancel(client, conv, run_id)
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "not_found"
        client.cookies.clear()
        assert _cancel(client, world.conv, run_id).status_code == 401


# ============================================================ review P2 (review-stop.md) =======
def test_p2_1_a_stop_while_a_server_busy_refusal_refunds_still_ends_with_one_terminal_event(world):
    """Probe 1 of the review: the turn was refused (no slot) and is refunding the day's message
    slot in a worker thread when the Stop arrives. Before the fix the task was cancelled inside
    the refusal branch and the stream ended with no event at all."""
    gate, entered = threading.Event(), threading.Event()
    refunds = []

    def slow_refund():
        entered.set()
        assert gate.wait(10)
        refunds.append(1)

    llm = FakeLLM(default=["unused"])

    async def go():
        agent = Agent(world.airline, llm, concurrency=TurnConcurrency(1, 0.05))
        agent.slots.active = 1  # another turn holds the only slot
        events = agent.stream_turn(world.alice, world.conv, "hi", on_not_stored=slow_refund)
        seen = asyncio.ensure_future(rest(events, []))
        await asyncio.to_thread(entered.wait, 5)
        answer = agent.cancel_run(world.alice, world.conv, events.run_id)
        gate.set()
        out = await seen
        await agent.wait_idle()
        agent.slots.active = 0
        released(agent, world)
        return answer, out, agent.run_state(events.run_id)

    answer, seen, state = asyncio.run(go())
    assert answer == "finishing"  # too late: the refusal is already being sent
    assert names(seen) == ["error"] and first_data(seen[0].data)["code"] == "server_busy"
    assert state == "failed" and refunds == [1] and stored(world) == []


def test_p2_1_a_run_cancelled_by_a_shutdown_still_ends_with_a_terminal_event(world):
    gate = asyncio.Event()
    llm = FakeLLM(["One. ", Wait(gate)])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = []
        await until(events, "delta", seen)
        agent._runs[events.run_id].task.cancel()  # not a Stop: e.g. the server shutting down
        await rest(events, seen)
        await asyncio.gather(*agent._tasks, return_exceptions=True)
        return seen, agent.run_state(events.run_id)

    seen, state = asyncio.run(go())
    assert names(seen)[-1] == "error" and seen[-1].data["code"] == "internal_error"
    assert sum(n in ("done", "cancelled", "error") for n in names(seen)) == 1 and state == "failed"


def test_p2_2_a_finished_run_keeps_no_turn_context(world):
    """Probe 2 of the review: the finished-run list kept every turn's context, cards and queue."""
    gate = asyncio.Event()
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["One. ", Wait(gate)], ["Two."])

    async def go():
        agent = Agent(world.airline, llm)
        stopped = agent.stream_turn(world.alice, world.conv, "A to D")
        await until(stopped, "delta", [])
        agent.cancel_run(world.alice, world.conv, stopped.run_id)
        await rest(stopped, [])
        done = agent.stream_turn(world.alice, world.conv, "again")
        await rest(done, [])
        await agent.wait_idle()
        return agent, [stopped.run_id, done.run_id]

    agent, run_ids = asyncio.run(go())
    for run_id in run_ids:
        run = agent._finished[run_id]
        assert run.turn is None and run.task is None
        assert agent.cancel_run(world.alice, world.conv, run_id) in ("cancelled", "completed")


def test_p2_8_the_model_is_told_that_a_stopped_replys_cards_cannot_be_confirmed(world):
    gate = asyncio.Event()
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["Ready. ", Wait(gate)], ["Sure."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A to D")
        await until(events, "delta", [])
        agent.cancel_run(world.alice, world.conv, events.run_id)
        await rest(events, [])
        await rest(agent.stream_turn(world.alice, world.conv, "book it"), [])
        await agent.wait_idle()

    asyncio.run(go())
    notes = [m["content"] for m in llm.requests[-1] if m["role"] == "system" and "stopped" in m["content"]]
    assert len(notes) == 1 and "cannot be confirmed" in notes[0] and "request_booking" in notes[0]


# P2-9: the cooperative checks are the second line of defence for a library that swallows the
# CancelledError of `steps_task.cancel()`. These tests accept a Stop *without* cancelling the task
# (exactly what a swallowed cancellation looks like to the loop) at each boundary.
def swallowed_stop(agent: Agent) -> None:
    """An accepted Stop whose task cancellation got lost. Safe from a worker thread in tests."""
    [run] = agent._runs.values()
    run.state = "cancelling"
    run.turn.cancelled = True


def test_p2_9_a_lost_cancellation_during_a_tool_thread_discards_its_result_and_stops(world):
    holder: dict = {}
    find_city = world.airline.find_city

    def stopping_find_city(name):
        swallowed_stop(holder["agent"])
        return find_city(name)

    world.airline.find_city = stopping_find_city
    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B")], default=["unused"])

    async def go():
        holder["agent"] = agent = Agent(world.airline, llm)
        seen = await rest(agent.stream_turn(world.alice, world.conv, "A and B?"), [])
        await agent.wait_idle()
        released(agent, world)
        return seen

    seen = asyncio.run(go())
    assert names(seen)[-1] == "cancelled" and "card" not in names(seen) and len(llm.bodies) == 1
    rows = world.store.list_tool_calls(world.alice, seen[0].data["id"])
    assert [r["error"] for r in rows] == ["cancelled: interrupted", "cancelled: not run"]


def test_p2_9_a_lost_cancellation_before_a_tool_runs_no_tool(world):
    holder: dict = {}
    ran = []
    find_city = world.airline.find_city
    world.airline.find_city = lambda name: (ran.append(name), find_city(name))[1]
    add_llm_call = world.store.add_llm_call

    def stopping_log(user_id, conversation_id, request, response, **kwargs):
        if response.get("tool_calls"):
            swallowed_stop(holder["agent"])
        return add_llm_call(user_id, conversation_id, request, response, **kwargs)

    world.store.add_llm_call = stopping_log
    llm = FakeLLM([Call("find_city", name="A")], default=["unused"])

    async def go():
        holder["agent"] = agent = Agent(world.airline, llm)
        seen = await rest(agent.stream_turn(world.alice, world.conv, "A?"), [])
        await agent.wait_idle()
        released(agent, world)
        return seen

    seen = asyncio.run(go())
    assert ran == [] and "status" not in names(seen) and names(seen)[-1] == "cancelled"
    assert [r["error"] for r in world.store.list_tool_calls(world.alice, seen[0].data["id"])] == ["cancelled: not run"]


def test_p2_9_a_lost_cancellation_after_a_tool_starts_no_further_model_request(world):
    holder: dict = {}
    add_tool_call = world.store.add_tool_call

    def stopping_row(*args, **kwargs):
        swallowed_stop(holder["agent"])  # the tool finished and is being recorded
        return add_tool_call(*args, **kwargs)

    world.store.add_tool_call = stopping_row
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], default=["never requested"])

    async def go():
        holder["agent"] = agent = Agent(world.airline, llm)
        seen = await rest(agent.stream_turn(world.alice, world.conv, "A to D"), [])
        await agent.wait_idle()
        released(agent, world)
        return seen

    seen = asyncio.run(go())
    assert len(llm.bodies) == 1 and names(seen)[-1] == "cancelled"
    [card] = seen[-1].data["message"]["cards"]  # the finished tool's card, not bookable
    assert card["state"]["bookable"] is False


def test_p2_9_a_stop_accepted_while_the_last_step_ends_still_ends_cancelled(world):
    holder: dict = {}
    add_llm_call = world.store.add_llm_call

    def stopping_log(user_id, conversation_id, request, response, **kwargs):
        if response.get("finish_reason") == "stop":
            swallowed_stop(holder["agent"])  # the steps return normally after this
        return add_llm_call(user_id, conversation_id, request, response, **kwargs)

    world.store.add_llm_call = stopping_log
    llm = FakeLLM(["All done."])

    async def go():
        holder["agent"] = agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = await rest(events, [])
        await agent.wait_idle()
        return seen, agent.run_state(events.run_id)

    seen, state = asyncio.run(go())
    assert names(seen)[-1] == "cancelled" and state == "cancelled"  # R4: an accepted Stop wins
    assert stored(world)[-1]["status"] == "cancelled"


def test_p2_9_a_stop_in_the_instant_the_steps_ended_is_finishing_and_the_reply_completes(world):
    """`cancel_run`'s `finishing` guard: the steps' result is set but `_turn` has not resumed."""
    answers = []
    llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["Here."])

    async def go():
        agent = Agent(world.airline, llm)
        steps = agent._steps

        async def steps_then_stop(turn, messages):
            result = await steps(turn, messages)
            run_id = next(iter(agent._runs))
            # runs after the task's result is set and before `_turn` wakes up (FIFO callbacks)
            asyncio.get_running_loop().call_soon(
                lambda: answers.append(agent.cancel_run(world.alice, world.conv, run_id)))
            return result

        agent._steps = steps_then_stop
        seen = await rest(agent.stream_turn(world.alice, world.conv, "A to D"), [])
        await agent.wait_idle()
        return seen

    seen = asyncio.run(go())
    assert answers == ["finishing"] and names(seen)[-1] == "done"
    assert seen[-1].data["message"]["cards"][0]["state"]["bookable"] is True


def test_p2_9_nothing_is_sent_after_the_terminal_event(world, caplog):
    llm = FakeLLM(["Hello."])

    async def go():
        agent = Agent(world.airline, llm)
        finish = agent._finish

        async def finish_twice(turn, fallback=None):
            await finish(turn, fallback)
            turn.emit("delta", {"text": "late"})
            turn.emit("done", {"message": {}})

        agent._finish = finish_twice
        seen = await rest(agent.stream_turn(world.alice, world.conv, "hi"), [])
        await agent.wait_idle()
        return seen

    seen = asyncio.run(go())
    assert names(seen).count("done") == 1 and names(seen)[-1] == "done" and "late" not in str(seen)
    assert "after done dropped" in caplog.text
