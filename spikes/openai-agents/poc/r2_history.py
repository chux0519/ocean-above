"""Check 15 on the real DeepSeek endpoint: history rebuilt from app.db (no SDK Session) as SDK input.

Turn 1 runs on today's backend loop (`app.agent.Agent` + `OpenAIChatClient`), which stores the user
message, tool calls, LLM calls (with reasoning) and the assistant message in app.db.
Turn 2: the user message is stored, `build_context()` rebuilds the chat history exactly as today,
`chat_to_items()` (our ~25-line adapter) turns it into SDK input items, and the SDK runs the turn.
Run: cd spikes/openai-agents/poc && ../.venv/bin/python r2_history.py
"""
from __future__ import annotations

import asyncio
import json

from common import (Cost, HttpLog, Toolbox, TurnCtx, World, chat_to_items, deepseek_settings, make_agent,
                    make_model, p, quiet_tracing, real_config, record_cost, run_config)
from turn import run_turn

from app.agent import Agent as LegacyAgent, OpenAIChatClient
from app.agent.context import build_context
from app.agent.guard import CityIndex


async def main() -> None:
    quiet_tracing()
    cfg = real_config()
    world = World("r2")
    cost = Cost()
    try:
        # turn 1 on the current loop
        legacy_llm = OpenAIChatClient(cfg)
        legacy = LegacyAgent(world.airline, legacy_llm)
        events = []
        async for ev in legacy.stream_turn(world.user, world.conv, "Quote two routes for me: Paris to Tokyo, and London to Sydney."):
            events.append(ev.name)
        await legacy_llm.aclose()
        for call in world.store.list_llm_calls(world.user, world.conv):
            cost.add_raw((call["response"] or {}).get("usage"))
        p(f"turn 1 (current loop) events: {events}")
        p(f"turn 1 llm_calls in app.db: {[(len((c['response'] or {}).get('reasoning_content') or ''), len((c['response'] or {}).get('tool_calls') or [])) for c in world.store.list_llm_calls(world.user, world.conv)]} (reasoning chars, tool calls)")

        # turn 2 on the SDK, history from app.db
        text2 = "Great, please book the London one."
        world.store.add_message(world.user, world.conv, "user", text2)
        context = build_context(world.store, world.user, world.conv)
        items = chat_to_items(context.messages)
        p(f"\nbuild_context -> {len(context.messages)} chat messages: "
          f"{[(m['role'], bool(m.get('reasoning_content')), len(m.get('tool_calls') or [])) for m in context.messages]}")
        p(f"chat_to_items -> {len(items)} SDK items: {[i.get('type', i.get('role')) for i in items]}")

        http = HttpLog()
        model, client = make_model(cfg.base_url, cfg.api_key, cfg.model, http=http)
        agent = make_agent(model, deepseek_settings("high"))
        ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline))
        index = CityIndex(world.networks.current().cities)
        log = await run_turn(agent, ctx, items, index=index, user_text=text2, run_config=run_config())
        for raw in log.raw_usage:
            cost.add_raw(raw)
        first = http.bodies[0]["messages"]
        p(f"\nSDK request 1 messages: {[(m['role'], 'reasoning_content' in m, len(m.get('tool_calls') or [])) for m in first]}")
        hist_ids = [c["id"] for m in context.messages for c in (m.get("tool_calls") or [])]
        sent_ids = [c["id"] for m in first for c in (m.get("tool_calls") or [])]
        p(f"tool_call ids from app.db preserved: {hist_ids == sent_ids} ({sent_ids})")
        rc_hist = [m.get("reasoning_content") for m in context.messages if m["role"] == "assistant" and m.get("reasoning_content")]
        rc_sent = [m.get("reasoning_content") for m in first if m["role"] == "assistant" and m.get("reasoning_content")]
        p(f"reasoning_content passed back verbatim: {rc_hist == rc_sent} ({len(rc_sent)} assistant message(s))")
        p(f"turn 2 tool calls: {[(c['name'], c['arguments']) for c in log.tool_calls]}")
        p(f"turn 2 outcomes: {[(o.name, o.code) for _, o in ctx.outcomes]}; cards: {[e for e in log.events if e[0] == 'card']}")
        p(f"turn 2 reply: {log.released!r}  guard: {log.guard_problems or 'ok'}")
        if log.error:
            p(f"ERROR: {log.error}")
        await client.close()
    finally:
        world.close()
        record_cost("r2_history", cost)
        p(f"\ncost this run: ${cost.usd:.5f} (hit {cost.hit}, miss {cost.miss}, out {cost.out})")


if __name__ == "__main__":
    asyncio.run(main())
