"""Checks 3, 8, 9, 10 and 13 against the local fake OpenAI-compatible server.

Run: cd spikes/openai-agents/poc && ../.venv/bin/python f_errors.py
"""
from __future__ import annotations

import asyncio
import json
import time

from agents import FunctionTool, MaxTurnsExceeded, ModelRetrySettings, retry_policies
from openai.types.responses import ResponseTextDeltaEvent

from common import (HttpLog, Runner, StepHooks, Toolbox, TurnCtx, World, deepseek_settings, make_agent,
                    make_model, make_tools, p, quiet_tracing, run_config)
from fake_server import USAGE, FakeServer, cut_after, hang, http_error, text, tool_calls

SECRET_KEY = "sk-fake-DO-NOT-LEAK-1234567890"


def setup(server, world, settings=None, tools=None, **ctx_kw):
    http = HttpLog()
    model, client = make_model(server.base_url, SECRET_KEY, "deepseek-flash", http=http, trust_env=False)
    agent = make_agent(model, settings or deepseek_settings("high"), tools=tools)
    ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline), **ctx_kw)
    return agent, ctx, http, client


async def consume(result, hooks=None, on_event=None):
    deltas, items, err = [], [], None
    try:
        async for ev in result.stream_events():
            if on_event:
                on_event(ev)
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                deltas.append(ev.data.delta)
            elif ev.type == "run_item_stream_event":
                items.append(ev.name)
    except Exception as exc:  # noqa: BLE001
        err = exc
    return deltas, items, err


def errtext(err) -> str:
    if err is None:
        return "none"
    s = f"{type(err).__module__}.{type(err).__name__}: {str(err)[:160]}"
    return s + ("  [KEY LEAKED]" if SECRET_KEY in repr(err) or SECRET_KEY in str(err) else "  [key not in message]")


async def malformed(world: World) -> None:
    p("\n=== 3/8 malformed and unknown tool calls (arguments streamed in 2-char fragments)")
    cases = [
        ("fragments reassembled", [("quote_route", '{"origin":"Paris","destination":"Tokyo"}')]),
        ("invalid JSON", [("quote_route", '{"origin": "Paris", "destination":')]),
        ("JSON not an object", [("find_city", '["Paris"]')]),
        ("empty arguments", [("list_my_tickets", "")]),
        ("unknown argument (user_id)", [("list_my_tickets", '{"user_id":"u_someone_else"}')]),
        ("unknown tool", [("book_ticket", '{"quote_id":"q_1"}')]),
        ("invalid tool name", [("bad name!", "{}")]),
        ("three calls, one completion", [("find_city", '{"name":"Paris"}'), ("find_city", '{"name":"Tokio"}'),
                                         ("find_city", '{"name":"Abu Dhabi"}')]),
    ]
    for label, calls in cases:
        async with FakeServer(scripts=[tool_calls(calls, split=2), text("done.")]) as server:
            agent, ctx, http, client = setup(server, world)
            result = Runner.run_streamed(agent, "x", context=ctx, run_config=run_config())
            deltas, items, err = await consume(result)
            outs = [(o.name, o.code, json.dumps(o.payload)[:90]) for _, o in ctx.outcomes]
            # what the model is told in the next request
            told = []
            if len(server.requests) > 1:
                told = [m["content"][:110] for m in server.requests[1].body["messages"] if m["role"] == "tool"]
                sent_calls = [(c["function"]["name"], c["function"]["arguments"])
                              for m in server.requests[1].body["messages"] if m["role"] == "assistant"
                              for c in m.get("tool_calls") or []]
            else:
                sent_calls = []
            p(f"  [{label}] error={errtext(err) if err else 'none'}; requests={len(server.requests)}")
            p(f"      our Toolbox outcomes: {outs}")
            p(f"      tool calls echoed back to the model: {sent_calls}")
            p(f"      tool results the model got: {told}")
            await client.close()


