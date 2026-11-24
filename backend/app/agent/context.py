"""Rebuilds the LLM context of a conversation from app.db (design §4.3).

History = every stored message plus the tool calls of every earlier turn, in order. Tool results
are the compact JSON the LLM saw at the time (names, ids, display values) - never a card from
`Airline.messages`, so a confirmation nonce cannot enter the context. When the history is larger
than the budget, the oldest turns are shortened: texts cut, tool results reduced to their code
and ids (quote and ticket ids are always kept). Above a hard limit the oldest turns are dropped
and replaced by one system message listing their quote and ticket ids.

Thinking mode: DeepSeek asks that, in requests that carry tools, the `reasoning_content` of
earlier assistant messages is passed back. The reasoning is stored with every LLM call
(`llm_calls.response`, tagged with the turn's user message id) and is put back here on the
assistant message it belongs to. It is passed back verbatim or not at all: turns that were
shortened, replies replaced by a template and turns made with thinking off carry none.

A reply the user stopped (#48): its tool calls without a result are stored as `cancelled` (their
result is never given to the model), its text is the part the guard had released, and a system
note follows it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .model import wire_tool_call
from .tools import slim

HISTORY_CHAR_BUDGET = 40_000  # roughly 10k tokens of history kept in full
HISTORY_HARD_LIMIT = 80_000  # beyond this the oldest (already shortened) turns are dropped
SHORT_TEXT_CHARS = 300
STOPPED_NOTE = "[the user stopped the previous reply; tool calls marked cancelled did not run to the end]"
_ID_KEYS = ("quote_id", "ticket_id", "origin", "destination", "city", "query", "count", "status")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Context:
    messages: list[dict] = field(default_factory=list)  # without the system prompt
    records: list[Any] = field(default_factory=list)  # what the ReplyGuard may trace values to
    user_texts: list[str] = field(default_factory=list)  # the user's messages, as shown to the LLM


@dataclass
class _Turn:
    user: dict
    tool_calls: list[dict] = field(default_factory=list)
    later: list[dict] = field(default_factory=list)  # assistant and system messages, in order
    completions: list[dict] = field(default_factory=list)  # logged LLM responses that completed

    def size(self) -> int:
        return (len(self.user["text"]) + sum(len(_dumps(c["result"])) + 80 for c in self.tool_calls)
                + sum(len(m["text"]) + len(_dumps(m["cards"])) for m in self.later)
                + sum(len(c.get("reasoning_content") or "") for c in self.completions))

    def steps(self) -> list[tuple[str, list[dict]]] | None:
        """The tool calls grouped as the LLM made them: [(reasoning, [tool call rows])], or None
        when the call log does not line up with the stored tool calls."""
        rows, out = list(self.tool_calls), []
        for completion in self.completions:
            calls = completion.get("tool_calls") or []
            if not calls:
                continue
            taken, rows = rows[:len(calls)], rows[len(calls):]
            if [c.get("name") for c in calls] != [r["name"] for r in taken]:
                return None
            out.append((completion.get("reasoning_content") or "", taken))
        return None if rows else out

    def final_reasoning(self, text: str) -> str:
        """Reasoning of the LLM call that wrote `text` ("" when the text is not the LLM's)."""
        last = self.completions[-1] if self.completions else {}
        if last.get("tool_calls") or not text.rstrip().endswith((last.get("content") or "\0").rstrip()):
            return ""
        return last.get("reasoning_content") or ""


def _short_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    out = {"code": result.get("code"), "shortened": True}
    out.update({k: result[k] for k in _ID_KEYS if k in result})
    if isinstance(result.get("tickets"), list):
        out["ticket_ids"] = [t.get("id") for t in result["tickets"] if isinstance(t, dict)]
    return out


def _cut(text: str, short: bool) -> str:
    return text if not short or len(text) <= SHORT_TEXT_CHARS else text[:SHORT_TEXT_CHARS] + " […]"


def _booking_event(message: dict, short: bool) -> dict | None:
    """A system message of the conversation (a ticket issued by the user's click) as a record."""
    tickets = [c for c in message["cards"] if c.get("type") == "ticket"]
    if not tickets:
        return None
    ticket = slim(tickets[0])
    if short:
        ticket = {k: ticket[k] for k in ("id", "quote_id", "origin", "destination") if k in ticket}
    return {"event": "ticket_booked_by_user_click", "ticket": ticket}


def _assistant(content: str, reasoning: str, tool_calls: list[dict] | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _emit(turn: _Turn, short: bool, out: Context) -> None:
    out.messages.append({"role": "user", "content": _cut(turn.user["text"], short)})
    out.user_texts.append(out.messages[-1]["content"])
    steps = None if short else turn.steps()
    if steps is None:  # no usable call log: one assistant message per call, no reasoning
        steps = [("", [call]) for call in turn.tool_calls]
    for reasoning, calls in steps:
        out.messages.append(_assistant("", reasoning, [
            wire_tool_call("call_" + call["id"], call["name"], call["args"]) for call in calls]))
        for call in calls:
            result = _short_result(call["result"]) if short else call["result"]
            if result is None:
                result = {"code": "tool_failed"}
            out.messages.append({"role": "tool", "tool_call_id": "call_" + call["id"], "content": _dumps(result)})
            out.records.append(result)
    for message in turn.later:
        if message["role"] == "assistant" and message.get("status") == "cancelled":
            # a reply the user stopped (#48, R7): only its released text, then a note, so the model
            # knows the reply was cut and an empty one leaves no empty assistant message
            if message["text"].strip():
                out.messages.append(_assistant(_cut(message["text"], short), ""))
            out.messages.append({"role": "system", "content": STOPPED_NOTE})
        elif message["role"] == "assistant":
            reasoning = "" if short or message["guard_problems"] else turn.final_reasoning(message["text"])
            out.messages.append(_assistant(_cut(message["text"], short), reasoning))
        elif message["role"] == "system":
            event = _booking_event(message, short)
            if event is not None:
                out.messages.append({"role": "system", "content": "[booking event] " + _dumps(event)})
                out.records.append(event)


def build_context(store: Any, user_id: str, conversation_id: str,
                  budget: int = HISTORY_CHAR_BUDGET, hard_limit: int = HISTORY_HARD_LIMIT) -> Context:
    """Context for the next LLM call; the last stored message must be the user's new message."""
    turns: list[_Turn] = []
    by_message: dict[str, _Turn] = {}
    for message in store.list_messages(user_id, conversation_id):
        if message["role"] == "user":
            turns.append(_Turn(message, store.list_tool_calls(user_id, message["id"])))
            by_message[message["id"]] = turns[-1]
        elif turns:
            turns[-1].later.append(message)
    for call in store.list_llm_calls(user_id, conversation_id):
        request, response = call["request"], call["response"]
        owner = by_message.get(request.get("turn")) if isinstance(request, dict) else None
        if owner is not None and isinstance(response, dict) and "finish_reason" in response \
                and not response.get("tool_calls_not_run"):
            # failed and aborted calls have no finish_reason; a completion whose tool calls were
            # all refused as a flood (loop P2-3) has no stored tool calls to line up with
            owner.completions.append(response)
    # the newest turns stay in full; older ones are shortened once the budget is used up
    full_from, used = len(turns), 0
    for i in range(len(turns) - 1, -1, -1):
        used += turns[i].size()
        if used > budget and i < len(turns) - 1:
            break
        full_from = i
    parts = []
    for i, turn in enumerate(turns):
        part = Context()
        _emit(turn, i < full_from, part)
        parts.append(part)
    # hard limit: drop the oldest turns (never the newest one) into a summary of their ids
    sizes = [len(_dumps(p.messages)) for p in parts]
    dropped = 0
    while dropped < len(parts) - 1 and sum(sizes[dropped:]) > hard_limit:
        dropped += 1
    out = Context()
    if dropped:
        quote_ids, ticket_ids = _ids(parts[:dropped])
        summary = {"omitted_turns": dropped, "quote_ids": quote_ids, "ticket_ids": ticket_ids}
        out.messages.append({"role": "system", "content": "[earlier turns omitted] " + _dumps(summary)})
        out.records.append(summary)
    for part in parts[dropped:]:
        out.messages += part.messages
        out.records += part.records
        out.user_texts += part.user_texts
    return out


def _ids(parts: list[Context]) -> tuple[list[str], list[str]]:
    text = _dumps([p.records for p in parts])
    found = dict.fromkeys(re.findall(r"\b[qt]_[0-9a-f]{6,}\b", text))
    return [i for i in found if i.startswith("q_")], [i for i in found if i.startswith("t_")]
