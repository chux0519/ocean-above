"""Checks 4, 5, 6 and 9 (usage on cancel) against the local fake OpenAI-compatible server.

The server sees the TCP connection itself, so "upstream closed" here means the client's FIN/RST
reached the server - not merely that we stopped reading.
Run: cd spikes/openai-agents/poc && ../.venv/bin/python f_cancel.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time

from openai.types.responses import ResponseTextDeltaEvent

from common import (HttpLog, Runner, StepHooks, Toolbox, TurnCtx, World, deepseek_settings, make_agent,
                    make_model, p, quiet_tracing, run_config)
from fake_server import FakeServer, forever, text, tool_calls
from turn import run_turn

from app.agent.guard import CityIndex


def setup(server: FakeServer, world: World, **ctx_kw):
    http = HttpLog()
    model, client = make_model(server.base_url, "sk-fake", "deepseek-flash", http=http, trust_env=False)
    agent = make_agent(model, deepseek_settings("high"))
    ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline), **ctx_kw)
    return agent, ctx, http, client


async def wait_closed(req, limit: float = 3.0) -> float | None:
    end = time.monotonic() + limit
    while req.closed_at is None and time.monotonic() < end:
        await asyncio.sleep(0.001)
    return req.closed_at


async def immediate_during_text(world: World, n: int = 9) -> None:
    p("\n=== 6/4a cancel('immediate') while the model streams text (infinite stream), n =", n)
    lat_close, lat_stream_end, after = [], [], []
    for i in range(n):
        async with FakeServer(scripts=[forever(0.01)]) as server:
            agent, ctx, http, client = setup(server, world)
            hooks = StepHooks()
            result = Runner.run_streamed(agent, "hi", context=ctx, hooks=hooks, run_config=run_config())
            deltas = 0
            t0 = None
            async for ev in result.stream_events():
                if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                    deltas += 1
                    if deltas == 10 and t0 is None:
                        t0 = time.monotonic()
                        result.cancel()
            t_end = time.monotonic()
            req = server.requests[0]
            closed = await wait_closed(req)
            sent_at_cancel = req.chunks_sent
            await asyncio.sleep(0.3)  # anything else happening afterwards?
            lat_stream_end.append((t_end - t0) * 1000)
            lat_close.append((closed - t0) * 1000 if closed else float("nan"))
            after.append((len(server.requests), len(hooks.llm_ends), result.context_wrapper.usage.requests,
                          result.context_wrapper.usage.total_tokens, result.is_complete, sent_at_cancel))
            await client.close()
    p(f"  cancel -> stream_events() ends (ms): {[round(x, 1) for x in lat_stream_end]} median {statistics.median(lat_stream_end):.1f}")
    p(f"  cancel -> server sees TCP close (ms): {[round(x, 1) for x in lat_close]} median {statistics.median(lat_close):.1f}")
    p(f"  per run (requests to server, on_llm_end calls, usage.requests, usage.total_tokens, is_complete, chunks server wrote): {after}")


async def immediate_during_tool_args(world: World) -> None:
    p("\n=== 6/4b cancel('immediate') while tool-call arguments are still streaming")
    async with FakeServer(scripts=[tool_calls([("quote_route", '{"origin":"Paris","destination":"Tokyo"}')],
                                              split=2, interval=0.05, then_hang=True), text("never requested")]) as server:
        agent, ctx, http, client = setup(server, world)
        result = Runner.run_streamed(agent, "Paris to Tokyo", context=ctx, run_config=run_config())
        kinds, t0 = [], None
        async for ev in result.stream_events():
            if ev.type == "raw_response_event":
                kinds.append(ev.data.type)
                if ev.data.type == "response.function_call_arguments.delta" and t0 is None:
                    t0 = time.monotonic()
                    result.cancel()
        closed = await wait_closed(server.requests[0])
        await asyncio.sleep(0.5)
        p(f"  raw events seen before cancel took effect: {sorted(set(kinds))}")
        p(f"  close latency {((closed or float('nan')) - t0) * 1000:.1f} ms; requests {len(server.requests)};"
          f" tools executed {len(ctx.outcomes)}; threads started {len(ctx.thread_done)}")
        await client.close()


async def after_turn_during_tool_step(world: World) -> None:
    p("\n=== 4c cancel('after_turn') while the model streams a tool call")
    async with FakeServer(scripts=[tool_calls([("quote_route", '{"origin":"Paris","destination":"Tokyo"}')],
                                              split=3, interval=0.03), text("never requested")]) as server:
        agent, ctx, http, client = setup(server, world)
        hooks = StepHooks()
        result = Runner.run_streamed(agent, "Paris to Tokyo", context=ctx, hooks=hooks, run_config=run_config())
        names = []
        async for ev in result.stream_events():
            if ev.type == "raw_response_event" and ev.data.type == "response.function_call_arguments.delta" and not names:
                names.append("cancel(after_turn)")
                result.cancel(mode="after_turn")
            if ev.type == "run_item_stream_event":
                names.append(ev.name)
        await asyncio.sleep(0.3)
        p(f"  item events: {names}")
        p(f"  requests {len(server.requests)} (2nd scripted reply never requested: {len(server.requests) == 1});"
          f" tools executed {[(o.name, o.code) for _, o in ctx.outcomes]}; final_output={result.final_output!r};"
          f" usage.requests={result.context_wrapper.usage.requests} total_tokens={result.context_wrapper.usage.total_tokens}")
        p(f"  new_items: {[type(i).__name__ for i in result.new_items]}  (tool call + its output kept; history stays well-formed)")
        await client.close()


async def after_turn_during_text(world: World) -> None:
    p("\n=== 4d cancel('after_turn') while the model streams its final text")
    async with FakeServer(scripts=[text("one two three four five six seven eight", interval=0.02)]) as server:
        agent, ctx, http, client = setup(server, world)
        result = Runner.run_streamed(agent, "hi", context=ctx, run_config=run_config())
        n = 0
        async for ev in result.stream_events():
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                n += 1
                if n == 2:
                    result.cancel(mode="after_turn")
        p(f"  deltas received {n}; final_output={result.final_output!r}; requests {len(server.requests)};"
          f" server finished the response itself: {server.requests[0].finished}")
        await client.close()


async def cancel_during_thread(world: World) -> None:
    p("\n=== 5 cancel('immediate') while a tool runs in a worker thread (tool sleeps 1.5 s, then writes the quote)")
    async with FakeServer(scripts=[tool_calls([("quote_route", '{"origin":"Paris","destination":"Tokyo"}')]),
                                   text("never requested")]) as server:
        agent, ctx, http, client = setup(server, world, tool_delay=1.5)
        result = Runner.run_streamed(agent, "Paris to Tokyo", context=ctx, run_config=run_config())
        t_called = t_cancel = None
        names = []

        async def canceller():
            nonlocal t_cancel
            while t_called is None:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.3)
            t_cancel = time.monotonic()
            result.cancel()

        task = asyncio.ensure_future(canceller())
        async for ev in result.stream_events():
            if ev.type == "run_item_stream_event":
                names.append(ev.name)
                if ev.name == "tool_called":
                    t_called = time.monotonic()
        t_end = time.monotonic()
        await task
        running_at_end = ctx.threads.running
        before = len(world.store.list_messages(world.user, world.conv))
        t_drain = time.monotonic()
        drained = await ctx.threads.drain(5)
        t_drained = time.monotonic()
        await asyncio.sleep(0.3)
        quotes = world.store_quotes() if hasattr(world, "store_quotes") else None
        p(f"  item events: {names}")
        p(f"  stream_events() ended {(t_end - t_cancel) * 1000:.1f} ms after cancel; worker threads still running then: {running_at_end}")
        p(f"  Threads.drain(): {drained} after {(t_drained - t_drain) * 1000:.0f} ms; thread finished its work: {ctx.thread_done and ctx.thread_done[0][0]}")
        p(f"  outcome reached the SDK: {len(ctx.outcomes)} (the awaiting coroutine was cancelled; the thread was not)")
        p(f"  requests to model: {len(server.requests)} (no LLM call after cancel); tool_output event: {'tool_output' in names}")
        await client.close()


async def guard_failure_closes_stream(world: World) -> None:
    p("\n=== 12/6 ReplyGuard refuses a sentence -> result.cancel() -> upstream closed, nothing unchecked emitted")
    script = text("Sure. The flight from Paris to Tokyo costs $123.45 today. More text follows here and keeps "
                  "going for a while so the stream is still open when the guard fires. " * 3, interval=0.02)
    async with FakeServer(scripts=[script]) as server:
        agent, ctx, http, client = setup(server, world)
        index = CityIndex(world.networks.current().cities)
        log = await run_turn(agent, ctx, "hello", index=index, user_text="hello", run_config=run_config())
        closed = await wait_closed(server.requests[0])
        p(f"  our events: {log.events}")
        p(f"  guard problems: {json.dumps(log.guard_problems)[:300]}")
        p(f"  text received {len(log.text)} chars; released {len(log.released)} chars: {log.released!r}")
        p(f"  server saw close: {closed is not None}; server finished response itself: {server.requests[0].finished};"
          f" chunks written {server.requests[0].chunks_sent}")
        await client.close()


async def main() -> None:
    quiet_tracing()
    world = World("fcancel")
    try:
        await immediate_during_text(world)
        await immediate_during_tool_args(world)
        await after_turn_during_tool_step(world)
        await after_turn_during_text(world)
        await cancel_during_thread(world)
        await guard_failure_closes_stream(world)
    finally:
        world.close()


if __name__ == "__main__":
    asyncio.run(main())
