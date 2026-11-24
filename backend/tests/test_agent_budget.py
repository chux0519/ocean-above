"""Per-turn agent budgets (issue #32, design §4.8), replayed against the scripted fake LLM:
tool calls per step and per turn, the turn token cap (reported, missing and retried usage), the
turn time limit, configuration validation, and the proof that a refused call is never executed
(including a mutation check: if the gate let refused calls through, these tests would notice)."""
from __future__ import annotations

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.agent import Agent, Completion, LLMError
from app.agent.budget import (CHARS_PER_TOKEN_ESTIMATE, AgentBudget, BudgetTracker, estimate_tokens,
                              refusal, reported_tokens)
from app.agent.context import build_context
from app.agent.templates import NO_ANSWER, fallback_reply
from app.agent.tools import Outcome
from app.airline import Airline
from app.api.settings import Settings

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, names, run_turn, tool_results
from .airline_helpers import World, fixture_schemas

SCHEMAS = fixture_schemas()
CITIES = ["Lima", "Paris", "Tokyo", "Cairo", "Madrid", "Oslo", "Rome", "Lagos"]


class CountingAirline(Airline):
    """Counts every tool the airline really executes, and every attempt to book."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed: list[tuple[str, tuple]] = []
        self.book_calls = 0

    def find_city(self, *args, **kwargs):
        self.executed.append(("find_city", args))
        return super().find_city(*args, **kwargs)

    def quote_route(self, *args, **kwargs):
        self.executed.append(("quote_route", args))
        return super().quote_route(*args, **kwargs)

    def request_booking(self, *args, **kwargs):
        self.executed.append(("request_booking", args))
        return super().request_booking(*args, **kwargs)

    def list_tickets(self, *args, **kwargs):
        self.executed.append(("list_my_tickets", args))
        return super().list_tickets(*args, **kwargs)

    def book(self, *args, **kwargs):
        self.book_calls += 1
        return super().book(*args, **kwargs)


class NoUsageLLM(FakeLLM):
    """A provider that reports no usage (some streams, some failed calls)."""

    async def stream(self, messages, tools, *, reasoning_effort=None):
        async for event in super().stream(messages, tools, reasoning_effort=reasoning_effort):
            if isinstance(event, Completion):
                event.usage = None
            yield event


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, real=True, airline_cls=CountingAirline)


def make(world, *steps, default=None, llm_cls=FakeLLM, **kwargs):
    llm = llm_cls(*steps, default=default)

    async def sleep(seconds):
        pass

    return Agent(world.airline, llm, sleep=sleep, **kwargs), llm


def finds(n, start=0):
    return [Call("find_city", name=CITIES[(start + i) % len(CITIES)]) for i in range(n)]


def turn(agent, world, text="Which of these cities do you fly to?"):
    events = run_turn(agent, world.alice, world.conv, text)
    return events, assert_stream(events, SCHEMAS)


def stored_calls(world):
    user = [m for m in world.store.list_messages(world.alice, world.conv) if m["role"] == "user"][-1]
    return world.store.list_tool_calls(world.alice, user["id"])


def assert_well_formed(messages):
    """Every assistant tool call is answered by exactly one tool message before the next
    assistant/user message (what OpenAI-compatible APIs require)."""
    open_ids: list[str] = []
    for m in messages:
        if m["role"] == "tool":
            assert m["tool_call_id"] in open_ids, m
            open_ids.remove(m["tool_call_id"])
            continue
        assert not open_ids, f"tool calls without a result: {open_ids}"
        if m["role"] == "assistant" and m.get("tool_calls"):
            open_ids = [c["id"] for c in m["tool_calls"]]
            assert len(set(open_ids)) == len(open_ids)
    assert not open_ids


def assert_executed_at_most(world, n):
    """The refused calls did not reach the airline (the check the mutation test targets)."""
    executed = len(world.airline.executed)
    assert executed <= n, f"{executed} tool calls reached the airline, budget allowed {n}"


# ============================================================ tool calls per step ============
def test_more_than_four_calls_in_one_completion_runs_only_four(world):
    agent, llm = make(world, finds(6), ["Here is what I found."])
    events, message = turn(agent, world)
    assert_executed_at_most(world, 4)
    assert [a[0] for _, a in world.airline.executed] == CITIES[:4]
    assert names(events).count("status") == 4
    # the next request answers all six calls, in order; the last two are structured refusals
    second = llm.requests[1]
    assert_well_formed(second[1:])
    results = tool_results(second)
    assert [r["code"] for r in results[:4]] != ["tool_budget_exceeded"] * 4
    assert [r["code"] for r in results[4:]] == ["tool_budget_exceeded"] * 2
    assert results[4] == refusal("per_step", AgentBudget())
    # the turn went on: the LLM's reply stays, the four cards are there
    assert message["text"] == "Here is what I found."
    # stored: one row per call (refusals marked), so the rebuilt history lines up
    rows = stored_calls(world)
    assert [r["error"] for r in rows] == [None] * 4 + ["budget: per_step"] * 2
    assert rows[4]["args"] == json.dumps({"name": CITIES[4]})
    ctx = build_context(world.store, world.alice, world.conv)
    assert_well_formed(ctx.messages)
    def calls(messages):  # one assistant message with all six calls, then their six results
        (m,) = [m for m in messages if m.get("tool_calls")]
        return [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in m["tool_calls"]]

    assert calls(ctx.messages) == calls(second) and len(calls(second)) == 6
    assert [r["code"] for r in tool_results(ctx.messages)] == [r["code"] for r in results]


def test_refused_calls_do_not_reach_the_guard_records(world):
    """A refusal's numbers (the limits) are not values the reply may quote."""
    agent, llm = make(world, finds(5), ["Only 4 per step, sorry."])
    events, message = turn(agent, world)
    assert names(events)[-2:] == ["replace", "done"]  # "4" traces to nothing the airline said


