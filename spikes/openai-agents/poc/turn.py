"""One turn on the SDK the way the migrated loop would drive it: SDK events in, our events out.

`run_turn` consumes `Runner.run_streamed(...).stream_events()` and
- turns text deltas into guarded sentences (SentenceBuffer + ReplyGuard, before anything is emitted);
- on the first guard problem: `result.cancel()` (upstream stream closed) and a `replace` event;
- emits `status` / `card` from the tool adapter's outcomes, `delta` per released sentence;
- collects usage per LLM call from the hooks (provider's raw usage).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agents import Runner
from openai.types.responses import ResponseTextDeltaEvent

from app.agent.guard import CityIndex, ReplyGuard, SentenceBuffer

from common import StepHooks, TurnCtx


@dataclass
class TurnLog:
    events: list[tuple[str, Any]] = field(default_factory=list)  # our SSE-like events
    sdk: list[str] = field(default_factory=list)  # SDK event kinds, in order (compressed)
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_items: int = 0
    reasoning_chars: int = 0
    first_delta_s: float | None = None
    first_release_s: float | None = None
    text: str = ""
    released: str = ""
    guard_problems: list[dict] | None = None
    usage: Any = None
    raw_usage: list[Any] = field(default_factory=list)
    llm_calls: int = 0
    seconds: float = 0
    error: str | None = None
    result: Any = None


def _kind(ev: Any) -> str:
    if ev.type == "raw_response_event":
        return "raw:" + getattr(ev.data, "type", "?")
    if ev.type == "run_item_stream_event":
        return "item:" + ev.name
    return ev.type


async def run_turn(agent, ctx: TurnCtx, input: Any, *, index: CityIndex, user_text: str,
                   run_config, max_turns: int = 6, hooks: StepHooks | None = None,
                   cancel_after_s: float | None = None, cancel_mode: str = "immediate") -> TurnLog:
    hooks = hooks or StepHooks()
    log = TurnLog()
    guard = ReplyGuard(index, user_text=user_text, today=date.today())
    seen_outcomes = 0
    buf = SentenceBuffer()
    started = time.monotonic()
    result = Runner.run_streamed(agent, input, context=ctx, max_turns=max_turns, hooks=hooks,
                                 run_config=run_config)
    log.result = result

    def sync_outcomes() -> None:
        nonlocal seen_outcomes
        for call_id, outcome in ctx.outcomes[seen_outcomes:]:
            if outcome.code != "tool_budget_exceeded":
                guard.add_record(outcome.payload)
            for card in outcome.cards:
                log.events.append(("card", card.get("type") if isinstance(card, dict) else card))
        seen_outcomes = len(ctx.outcomes)

    def release(piece: str) -> bool:
        sync_outcomes()
        problems = guard.check(log.released + piece)
        if problems:
            log.guard_problems = problems
            return False
        if log.first_release_s is None:
            log.first_release_s = time.monotonic() - started
        log.released += piece
        log.events.append(("delta", piece))
        return True

    try:
        async for ev in result.stream_events():
            k = _kind(ev)
            if not log.sdk or log.sdk[-1] != k:
                log.sdk.append(k)
            if cancel_after_s is not None and time.monotonic() - started > cancel_after_s:
                result.cancel(mode=cancel_mode)
                cancel_after_s = None
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                if log.first_delta_s is None:
                    log.first_delta_s = time.monotonic() - started
                log.text += ev.data.delta
                for sentence in buf.push(ev.data.delta):
                    if sentence.strip() and not release(sentence):
                        result.cancel()  # stop the model now; closes the upstream stream
                        break
            elif ev.type == "run_item_stream_event":
                if ev.name == "tool_called":
                    raw = ev.item.raw_item
                    log.tool_calls.append({"name": raw.name, "arguments": raw.arguments, "call_id": raw.call_id,
                                           "llm_call": len(hooks.llm_starts)})
                    log.events.append(("status", raw.name))
                elif ev.name == "tool_output":
                    sync_outcomes()
                elif ev.name == "reasoning_item_created":
                    log.reasoning_items += 1
                    log.reasoning_chars += sum(len(s.text) for s in (ev.item.raw_item.summary or []))
                elif ev.name == "message_output_created":
                    # a later LLM step starts a new paragraph: flush the rest of this step's text
                    rest = buf.flush()
                    if rest.strip() and log.guard_problems is None and not release(rest):
                        result.cancel()
            if log.guard_problems is not None:
                break
    except Exception as exc:  # noqa: BLE001 - recorded as evidence
        log.error = f"{type(exc).__name__}: {exc}"[:300]
    rest = buf.flush()
    if rest.strip() and log.guard_problems is None and log.error is None:
        release(rest)
    if log.guard_problems is not None:
        log.events.append(("replace", "template"))
    sync_outcomes()
    log.seconds = time.monotonic() - started
    log.usage = result.context_wrapper.usage
    log.raw_usage = [r for _, _, r in hooks.llm_ends]
    log.llm_calls = len(hooks.llm_starts)
    return log
