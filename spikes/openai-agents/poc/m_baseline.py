"""Measurements that need a baseline from today's client (`app.agent.llm.OpenAIChatClient`).

1. First text delta on real DeepSeek, same prompt / system prompt / tools / effort, current client vs SDK
   (interleaved, n runs each).
2. Close-to-upstream latency on the fake server: current client (`aclosing` the generator) vs SDK
   `result.cancel()`.
3. Per-delta overhead: a 2000-chunk fake stream consumed by both (CPU time per chunk).
Run: cd spikes/openai-agents/poc && ../.venv/bin/python m_baseline.py [n]
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from datetime import date

from openai.types.responses import ResponseTextDeltaEvent

from common import (Cost, Runner, Toolbox, TurnCtx, World, deepseek_settings, make_agent, make_model, p,
                    quiet_tracing, real_config, record_cost, run_config)
from fake_server import FakeServer, forever, text

from app.agent.config import LLMConfig
from app.agent.llm import OpenAIChatClient, TextDelta, Completion
from app.agent.prompt import system_prompt
from app.agent.tools import TOOL_SCHEMAS

PROMPT = "Hi! In one short sentence, what can you help me with?"


async def legacy_first_delta(cfg, cost: Cost) -> float:
    llm = OpenAIChatClient(cfg)
    msgs = [{"role": "system", "content": system_prompt(date.today())}, {"role": "user", "content": PROMPT}]
    t0 = time.monotonic()
    first = None
    async for ev in llm.stream(msgs, TOOL_SCHEMAS):
        if isinstance(ev, TextDelta) and first is None:
            first = time.monotonic() - t0
        if isinstance(ev, Completion):
            cost.add_raw(ev.usage)
    await llm.aclose()
    return first


async def sdk_first_delta(cfg, world, cost: Cost) -> float:
    model, client = make_model(cfg.base_url, cfg.api_key, cfg.model)
    agent = make_agent(model, deepseek_settings(cfg.reasoning_effort))
    ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline))
    from common import StepHooks
    hooks = StepHooks()
    t0 = time.monotonic()
    first = None
    result = Runner.run_streamed(agent, PROMPT, context=ctx, hooks=hooks, run_config=run_config())
    async for ev in result.stream_events():
        if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent) and first is None:
            first = time.monotonic() - t0
    for _, _, raw in hooks.llm_ends:
        cost.add_raw(raw)
    await client.close()
    return first


async def legacy_close_precise(n: int) -> list[float]:
    """Same, timing from the moment we decide to stop (before aclose)."""
    import httpx
    out = []
    for _ in range(n):
        async with FakeServer(scripts=[forever(0.01)]) as server:
            cfg = LLMConfig(api_key="sk-fake", base_url=server.base_url, model="deepseek-flash")
            llm = OpenAIChatClient(cfg)
            llm._client = httpx.AsyncClient(trust_env=False, timeout=llm._timeout)
            gen = llm.stream([{"role": "user", "content": "x"}], TOOL_SCHEMAS)
            k = 0
            async for ev in gen:
                k += 1
                if k == 10:
                    break
            t0 = time.monotonic()
            await gen.aclose()
            req = server.requests[0]
            for _ in range(3000):
                if req.closed_at:
                    break
                await asyncio.sleep(0.0005)
            out.append((req.closed_at - t0) * 1000 if req.closed_at else float("nan"))
            await llm.aclose()
    return out


async def per_chunk(world) -> None:
    import httpx
    n = 2000
    words = " ".join(f"w{i}" for i in range(n))
    async with FakeServer(scripts=[text(words), text(words)]) as server:
        cfg = LLMConfig(api_key="sk-fake", base_url=server.base_url, model="deepseek-flash")
        llm = OpenAIChatClient(cfg)
        llm._client = httpx.AsyncClient(trust_env=False)
        c0, t0 = time.process_time(), time.monotonic()
        k = 0
        async for ev in llm.stream([{"role": "user", "content": "x"}], TOOL_SCHEMAS):
            k += isinstance(ev, TextDelta)
        c1, t1 = time.process_time(), time.monotonic()
        await llm.aclose()
        model, client = make_model(server.base_url, "sk-fake", "deepseek-flash", trust_env=False)
        agent = make_agent(model, deepseek_settings("high"))
        ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline))
        c2, t2 = time.process_time(), time.monotonic()
        m = 0
        result = Runner.run_streamed(agent, "x", context=ctx, run_config=run_config())
        async for ev in result.stream_events():
            m += ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent)
        c3, t3 = time.process_time(), time.monotonic()
        await client.close()
    p(f"\n3 per-chunk CPU (2000-chunk stream, client+server in one process): current client {(c1 - c0) / k * 1e6:.0f} µs/chunk"
      f" ({k} deltas, {t1 - t0:.2f}s wall); SDK {(c3 - c2) / m * 1e6:.0f} µs/chunk ({m} deltas, {t3 - t2:.2f}s wall)")


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    quiet_tracing()
    cfg = real_config()
    world = World("mbase")
    cost = Cost()
    try:
        leg, sdk = [], []
        for _ in range(n):
            leg.append(await legacy_first_delta(cfg, cost))
            sdk.append(await sdk_first_delta(cfg, world, cost))
        p(f"1 first text delta, real {cfg.model} effort {cfg.reasoning_effort}, n={n} each (interleaved):")
        p(f"  current client: {[round(x, 2) for x in leg]} median {statistics.median(leg):.2f} s")
        p(f"  SDK           : {[round(x, 2) for x in sdk]} median {statistics.median(sdk):.2f} s")
        lc = await legacy_close_precise(9)
        p(f"\n2 stop -> server sees TCP close, current client (generator aclose), n=9: {[round(x, 1) for x in lc]}"
          f" median {statistics.median(lc):.1f} ms   (SDK result.cancel(): see f_cancel.txt)")
        await per_chunk(world)
    finally:
        world.close()
        record_cost("m_baseline", cost)
        p(f"\ncost this run: ${cost.usd:.5f}")


if __name__ == "__main__":
    asyncio.run(main())