def test_refused_request_booking_changes_no_quote_state(world):
    quote = world.quote("Guangzhou", "Lima")
    world.done(list(quote.cards))
    status = world.status(quote.data["quote_id"])
    agent, llm = make(world, [*finds(4), Call("request_booking", quote_id=quote.data["quote_id"])], ["Done."])
    events, message = turn(agent, world, "book it")
    assert ("request_booking", (world.alice, world.conv, quote.data["quote_id"])) not in world.airline.executed
    assert world.status(quote.data["quote_id"]) == status
    assert all(c["type"] != "confirm" for c in message["cards"]) and world.airline.book_calls == 0
    assert world.ticket_count() == 0
    # no "Please check the confirmation card" sentence comes from a refused request_booking
    assert fallback_reply([Outcome("request_booking", "{}", "tool_budget_exceeded",
                                   refusal("per_step", AgentBudget()))]) == NO_ANSWER


# ============================================================ tool calls per turn ============
def test_crossing_twelve_calls_per_turn_ends_the_turn_without_running_more(world):
    agent, llm = make(world, finds(4, 0), finds(4, 4), finds(4, 0), finds(3, 2), ["never sent"])
    events, message = turn(agent, world)
    assert_executed_at_most(world, 12)
    assert len(world.airline.executed) == 12 and len(llm.requests) == 4
    assert names(events)[-2:] == ["replace", "done"]
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "tool_budget", "value": "12"}]
    rows = stored_calls(world)
    assert len(rows) == 15 and [r["error"] for r in rows[12:]] == ["budget: per_turn"] * 3
    assert_well_formed(build_context(world.store, world.alice, world.conv).messages)
    assert message["text"] != NO_ANSWER and "Lagos" in message["text"]  # template from the 12 results


def test_turn_limit_reached_inside_a_step(world):
    budget = AgentBudget(max_tool_calls_per_turn=6)
    agent, llm = make(world, finds(4), finds(4, 4), ["never sent"], budget=budget)
    events, message = turn(agent, world)
    assert len(world.airline.executed) == 6 and len(llm.requests) == 2
    assert [r["error"] for r in stored_calls(world)] == [None] * 6 + ["budget: per_turn"] * 2
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "tool_budget", "value": "6"}]


def test_exactly_using_the_turn_budget_still_lets_the_llm_answer(world):
    budget = AgentBudget(max_tool_calls_per_turn=4)
    agent, llm = make(world, finds(4), ["Here is what I found."], budget=budget)
    events, message = turn(agent, world)
    assert len(world.airline.executed) == 4 and message["text"] == "Here is what I found."


@given(per_step=st.integers(1, 5), per_turn=st.integers(1, 14),
       steps=st.lists(st.integers(0, 7), min_size=1, max_size=6))
def test_tracker_never_admits_more_than_the_budget(per_step, per_turn, steps):
    tracker = BudgetTracker(AgentBudget(max_tool_calls_per_step=per_step, max_tool_calls_per_turn=per_turn))
    admitted = 0
    for n in steps:
        if tracker.stop_before_step() is not None:
            break
        in_step = sum(tracker.admit_tool_call() is None for _ in range(n))
        assert in_step <= per_step
        admitted += in_step
    assert admitted == tracker.tool_calls <= per_turn


