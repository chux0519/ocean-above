"""A scripted LLM for the CI mode, and the helpers the case scripts use to write replies that
copy display values out of tool results (what a well-behaved model does)."""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from app.agent import Completion, TextDelta


class Use:
    """A tool call in a script."""

    def __init__(self, tool: str, **args: Any):
        self.tool = tool
        self.args = args


Step = "list[str | Use] | Callable[[list[dict]], list[str | Use]]"


class ScriptedLLM:
    """Plays one scripted step per LLM call; a step is a list of text chunks and `Use` calls, or a
    function `messages -> list` (to read a value out of an earlier tool result). Running out of
    steps is an error: the case script and the agent disagree about the trajectory."""

    model = "scripted-fake"

    def __init__(self, steps: Sequence[Any]):
        self.steps = list(steps)
        self.calls = 0
        self.efforts: list[str | None] = []

    async def stream(self, messages, tools, *, reasoning_effort=None):
        self.calls += 1
        self.efforts.append(reasoning_effort)
        if not self.steps:
            raise AssertionError(f"scripted LLM has no step for call {self.calls}")
        step = self.steps.pop(0)
        if callable(step):
            step = step(messages)
        content, calls = "", []
        for item in step:
            if isinstance(item, Use):
                calls.append({"id": f"call_{self.calls}_{len(calls)}", "name": item.tool,
                              "arguments": json.dumps(item.args)})
            else:
                content += item
                yield TextDelta(item)
        yield Completion(content, calls, "tool_calls" if calls else "stop",
                         {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})


# ---------------------------------------------------------------- reply builders -------------
def tool_results(messages: list[dict]) -> list[dict]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def newest(messages: list[dict], key: str) -> dict:
    """The newest tool result that has `key` (this turn's results come last)."""
    return [r for r in tool_results(messages) if key in r][-1]


def last_quote_id(messages: list[dict]) -> str:
    return newest(messages, "quote_id")["quote_id"]


def quote_reply(messages):
    q = newest(messages, "price_display")
    via = f" via {', '.join(q['transfers'])}" if q["transfers"] else " direct"
    return [f"The cheapest route is{via}: ", f"{q['distance_display']} for ", f"{q['price_display']}. ",
            "Shall I prepare the booking?"]


def quote_reply_zh(messages):
    q = newest(messages, "price_display")
    via = "、".join(q["transfers"]) or "直飞"
    return [f"路线经过 {via}，", f"总距离 {q['distance_display']}，", f"价格 {q['price_display']}。", "需要我为您订票吗？"]


def no_service_reply(messages):
    issue = _issue(messages, "nearest")
    city, near = issue["city"], issue["nearest"][0]
    return [f"We have no service at {city}. ", f"The nearest served city is {near['city']}, ",
            f"{near['distance_display']} away. ", "Would you like a quote from there?"]


def _issue(messages, key: str) -> dict:
    """The first city problem with `key`, from a quote_route (`issues`) or find_city result."""
    for r in reversed(tool_results(messages)):
        for item in r.get("issues", [r]):
            if key in item:
                return item
    raise AssertionError(f"no tool result with {key}")


def ambiguous_reply(messages):
    names = " or ".join(c["city"] for c in _issue(messages, "candidates")["candidates"])
    return [f"There is more than one match: {names}. ", "Which one do you mean?"]


def unknown_reply(messages):
    first = _issue(messages, "suggestions")["suggestions"][0]
    return [f"I could not find that city. Did you mean {first}? ", "Please tell me which city you want."]


def same_city_reply(messages):
    return ["The origin and the destination are the same city. ", "Where would you like to fly to?"]


def confirm_card_reply(messages):
    return ["Please check the confirmation card and click Confirm to book it. ", "Nothing is booked until you do."]


def tickets_reply(messages):
    r = newest(messages, "count")
    if r["count"] == 0:
        return ["You have no tickets yet."]
    return [f"You have {r['count']} ticket" + ("" if r["count"] == 1 else "s") + ". ", "They are listed on the card."]


def say(*chunks: str):
    return list(chunks)
