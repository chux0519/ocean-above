"""A deterministic, stateless fake LLM for the benchmark: every chat turn is one realistic quote
turn (quote_route tool call, then a short reply), so a turn writes what a real one writes:
user message, LLM call logs, a tool call log, a quote, the assistant message with its card.

The user text is `Quote <origin> | <destination>`. `delay` seconds are slept before each LLM step
(simulated model latency; 0 = measure the database, not the model)."""
from __future__ import annotations

import asyncio
import json

from app.agent import Completion, ScriptedLLM, TextDelta

from .common import FAKE_REPLY

USAGE = {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240}


def user_text(origin: str, destination: str) -> str:
    return f"Quote {origin} | {destination}"


class BenchLLM(ScriptedLLM):
    model = "bench-fake-llm"

    def __init__(self, delay: float = 0.0):
        super().__init__()
        self.delay = delay

    async def stream(self, messages, tools, *, reasoning_effort=None):
        if self.delay:
            await asyncio.sleep(self.delay)
        last = messages[-1]
        if last["role"] == "user" and str(last.get("content", "")).startswith("Quote "):
            origin, _, destination = last["content"][len("Quote "):].partition(" | ")
            call = {"id": "call_bench_0", "name": "quote_route",
                    "arguments": json.dumps({"origin": origin.strip(), "destination": destination.strip()})}
            yield Completion("", [call], "tool_calls", USAGE, "")
            return
        yield TextDelta(FAKE_REPLY)
        yield Completion(FAKE_REPLY, [], "stop", USAGE, "")