def test_mutation_letting_refused_calls_through_is_caught(world, monkeypatch):
    """If the gate admitted every call, the excess would reach the airline and the check fails."""
    monkeypatch.setattr(BudgetTracker, "admit_tool_call", lambda self: None)
    agent, llm = make(world, finds(6), ["Here is what I found."])
    turn(agent, world)
    with pytest.raises(AssertionError, match="6 tool calls reached the airline"):
        assert_executed_at_most(world, 4)


# ============================================================ tokens ========================
def test_turn_token_cap_stops_before_the_next_step(world):
    # every fake call reports 110 total tokens: 110 < 200, 220 >= 200 -> no third call
    agent, llm = make(world, default=finds(1), budget=AgentBudget(max_total_tokens_per_turn=200))
    events, message = turn(agent, world)
    assert len(llm.requests) == 2 and len(world.airline.executed) == 2
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "token_budget", "value": "200"}]
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert [c["response"]["budget"] for c in logged] == [
        {"charged_tokens": 110, "estimated": False, "turn_tokens": 110},
        {"charged_tokens": 110, "estimated": False, "turn_tokens": 220}]
    assert [c["tokens"] for c in logged] == [110, 110]  # the column keeps the observed usage
    assert_well_formed(build_context(world.store, world.alice, world.conv).messages)


def test_missing_usage_is_charged_conservatively(world):
    """A completed call without usage: the request at 2 characters per token plus the output it
    produced at the same rate (06 review P1-2: not the whole 4096 allowance)."""
    budget = AgentBudget(max_total_tokens_per_turn=1000)
    agent, llm = make(world, default=finds(1), llm_cls=NoUsageLLM, budget=budget)
    events, message = turn(agent, world)
    # the first call alone is charged more than the cap: its request is larger than 2000 characters
    assert len(llm.requests) == 1
    logged = world.store.list_llm_calls(world.alice, world.conv)
    charged = logged[0]["response"]["budget"]
    call = logged[0]["response"]["tool_calls"][0]
    output = len(call["name"]) + len(call["arguments"])
    assert charged["estimated"] is True
    assert charged["charged_tokens"] == estimate_tokens(logged[0]["request"]["messages"], 4096, output)
    assert charged["charged_tokens"] == math.ceil(
        len(json.dumps(logged[0]["request"]["messages"], separators=(",", ":"), ensure_ascii=False)) / 2) \
        + math.ceil(output / 2)
    assert logged[0]["tokens"] == charged["charged_tokens"]  # the site budget counts the same (P2-9)
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "token_budget", "value": "1000"}]


def test_failed_and_retried_calls_count(world):
    """An HTTP error status read no response body: charged 0. A stream cut after a few
    characters is estimated from those. The successful retry is charged its usage."""
    no_body = LLMError("LLM HTTP 503", retryable=True, status=503)
    cut = ["Hel", LLMError("LLM transport error: ReadError", retryable=True)]  # "Hel" is never released
    agent, llm = make(world, no_body, cut, ["Hello."])
    events, message = turn(agent, world, "hi")
    assert message["text"] == "Hello." and len(llm.requests) == 3
    failed, broken, ok = world.store.list_llm_calls(world.alice, world.conv)
    assert failed["response"]["budget"] == {"charged_tokens": 0, "estimated": False, "turn_tokens": 0}
    assert failed["tokens"] == 0
    estimate = broken["response"]["budget"]["charged_tokens"]
    assert broken["response"]["budget"]["estimated"] is True
    assert estimate == estimate_tokens(broken["request"]["messages"], 4096, len("Hel"))
    assert ok["response"]["budget"] == {"charged_tokens": 110, "estimated": False, "turn_tokens": estimate + 110}


def test_retries_can_exhaust_the_token_budget(world):
    """Two streams cut after 4000 characters each: the first leaves tokens, the second uses them
    up, so there is no third attempt (06 review P2-2: retries stop at the cap too)."""
    def cut():
        return LLMError("LLM transport error: ReadError", retryable=True, received=4000)

    budget = AgentBudget(max_total_tokens_per_turn=6000)
    agent, llm = make(world, cut(), cut(), finds(1), ["never sent"], budget=budget)
    events, message = turn(agent, world)
    first, second = world.store.list_llm_calls(world.alice, world.conv)
    each = first["response"]["budget"]["charged_tokens"]
    assert each == estimate_tokens(first["request"]["messages"], 4096, 4000) and 3000 < each < 6000
    assert second["response"]["budget"]["turn_tokens"] == 2 * each
    assert len(llm.requests) == 2 and world.airline.executed == []
    assert world.store.list_messages(world.alice, world.conv)[-1]["guard_problems"] == [
        {"kind": "token_budget", "value": "6000"}]