async def raising_tool(world: World) -> None:
    p("\n=== 3b a FunctionTool whose on_invoke_tool raises")

    async def boom(tc, raw):
        raise RuntimeError("database exploded: secret detail")

    tools = make_tools() + [FunctionTool(name="boom", description="x", params_json_schema={"type": "object", "properties": {}},
                                         on_invoke_tool=boom, strict_json_schema=False)]
    async with FakeServer(scripts=[tool_calls([("boom", "{}")]), text("done.")]) as server:
        agent, ctx, http, client = setup(server, world, tools=tools)
        result = Runner.run_streamed(agent, "x", context=ctx, run_config=run_config())
        deltas, items, err = await consume(result)
        told = [m["content"] for m in server.requests[1].body["messages"] if m["role"] == "tool"] if len(server.requests) > 1 else []
        p(f"  error={errtext(err)}; requests={len(server.requests)}; model was told: {told}")
        await client.close()


async def errors_and_retries(world: World) -> None:
    p("\n=== 10 HTTP errors, timeouts, cut streams; openai client retries off, runner retry policy on")
    policy = retry_policies.any(retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
                                retry_policies.network_error())
    retry = ModelRetrySettings(max_retries=2, backoff={"initial_delay": 0.05, "multiplier": 2, "jitter": False},
                               policy=policy)
    cases = [
        ("500 twice then ok", [http_error(500), http_error(500), text("ok then.")], {}),
        ("500 x3 (retries exhausted)", [http_error(500), http_error(500), http_error(500)], {}),
        ("400 (not retried)", [http_error(400, "bad request")], {}),
        ("401 (not retried)", [http_error(401, "invalid key " + SECRET_KEY)], {}),
        ("429 then ok", [http_error(429), text("ok then.")], {}),
        ("hang -> per-attempt timeout 0.5 s", [hang(), hang(), hang()], {"timeout": 0.5}),
        ("stream cut before any text, then ok", [cut_after(0), text("ok then.")], {}),
        ("stream cut after 3 words (text already streamed)", [cut_after(3), text("ok then.")], {}),
        ("no usage chunk", [text("fine.", usage=None)], {}),
    ]
    for label, scripts, extra in cases:
        async with FakeServer(scripts=list(scripts)) as server:
            settings = deepseek_settings("high", retry=retry, **extra)
            agent, ctx, http, client = setup(server, world, settings=settings)
            hooks = StepHooks()
            t0 = time.monotonic()
            result = Runner.run_streamed(agent, "x", context=ctx, hooks=hooks, run_config=run_config())
            deltas, items, err = await consume(result)
            u = result.context_wrapper.usage
            p(f"  [{label}] requests={len(server.requests)} in {time.monotonic() - t0:.2f}s; deltas={''.join(deltas)!r};"
              f" final={result.final_output!r}")
            p(f"      error: {errtext(err)}")
            p(f"      usage: requests={u.requests} total={u.total_tokens}; raw per call={[r for _, _, r in hooks.llm_ends]}")
            await client.close()


