"""Scaffolding for the agent tests: a scripted fake LLM and helpers to run a turn and to check
the event stream against the contract."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.agent import Agent, Completion, Event, TextDelta

from .airline_helpers import assert_card, assert_message


class Call:
    """A tool call in a script. `raw` replaces the JSON arguments (to send broken ones)."""

    def __init__(self, tool: str, /, raw: str | None = None, **args: Any):
        self.name = tool
        self.arguments = raw if raw is not None else json.dumps(args)


class Think:
    """Reasoning text of a step (thinking mode): stored with the call, never a TextDelta."""

    def __init__(self, text: str):
        self.text = text


class Finish:
    """Override the finish_reason of a step (e.g. "length")."""

    def __init__(self, reason: str):
        self.reason = reason


class Wait:
    """Pause the scripted stream: `await gate.wait()` (asyncio.Event) or sleep `seconds`."""

    def __init__(self, gate: asyncio.Event | None = None, seconds: float = 0.0):
        self.gate, self.seconds = gate, seconds


class FakeLLM:
    """Plays one scripted step per LLM call. A step is a list of: str (a text chunk), Call,
    Wait, or an Exception (raised at that point); or an Exception (the call fails at once); or a
    function `messages -> step` for steps that need a value from an earlier tool result. When
    the script is used up, every further call gets `default` (if given)."""

    model = "fake-llm"

    def __init__(self, *steps: Any, default: Any = None):
        self.steps = list(steps)
        self.default = default
        self.requests: list[list[dict]] = []  # the messages of every call (deep copies)
        self.tools_seen: list[list[str]] = []
        self.efforts: list[str | None] = []  # the per-request reasoning_effort of every call

    async def stream(self, messages, tools, *, reasoning_effort=None):
        self.requests.append(json.loads(json.dumps(messages)))
        self.efforts.append(reasoning_effort)
        self.tools_seen.append([t["function"]["name"] for t in tools])
        if self.steps:
            step = self.steps.pop(0)
        elif self.default is not None:
            step = self.default
        else:
            raise AssertionError("FakeLLM: the script has no step for this call")
        if callable(step):
            step = step(messages)
        if isinstance(step, Exception):
            raise step
        content, calls, reasoning, finish = "", [], "", None
        for item in step:
            if isinstance(item, Exception):
                raise item
            if isinstance(item, Wait):
                if item.gate is not None:
                    await item.gate.wait()
                else:
                    await asyncio.sleep(item.seconds)
            elif isinstance(item, Finish):
                finish = item.reason
            elif isinstance(item, Think):
                reasoning += item.text
            elif isinstance(item, Call):
                calls.append({"id": f"call_{len(self.requests)}_{len(calls)}", "name": item.name,
                              "arguments": item.arguments})
            else:
                content += item
                yield TextDelta(item)
        yield Completion(content, calls, finish or ("tool_calls" if calls else "stop"),
                         {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}, reasoning)


def tool_results(messages: list[dict]) -> list[dict]:
    """The decoded tool results in a context, oldest first."""
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def last_quote_id(messages: list[dict]) -> str:
    return [r["quote_id"] for r in tool_results(messages) if "quote_id" in r][-1]


def run_turn(agent: Agent, user: str, conv: str, text: str) -> list[Event]:
    async def go():
        events = [e async for e in agent.stream_turn(user, conv, text)]
        await agent.wait_idle()
        return events

    return asyncio.run(go())


def names(events: list[Event]) -> list[str]:
    return [e.name for e in events]


def final_text(events: list[Event]) -> str:
    """What the client shows at the end, built the way a client builds it: deltas appended,
    `replace` replacing everything so far."""
    text = ""
    for e in events:
        if e.name == "delta":
            text += e.data["text"]
        elif e.name == "replace":
            text = e.data["text"]
    return text


def assert_stream(events: list[Event], schemas: dict | None = None) -> dict | None:
    """The stream follows docs/api/contract.md. Returns the `done` message (None for `error`)."""
    kinds = names(events)
    assert kinds[0] == "user_message", kinds
    assert kinds.count("user_message") == 1
    assert kinds[-1] in ("done", "error") and sum(k in ("done", "error") for k in kinds) == 1, kinds
    assert set(kinds) <= {"user_message", "status", "card", "delta", "replace", "ping", "done", "error"}
    assert_message(events[0].data, schemas)
    assert events[0].data["role"] == "user" and events[0].data["cards"] == []
    pushed = []
    for e in events[1:-1]:
        if e.name == "status":
            assert set(e.data) == {"text"} and e.data["text"]
        elif e.name == "card":
            assert set(e.data) == {"card"}
            assert_card(e.data["card"], schemas)
            state = e.data["card"].get("state")
            assert state is None or (state["bookable"] is False and state["nonce"] is None)
            pushed.append(e.data["card"])
        elif e.name in ("delta", "replace"):
            assert set(e.data) == {"text"} and isinstance(e.data["text"], str) and e.data["text"]
        elif e.name == "ping":
            assert e.data == {}
    last = events[-1]
    if last.name == "error":
        assert set(last.data) == {"code", "message"}
        return None
    assert set(last.data) == {"message"}
    message = last.data["message"]
    assert_message(message, schemas)
    assert message["role"] == "assistant"
    assert final_text(events).rstrip() == message["text"]
    # the final message has exactly the cards that were pushed, in order
    assert [(c["type"], c.get("id")) for c in message["cards"]] == [(c["type"], c.get("id")) for c in pushed]
    return message


Step = Callable[[list[dict]], list]