def test_token_helpers():
    assert reported_tokens({"total_tokens": 7}) == 7
    for bad in (None, {}, {"total_tokens": None}, {"total_tokens": -1}, {"total_tokens": "7"},
                {"total_tokens": True}, "x", {"total_tokens": 0}):
        assert reported_tokens(bad) is None
    messages = [{"role": "user", "content": "x" * 99}]
    chars = len(json.dumps(messages, separators=(",", ":")))
    assert estimate_tokens(messages, 10) == math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE) + 10
    tracker = BudgetTracker(AgentBudget(max_total_tokens_per_turn=1000))
    assert tracker.stop_before_step() is None
    assert tracker.charge({"total_tokens": 999}, []) == (999, False)
    assert tracker.stop_before_step() is None
    tracker.charge({"total_tokens": 1}, [])
    assert tracker.stop_before_step() == {"kind": "token_budget", "value": "1000"}


# ============================================================ time, steps ===================
def test_turn_seconds_come_from_the_budget(world):
    agent, _ = make(world, [Wait(seconds=5)], budget=AgentBudget(max_turn_seconds=0.1), llm_timeout=5)
    assert agent.turn_timeout == 0.1
    events = run_turn(agent, world.alice, world.conv, "hi")
    assert names(events) == ["user_message", "error"] and events[-1].data["code"] == "llm_unavailable"


def test_defaults_match_the_issue_and_keep_the_old_limits(world):
    b = AgentBudget()
    assert (b.max_steps, b.max_tool_calls_per_step, b.max_tool_calls_per_turn, b.max_output_tokens_per_call,
            b.max_total_tokens_per_turn, b.max_turn_seconds) == (6, 4, 12, 4096, 60000, 90.0)
    agent, _ = make(world)
    assert (agent.max_steps, agent.turn_timeout, agent.budget) == (6, 90.0, b)
    agent, _ = make(world, budget=AgentBudget(max_steps=3), max_steps=2, turn_timeout=5)
    assert agent.budget == AgentBudget(max_steps=2, max_turn_seconds=5)
    from app.agent.model import MAX_TOKENS
    assert MAX_TOKENS == b.max_output_tokens_per_call


# ============================================================ configuration =================
def test_budget_from_env():
    assert AgentBudget.from_env({}) == AgentBudget()
    env = {"AGENT_MAX_STEPS": "8", "AGENT_MAX_TOOL_CALLS_PER_STEP": "2", "AGENT_MAX_TOOL_CALLS_PER_TURN": "10",
           "AGENT_MAX_OUTPUT_TOKENS_PER_CALL": "2048", "AGENT_MAX_TOTAL_TOKENS_PER_TURN": "50_000",
           "AGENT_MAX_TURN_SECONDS": "45.5"}
    assert AgentBudget.from_env(env) == AgentBudget(8, 2, 10, 2048, 50_000, 45.5)
    assert Settings.from_env(env).agent_budget == AgentBudget(8, 2, 10, 2048, 50_000, 45.5)
    assert Settings.from_env({}).agent_budget == AgentBudget()


@pytest.mark.parametrize("var,value", [
    ("AGENT_MAX_STEPS", "0"), ("AGENT_MAX_STEPS", "-1"), ("AGENT_MAX_STEPS", "21"), ("AGENT_MAX_STEPS", "x"),
    ("AGENT_MAX_STEPS", "2.5"), ("AGENT_MAX_TOOL_CALLS_PER_STEP", "0"), ("AGENT_MAX_TOOL_CALLS_PER_TURN", "1000"),
    ("AGENT_MAX_OUTPUT_TOKENS_PER_CALL", "10"), ("AGENT_MAX_TOTAL_TOKENS_PER_TURN", "999"),
    ("AGENT_MAX_TURN_SECONDS", "nan"), ("AGENT_MAX_TURN_SECONDS", "inf"), ("AGENT_MAX_TURN_SECONDS", "0"),
    ("AGENT_MAX_TURN_SECONDS", "601")])
def test_bad_budget_env_is_refused(var, value):
    with pytest.raises(ValueError, match=var):
        AgentBudget.from_env({var: value})
    with pytest.raises(ValueError):
        Settings.from_env({var: value})


@pytest.mark.parametrize("kwargs", [{"max_steps": 0}, {"max_tool_calls_per_turn": -3}, {"max_steps": True},
                                    {"max_steps": 2.0}, {"max_turn_seconds": float("nan")},
                                    {"max_total_tokens_per_turn": "100"}])
def test_budget_values_are_positive_numbers(kwargs):
    with pytest.raises(ValueError):
        AgentBudget(**kwargs)
