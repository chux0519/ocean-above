"""The agent runtime on Pydantic AI (ADR-001, #49): what the migration itself must guarantee.

- Cancellation (the runtime side of #48's Stop): `Agent.cancel` closes the model's HTTP stream,
  starts no further LLM request or tool call, and lets a tool thread that already started end
  before the turn stores its reply and releases the conversation lock.
- Tools run one at a time in call order; the model's arguments never name the user.
- Tracing: nothing is instrumented or exported by default; `tracer_provider` is the entry point
  #35 will use. No outbound connection during a turn (no telemetry, no price data, no banner).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading

import pytest

from app.agent import Agent
from app.agent.loop import _Runtime

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, names
from .airline_helpers import World


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def stored(world):
    return world.store.list_messages(world.alice, world.conv)


async def _first(events, name):
    async for e in events:
        if e.name == name:
            return e
    raise AssertionError(f"no {name} event")


# ============================================================ cancellation ===================
def test_cancel_while_the_model_streams_closes_the_stream_and_ends_the_turn(world):
    gate = asyncio.Event()  # never set: the model "keeps thinking"
    llm = FakeLLM(["Let me look. ", Wait(gate), "never sent."], default=["unused"])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        seen = [await _first(events, "user_message"), await _first(events, "delta")]
        assert agent.cancel(world.bob, world.conv) is False  # not bob's turn
        assert world.airline.locks.locked(world.conv)
        assert agent.cancel(world.alice, world.conv) is True
        seen += [e async for e in events]
        await agent.wait_idle()
        assert agent.cancel(world.alice, world.conv) is False  # nothing running any more
        return seen

    events = asyncio.run(go())
    assert names(events)[-2:] == ["replace", "done"]
    assert llm.streams == ["closed early"] and len(llm.bodies) == 1  # stream closed, no further request
    assert stored(world)[-1]["guard_problems"] == [{"kind": "cancelled", "value": ""}]
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert logged[-1]["response"]["aborted"] == "cancelled" and logged[-1]["response"]["content"] == "Let me look. "
    assert not world.airline.locks.locked(world.conv)


def test_cancel_during_a_tool_waits_for_its_thread_and_runs_no_later_tool(world):
    block, entered = threading.Event(), threading.Event()
    find_city = world.airline.find_city
    ran: list[str] = []

    def slow_find_city(name):
        ran.append(name)
        entered.set()
        block.wait(5)
        return find_city(name)

    world.airline.find_city = slow_find_city
    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B")], default=["unused"])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "A and B?")
        collected = asyncio.ensure_future(_collect(events))
        await asyncio.to_thread(entered.wait, 5)
        assert agent.cancel(world.alice, world.conv) is True
        await asyncio.sleep(0.2)
        # the thread is still running: the turn has not stored its reply, the lock is held
        assert not collected.done() and world.airline.locks.locked(world.conv)
        block.set()
        out = await collected
        await agent.wait_idle()
        return out

    events = asyncio.run(go())
    assert names(events)[-1] == "done"
    assert ran == ["A"] and len(llm.bodies) == 1  # B never ran, no LLM request after the cancel
    assert stored(world)[-1]["guard_problems"] == [{"kind": "cancelled", "value": ""}]
    assert not world.airline.locks.locked(world.conv)
    # as with the turn's time limit (design §4.9): the interrupted call's thread ran to its end, but
    # its result is not part of the turn (no card, no tool_calls row). Settling such results is #48.
    turn_id = events[0].data["id"]
    assert world.store.list_tool_calls(world.alice, turn_id) == []
    assert "card" not in names(events)


async def _collect(events):
    return [e async for e in events]


def test_the_next_turn_works_after_a_cancel(world):
    gate = asyncio.Event()
    llm = FakeLLM(["One. ", Wait(gate)], ["Hello again."])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, "hi")
        await _first(events, "delta")
        agent.cancel(world.alice, world.conv)
        [e async for e in events]
        await agent.wait_idle()
        second = [e async for e in agent.stream_turn(world.alice, world.conv, "again")]
        await agent.wait_idle()
        return second

    second = asyncio.run(go())
    assert assert_stream(second)["text"] == "Hello again."


# ============================================================ tools ==========================
def test_tools_run_one_at_a_time_in_call_order(world):
    active, order, peak = [0], [], [0]
    lock = threading.Lock()
    find_city = world.airline.find_city

    def tracked(name):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        order.append(name)
        threading.Event().wait(0.05)
        with lock:
            active[0] -= 1
        return find_city(name)

    world.airline.find_city = tracked
    llm = FakeLLM([Call("find_city", name="A"), Call("find_city", name="B"), Call("find_city", name="C")], ["Ok."])
    agent = Agent(world.airline, llm)
    events = asyncio.run(_turn(agent, world))
    assert names(events)[-1] == "done"
    assert order == ["A", "B", "C"] and peak[0] == 1
    assert [e.data["text"] for e in events if e.name == "status"] == ["Looking up the city…"] * 3


async def _turn(agent, world, text="hi"):
    events = [e async for e in agent.stream_turn(world.alice, world.conv, text)]
    await agent.wait_idle()
    return events


def test_the_model_cannot_name_the_user(world):
    """The tools take the user and the conversation from the run's deps; an argument naming
    another user is an unknown argument (the schema has no such field)."""
    llm = FakeLLM([Call("list_my_tickets", user_id=world.bob)], ["Ok."])
    agent = Agent(world.airline, llm)
    asyncio.run(_turn(agent, world))
    result = json.loads([m for m in llm.requests[1] if m["role"] == "tool"][0]["content"])
    assert result["code"] == "invalid_arguments" and "user_id" in result["message"]
    for tool in llm.bodies[0]["tools"]:
        assert not {"user_id", "conversation_id", "user", "conversation"} & set(
            tool["function"]["parameters"]["properties"])


def test_there_is_no_booking_tool_in_the_runtime(world):
    agent = Agent(world.airline, FakeLLM(["Hi."]))
    names_ = sorted(agent._runtime._function_toolset.tools)
    assert names_ == ["find_city", "list_my_tickets", "quote_route", "request_booking"]


# ============================================================ tracing ========================
def test_no_tracing_by_default(world):
    agent = Agent(world.airline, FakeLLM(["Hi."]))
    from pydantic_ai.capabilities import Instrumentation
    assert not any(isinstance(c, Instrumentation) for c in agent._runtime.root_capability.capabilities)
    assert _Runtime._instrument_default is False  # nobody called Agent.instrument_all()
    assert importlib.util.find_spec("logfire") is None  # only the no-op `logfire-api` shim is installed
    assert os.environ.get("PYDANTIC_AI_NO_BANNER") == "1"


def test_a_turn_opens_no_outbound_connection(world):
    """With the scripted model there is no network at all: any socket connect during a turn would be
    telemetry, a price-data download or the like."""
    connects: list = []
    active = [True]

    def hook(event, args):
        if active[0] and event in ("socket.connect", "socket.getaddrinfo"):
            connects.append((event, repr(args)[:80]))

    sys.addaudithook(hook)
    try:
        llm = FakeLLM([Call("quote_route", origin="A", destination="D")], ["See the card."])
        agent = Agent(world.airline, llm)
        events = asyncio.run(_turn(agent, world))
    finally:
        active[0] = False  # an audit hook cannot be removed; it goes quiet
    assert names(events)[-1] == "done"
    assert connects == []


def test_a_tracer_provider_gets_the_runtime_spans(world):
    """The entry point for #35: spans of the run, the model requests and the tool calls, without
    message content."""
    from opentelemetry.trace import NoOpTracer, NoOpTracerProvider

    spans: list[str] = []

    class Tracer(NoOpTracer):
        def start_span(self, name, *args, **kwargs):
            spans.append(name)
            return super().start_span(name, *args, **kwargs)

        def start_as_current_span(self, name, *args, **kwargs):
            spans.append(name)
            return super().start_as_current_span(name, *args, **kwargs)

    class Provider(NoOpTracerProvider):
        def get_tracer(self, *args, **kwargs):
            return Tracer()

    llm = FakeLLM([Call("find_city", name="A")], ["Ok."])
    agent = Agent(world.airline, llm, tracer_provider=Provider())
    asyncio.run(_turn(agent, world))
    assert any(s.startswith("invoke_agent") for s in spans), spans
    assert any(s.startswith("chat ") for s in spans) and any(s == "execute_tool find_city" for s in spans), spans