async def budgets(world: World) -> None:
    p("\n=== 13 budgets")
    # max steps: max_turns
    def loop_scripts():
        return [tool_calls([("find_city", '{"name":"Paris"}')]) for _ in range(5)]

    async with FakeServer(scripts=loop_scripts()) as server:
        agent, ctx, http, client = setup(server, world)
        result = Runner.run_streamed(agent, "x", context=ctx, max_turns=3, run_config=run_config())
        deltas, items, err = await consume(result)
        p(f"  max_turns=3 with a model that always calls a tool: requests={len(server.requests)}; tools run={len(ctx.outcomes)};"
          f" error={type(err).__name__ if err else None} (MaxTurnsExceeded: {isinstance(err, MaxTurnsExceeded)})")
        await client.close()

    async with FakeServer(scripts=loop_scripts()) as server:
        agent, ctx, http, client = setup(server, world)
        handled = []

        def on_max_turns(data):
            handled.append(type(data.error).__name__)
            return "TEMPLATE REPLY (max_steps)"  # our code template would go here

        result = Runner.run_streamed(agent, "x", context=ctx, max_turns=3, run_config=run_config(),
                                     error_handlers={"max_turns": on_max_turns})
        deltas, items, err = await consume(result)
        p(f"  same with error_handlers['max_turns']: final_output={result.final_output!r}; error={err}; handler saw {handled}")
        await client.close()

    # per-step and per-turn tool caps (our adapter in on_invoke_tool, counter reset in on_llm_start)
    many = [("find_city", json.dumps({"name": n})) for n in ["Paris", "Rome", "Lima", "Oslo", "Cairo", "Dakar"]]
    async with FakeServer(scripts=[tool_calls(many), text("done.")]) as server:
        agent, ctx, http, client = setup(server, world)
        result = Runner.run_streamed(agent, "x", context=ctx, hooks=StepHooks(), run_config=run_config())
        deltas, items, err = await consume(result)
        told = [json.loads(m["content"]).get("code") for m in server.requests[1].body["messages"] if m["role"] == "tool"]
        p(f"  6 calls in one completion, per-step cap 4: executed={len(ctx.outcomes)} refused={ctx.refused};"
          f" every call answered: {told}")
        await client.close()

    # token soft cap: on_llm_end sees usage; over the cap -> cancel(after_turn): this step's tools run, no next LLM call
    class TokenCap(StepHooks):
        def __init__(self, cap: int):
            super().__init__()
            self.cap, self.tokens, self.result, self.stopped = cap, 0, None, False

        async def on_llm_end(self, context, agent, response) -> None:
            await super().on_llm_end(context, agent, response)
            self.tokens += response.usage.total_tokens
            if self.tokens >= self.cap and self.result is not None:
                self.stopped = True
                self.result.cancel(mode="after_turn")

    big = dict(USAGE, total_tokens=70000, prompt_tokens=69980)
    async with FakeServer(scripts=[tool_calls([("find_city", '{"name":"Paris"}')], usage=big), text("never")]) as server:
        agent, ctx, http, client = setup(server, world)
        hooks = TokenCap(60000)
        result = Runner.run_streamed(agent, "x", context=ctx, hooks=hooks, run_config=run_config())
        hooks.result = result
        deltas, items, err = await consume(result)
        p(f"  token soft cap 60000, first call reports 70000: stopped={hooks.stopped}; tools run={len(ctx.outcomes)};"
          f" requests={len(server.requests)}; final_output={result.final_output!r} -> our template reply")
        await client.close()

    # tool flood: more than 4x the per-step cap in one completion -> cancel before any tool runs
    flood = [("find_city", '{"name":"Paris"}')] * 20

    class Flood(StepHooks):
        result = None

        async def on_llm_end(self, context, agent, response) -> None:
            await super().on_llm_end(context, agent, response)
            calls = [o for o in response.output if getattr(o, "type", "") == "function_call"]
            if len(calls) > 16 and self.result is not None:
                self.result.cancel()

    async with FakeServer(scripts=[tool_calls(flood), text("never")]) as server:
        agent, ctx, http, client = setup(server, world)
        hooks = Flood()
        result = Runner.run_streamed(agent, "x", context=ctx, hooks=hooks, run_config=run_config())
        hooks.result = result
        deltas, items, err = await consume(result)
        await asyncio.sleep(0.2)
        p(f"  flood of 20 calls, cancel('immediate') from on_llm_end: tools run={len(ctx.outcomes)}; items={items};"
          f" requests={len(server.requests)}")
        await client.close()

    # whole-turn time limit: our asyncio deadline around the consumer, then cancel()
    from fake_server import forever
    async with FakeServer(scripts=[forever(0.01)]) as server:
        agent, ctx, http, client = setup(server, world)
        result = Runner.run_streamed(agent, "x", context=ctx, run_config=run_config())
        t0 = time.monotonic()
        try:
            async with asyncio.timeout(0.5):
                await consume(result)
        except TimeoutError:
            result.cancel()
        t1 = time.monotonic()
        for _ in range(300):
            if server.requests[0].closed_at:
                break
            await asyncio.sleep(0.01)
        closed = server.requests[0].closed_at
        p(f"  turn deadline 0.5 s on an endless stream: stopped after {t1 - t0:.2f}s;"
          f" upstream closed {((closed or t1) - t1) * 1000:.1f} ms later; requests={len(server.requests)}")
        await client.close()


async def main() -> None:
    quiet_tracing()
    world = World("ferr")
    try:
        await malformed(world)
        await raising_tool(world)
        await errors_and_retries(world)
        await budgets(world)
    finally:
        world.close()


if __name__ == "__main__":
    asyncio.run(main())
