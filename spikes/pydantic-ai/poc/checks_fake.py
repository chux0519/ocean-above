"""Deterministic checks against the local fake OpenAI-compatible server (no cost):
3 tool errors / bad args / ModelRetry, 4 cancellation, 5 cancellation during a sync tool,
6 upstream stream closed + no new LLM requests / tools, 8 incremental and malformed tool-call
arguments, 9 usage (normal, cancelled) and UsageLimits, 10 retries / timeouts / errors,
11 tracing (outbound audit, OpenTelemetry SDK without logfire), 12 ReplyGuard before output,
13 budgets, 14 trusted deps.

    PYDANTIC_AI_NO_BANNER=1 uv run python poc/checks_fake.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import threading
import time
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from common import (Deps, World, backend_tools, deps_for, dump, make_agent, make_model, settings,  # noqa: E402
                    SocketAudit)
AUDIT = SocketAudit()  # before any client exists

from fake_server import FakeServer  # noqa: E402
from pydantic_ai import (Agent, CancellationToken, ModelRetry, RunCancelled, RunContext,  # noqa: E402
                         RunTimedOut, Tool, UsageLimitExceeded, UsageLimits)
from pydantic_ai.exceptions import (ModelHTTPError, ModelRequestTimeout,  # noqa: E402
                                    UnexpectedModelBehavior)
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta  # noqa: E402
from pydantic_ai.tool_manager import ToolManager  # noqa: E402

SRV = FakeServer().start()
OUT: dict[str, Any] = {}


def model(**kw: Any):
    return make_model(SRV.base_url, **kw)


def requests_by(scenario: str) -> int:
    return sum(1 for r in SRV.requests if r["scenario"] == scenario)


async def drive(run, on_text=None, on_tool_event=None) -> None:
    """Our driver: iterate the graph, stream model output and tool events."""
    async for node in run:
        if Agent.is_model_request_node(node):
            async with node.stream(run.ctx) as stream:
                async for ev in stream:
                    if on_text and isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                        await on_text(ev.part.content)
                    elif on_text and isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                        await on_text(ev.delta.content_delta)
        elif Agent.is_call_tools_node(node):
            async with node.stream(run.ctx) as stream:
                async for ev in stream:
                    if on_tool_event:
                        on_tool_event(ev)


def err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


# ------------------------------------------------------------------ 3 / 8 tool errors -------
def boom(x: str) -> str:
    raise RuntimeError("tool crashed")


PICKY_CALLS = {"n": 0}


def picky(x: str) -> str:
    PICKY_CALLS["n"] += 1
    if PICKY_CALLS["n"] == 1:
        raise ModelRetry("x must be a city name, try again")
    return "ok"


async def check_tool_errors() -> dict:
    w = World.create("errors")
    res: dict[str, Any] = {}
    extra = [Tool(boom), Tool(picky)]
    for scenario in ("badargs", "unknown_tool", "malformed", "malformed_always", "raise", "modelretry"):
        SRV.reset()
        PICKY_CALLS["n"] = 0
        agent = make_agent(model(), tools=backend_tools() + extra)
        deps = deps_for(w)
        try:
            r = await agent.run(f"go [[{scenario}]]", deps=deps, model_settings=settings())
            outcome = f"ok: {r.output!r}"
            retry_parts = [p.model_dump(mode="json") if hasattr(p, "model_dump") else repr(p)
                           for m in r.all_messages() for p in getattr(m, "parts", [])
                           if type(p).__name__ == "RetryPromptPart"]
        except Exception as exc:
            outcome, retry_parts = err(exc), []
        res[scenario] = {"outcome": outcome, "llm_requests": len(SRV.requests),
                         "backend_tool_codes": [o.code for o in deps.outcomes],
                         "tool_messages_in_last_request": [str(m.get("content"))[:200] for m in SRV.requests[-1]["body"]["messages"]
                                                           if m.get("role") == "tool"]}
    # retries knob: malformed_always with tool retries=3
    SRV.reset()
    try:
        await make_agent(model(), retries=3).run("go [[malformed_always]]", deps=deps_for(w), model_settings=settings())
    except Exception as exc:
        res["malformed_always_retries3"] = {"outcome": err(exc), "llm_requests": len(SRV.requests)}
    return res


# ------------------------------------------------------------------ 4 / 6 cancellation ------
async def cancel_once(mode: str, delay: float = 0.3) -> dict:
    """Stream the infinite scenario, cancel `delay` s after the first text, measure until the
    fake server saw the connection closed."""
    SRV.reset()
    w = World.create("cancel")
    agent = make_agent(model())
    token = CancellationToken()
    first = asyncio.Event()
    texts: list[str] = []
    t_cancel: dict[str, float] = {}

    async def on_text(t: str) -> None:
        texts.append(t)
        first.set()

    async def body() -> str:
        async with agent.iter("stream forever [[infinite]]", deps=deps_for(w), model_settings=settings(),
                              cancellation_token=token) as run:
            holder["run"] = run
            if mode == "break":  # the consumer stops reading: guard failure path
                async for node in run:
                    if Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for ev in stream:
                                texts.append("x")
                                if len(texts) > 10:
                                    t_cancel["t"] = time.monotonic()
                                    break
                        break  # leave the run without finishing it
                return "left"
            await drive(run, on_text)
        return "finished"

    holder: dict[str, Any] = {}
    task = asyncio.create_task(body())
    if mode != "break":
        await asyncio.wait_for(first.wait(), 5)
        await asyncio.sleep(delay)
        t_cancel["t"] = time.monotonic()
        if mode == "run.cancel":
            holder["run"].cancel()
        elif mode == "token_thread":
            threading.Thread(target=token.cancel).start()
        elif mode == "task.cancel":
            task.cancel()
    try:
        outcome = await task
        exc_info: dict = {"outcome": outcome}
    except RunCancelled as exc:
        exc_info = {"outcome": "RunCancelled", "usage": repr(exc.usage),
                    "messages_kept": len(exc.all_messages())}
    except asyncio.CancelledError:
        exc_info = {"outcome": "CancelledError"}
    t_raised = time.monotonic()
    await asyncio.sleep(0.5)  # anything still going on would show up now
    conn = SRV.conns[0]
    return {"mode": mode, **exc_info, "chunks_before_close": conn.chunks,
            "upstream_closed_after_ms": None if conn.closed is None else round((conn.closed - t_cancel["t"]) * 1000, 1),
            "raised_after_ms": round((t_raised - t_cancel["t"]) * 1000, 1),
            "llm_requests_total": len(SRV.requests)}


async def run_stream_cancel() -> dict:
    """`agent.run_stream()` + `StreamedRunResult.cancel()` (stops the current response)."""
    SRV.reset()
    w = World.create("rs")
    agent = make_agent(model())
    async with agent.run_stream("x [[infinite]]", deps=deps_for(w), model_settings=settings()) as result:
        n = 0
        async for _ in result.stream_text(delta=True):
            n += 1
            if n == 10:
                t = time.monotonic()
                await result.cancel()
                break
    await asyncio.sleep(0.5)
    c = SRV.conns[0]
    return {"api": "run_stream + StreamedRunResult.cancel()", "deltas_read": n,
            "upstream_closed_after_ms": None if c.closed is None else round((c.closed - t) * 1000, 1),
            "llm_requests_total": len(SRV.requests)}


async def check_cancel() -> dict:
    out: dict[str, Any] = {}
    for mode in ("run.cancel", "token_thread", "task.cancel", "break"):
        runs = [await cancel_once(mode) for _ in range(7)]
        lat = [r["upstream_closed_after_ms"] for r in runs if r["upstream_closed_after_ms"] is not None]
        out[mode] = {"example": runs[0], "upstream_close_ms_median": statistics.median(lat) if lat else None,
                     "upstream_close_ms_all": lat, "closed_in_all_runs": len(lat) == len(runs),
                     "max_llm_requests": max(r["llm_requests_total"] for r in runs)}
    out["run_stream"] = await run_stream_cancel()
    return out


# ------------------------------------------------------------------ 5 sync tool + cancel ----
SLOW_LOG: list[dict] = []


def slow(seconds: float) -> str:
    """Blocking work (stands in for an app.db write)."""
    entry = {"start": time.monotonic(), "thread": threading.current_thread().name}
    SLOW_LOG.append(entry)
    time.sleep(seconds)
    entry["end"] = time.monotonic()
    return "slow done"


async def cancel_in_tool(scenario: str, mode: str, sequential: bool, tracked: bool = False) -> dict:
    """tracked=True: the adapter runs the sync work through the backend's `Threads` (async tool),
    and after the cancellation drains it before "releasing the lock" (design §4.9)."""
    from app.agent.concurrency import Threads
    SRV.reset()
    SLOW_LOG.clear()
    w = World.create("slow")
    deps = deps_for(w)
    threads = Threads()

    async def slow_tracked(seconds: float) -> str:
        return await threads.run(slow, seconds)

    tool = Tool(slow_tracked, name="slow") if tracked else Tool(slow)
    agent = make_agent(model(), tools=backend_tools() + [tool])
    holder: dict[str, Any] = {}

    async def body() -> None:
        async with agent.iter(f"x [[{scenario}]]", deps=deps, model_settings=settings()) as run:
            holder["run"] = run
            await drive(run)

    async def main() -> None:
        if sequential:
            with ToolManager.parallel_execution_mode("sequential"):
                await body()
        else:
            await body()

    task = asyncio.create_task(main())
    while not SLOW_LOG:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.3)
    t = time.monotonic()
    holder["run"].cancel() if mode == "run.cancel" else task.cancel()
    try:
        await task
        outcome = "finished"
    except RunCancelled:
        outcome = "RunCancelled"
    except asyncio.CancelledError:
        outcome = "CancelledError"
    t_ret = time.monotonic()
    drained = None
    if tracked:
        drained = await threads.drain(30)
        t_ret = time.monotonic()  # the moment the lock / slot would be released
    await asyncio.sleep(1.5)  # let any abandoned thread finish
    return {"scenario": scenario, "mode": mode, "sequential": sequential, "tracked_and_drained": drained,
            "outcome": outcome,
            "returned_after_cancel_ms": round((t_ret - t) * 1000),
            "threads": [{"ended_after_cancel_ms": None if "end" not in e else round((e["end"] - t) * 1000),
                         "returned_before_thread_ended": "end" in e and t_ret < e["end"]} for e in SLOW_LOG],
            "backend_tools_run": [o.name for o in deps.outcomes],
            "llm_requests_total": len(SRV.requests)}


async def check_cancel_in_tool() -> dict:
    return {"single_run_cancel": await cancel_in_tool("slow_tool", "run.cancel", False),
            "single_task_cancel": await cancel_in_tool("slow_tool", "task.cancel", False),
            "two_parallel": await cancel_in_tool("two_slow", "run.cancel", False),
            "slow_then_find_city_parallel": await cancel_in_tool("slow_then_more", "run.cancel", False),
            "slow_then_find_city_sequential": await cancel_in_tool("slow_then_more", "run.cancel", True),
            "adapter_Threads_then_drain": await cancel_in_tool("slow_tool", "run.cancel", False, tracked=True),
            "adapter_Threads_then_drain_sequential": await cancel_in_tool("slow_then_more", "run.cancel", True,
                                                                          tracked=True)}


# ------------------------------------------------------------------ 9 usage / limits --------
async def check_usage() -> dict:
    w = World.create("usage")
    out: dict[str, Any] = {}
    SRV.reset()
    r = await make_agent(model()).run("x [[tools2]]", deps=deps_for(w), model_settings=settings())
    out["normal"] = {"usage": repr(r.usage), "per_response": [repr(m.usage) for m in r.all_messages()
                                                              if type(m).__name__ == "ModelResponse"]}
    c = await cancel_once("run.cancel")
    out["cancelled_mid_stream"] = {"usage": c.get("usage"),
                                   "note": "the fake sends usage only in the final chunk, as DeepSeek does"}
    for name, limits, scenario in (
            ("request_limit=3", UsageLimits(request_limit=3), "loop"),
            ("tool_calls_limit=1", UsageLimits(tool_calls_limit=1), "tools2"),
            ("total_tokens_limit=250", UsageLimits(total_tokens_limit=250), "serial")):
        SRV.reset()
        deps = deps_for(w)
        try:
            await make_agent(model()).run(f"x [[{scenario}]]", deps=deps, model_settings=settings(),
                                          usage_limits=limits)
            outcome = "finished"
        except UsageLimitExceeded as exc:
            outcome = err(exc)
        out[name] = {"outcome": outcome, "llm_requests": len(SRV.requests),
                     "tools_executed": [o.name for o in deps.outcomes]}
    return out


# ------------------------------------------------------------------ 10 retries / timeouts ---
async def check_errors() -> dict:
    w = World.create("err")
    out: dict[str, Any] = {}
    cases = [("http500 (2x 500 then 200), SDK default max_retries", "http500", settings()),
             ("http500 with max_retries=0", "http500", settings(max_retries=0)),
             ("http400", "http400", settings()),
             ("hang after first chunk, first_event_timeout=1", "hang", settings(first_event_timeout=1.0)),
             ("hang before any chunk, first_event_timeout=1", "hang0", settings(first_event_timeout=1.0)),
             ("hang, request_timeout=1", "hang", settings(request_timeout=1.0)),
             ("drop mid-stream", "drop", settings()),
             ("stream ends without finish_reason", "no_finish", settings())]
    for label, scenario, s in cases:
        SRV.reset()
        t = time.monotonic()
        texts: list[str] = []

        async def on_text(x: str) -> None:
            texts.append(x)
        try:  # streaming, as production does
            async with make_agent(model()).iter(f"x [[{scenario}]]", deps=deps_for(w), model_settings=s) as run:
                await drive(run, on_text)
            outcome = f"ok: {run.result.output!r}"
        except Exception as exc:
            outcome = err(exc)
            if isinstance(exc, ModelHTTPError):
                outcome += f" (status_code={exc.status_code})"
        out[label] = {"outcome": outcome, "http_attempts": len(SRV.requests), "text_streamed": "".join(texts),
                      "elapsed_s": round(time.monotonic() - t, 2)}
    SRV.reset()
    t = time.monotonic()
    try:
        await make_agent(model()).run("x [[infinite]]", deps=deps_for(w), model_settings=settings(), timeout=1.0)
    except RunTimedOut as exc:
        out["whole run timeout=1 (infinite stream)"] = {
            "outcome": err(exc), "elapsed_s": round(time.monotonic() - t, 2),
            "upstream_closed": SRV.conns[0].closed is not None}
    return out


# ------------------------------------------------------------------ 11 tracing ---------------
async def check_tracing() -> dict:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic_ai import InstrumentationSettings
    from pydantic_ai.capabilities.instrumentation import Instrumentation

    import importlib.util
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    w = World.create("otel")
    SRV.reset()
    agent = Agent(model(), deps_type=Deps, tools=backend_tools(), capabilities=[
        Instrumentation(InstrumentationSettings(tracer_provider=provider, include_content=False))])
    await agent.run("x [[tools2]]", deps=deps_for(w), model_settings=settings())
    spans = exporter.get_finished_spans()
    attrs = {k for s in spans for k in (s.attributes or {})}
    content_leak = any(w in json.dumps(dict(s.attributes or {}), default=str) for s in spans for w in ("Paris", "tools2"))
    return {"logfire_installed": importlib.util.find_spec("logfire") is not None,
            "logfire_api_installed": importlib.util.find_spec("logfire_api") is not None,
            "spans": [s.name for s in spans], "attribute_keys": sorted(attrs)[:40],
            "tool_args_or_results_in_spans_with_include_content_False": content_leak}


# ------------------------------------------------------------------ 12 ReplyGuard ------------
async def check_guard() -> dict:
    from app.agent.guard import CityIndex, ReplyGuard, SentenceBuffer
    SRV.reset()
    w = World.create("guard")
    guard = ReplyGuard(CityIndex(w.airline.networks.current().cities), records=[], user_text="hi")
    emitted: list[str] = []
    released = ""
    buf = SentenceBuffer()
    failed: list = []

    class GuardFailed(Exception):
        pass

    async def on_text(t: str) -> None:
        nonlocal released
        for sentence in buf.push(t):
            problems = guard.check(released + sentence)
            if problems:
                failed.extend(problems)
                raise GuardFailed()
            released += sentence
            emitted.append(sentence)  # the SSE `delta` would be sent here

    agent = make_agent(model())
    try:
        async with agent.iter("x [[guard]]", deps=deps_for(w), model_settings=settings()) as run:
            await drive(run, on_text)
        outcome = "finished"
    except GuardFailed:
        outcome = "GuardFailed raised out of the stream; run left; template reply would be emitted"
    await asyncio.sleep(0.2)
    return {"outcome": outcome, "emitted_before_failure": emitted, "problems": failed,
            "upstream_closed_by_client": SRV.conns[0].closed is not None,
            "upstream_finished_normally": SRV.conns[0].finished is not None}


# ------------------------------------------------------------------ 13 budgets ---------------
async def check_budgets() -> dict:
    """The narrow adapter: per-step / per-turn tool admission from the backend's BudgetTracker,
    checked inside the tool function (before any airline call), flood check on the completion
    before tools run, steps and token soft cap before each model request."""
    from app.agent.budget import AgentBudget, BudgetTracker, refusal
    from app.agent.tools import Toolbox
    w = World.create("budget")
    SRV.reset()
    budget = AgentBudget()
    tracker = BudgetTracker(budget)
    toolbox = Toolbox(w.airline)
    executed: list[str] = []
    refused: list[str] = []

    def admitted(name: str):
        def run(ctx: RunContext[Deps], **kwargs: Any) -> str:
            why = tracker.admit_tool_call()
            if why is not None:
                refused.append(f"{name}:{why}")
                return json.dumps(refusal(why, budget))
            executed.append(name)
            return toolbox.run(ctx.deps.user_id, ctx.deps.conversation_id, name, json.dumps(kwargs)).for_llm()
        return run

    from app.agent.tools import TOOL_SCHEMAS
    tools = [Tool.from_schema(admitted(t["function"]["name"]), t["function"]["name"], t["function"]["description"],
                              t["function"]["parameters"], takes_ctx=True) for t in TOOL_SCHEMAS]
    agent = make_agent(model(), tools=tools)
    out: dict[str, Any] = {}
    SRV.reset()
    with ToolManager.parallel_execution_mode("sequential"):
        await agent.run("x [[six]]", deps=deps_for(w), model_settings=settings())
    out["six_calls_one_completion"] = {"executed": list(executed), "refused": list(refused),
                                      "llm_requests": len(SRV.requests)}
    tracker = BudgetTracker(budget)
    executed.clear(); refused.clear(); SRV.reset()
    stop = None
    with ToolManager.parallel_execution_mode("sequential"):
        async with agent.iter("x [[flood]]", deps=deps_for(w), model_settings=settings()) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    if (stop := tracker.stop_before_step()) is not None:
                        break
                elif Agent.is_call_tools_node(node):
                    calls = node.model_response.tool_calls
                    if len(calls) > budget.max_tool_calls_per_step * 4:
                        stop = {"kind": "tool_budget", "value": str(len(calls))}
                        break  # leave before any tool runs
    out["flood_20_calls"] = {"stop": stop, "executed": list(executed), "refused": len(refused),
                             "llm_requests": len(SRV.requests)}
    tracker = BudgetTracker(budget)
    executed.clear(); refused.clear(); SRV.reset()
    with ToolManager.parallel_execution_mode("sequential"):
        try:
            async with agent.iter("x [[loop]]", deps=deps_for(w), model_settings=settings(),
                                  usage_limits=UsageLimits(request_limit=None)) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        if (stop := tracker.stop_before_step()) is not None:
                            break
        except Exception as exc:
            stop = err(exc)
    out["loop_max_steps_6"] = {"stop": stop, "llm_requests": len(SRV.requests), "executed": len(executed)}
    return out


# ------------------------------------------------------------------ 14 deps -----------------
async def check_deps() -> dict:
    """The model can only send arguments of the tool schema; user / conversation come from deps."""
    w = World.create("deps")
    other = w.other_user()
    SRV.reset()
    agent = make_agent(model())
    deps = deps_for(w)
    # the model tries to name another user: the backend's validation refuses unknown arguments
    try:
        await agent.run("x [[spoof]]", deps=deps, model_settings=settings())
    except Exception:
        pass
    sent_schema = SRV.requests[0]["body"]["tools"]
    return {"tool_parameters_sent_to_model": {t["function"]["name"]: sorted(t["function"]["parameters"]["properties"])
                                              for t in sent_schema},
            "model_args": [e["args"] for e in deps.tool_log],
            "user_id_the_tools_used": [e["user_id"] for e in deps.tool_log],
            "world_user": w.user_id, "other_user": other,
            "tool_codes": [o.code for o in deps.outcomes],
            "unknown_argument_result": deps.outcomes[0].payload if deps.outcomes else None}


async def main() -> None:
    checks = [("3_8_tool_errors", check_tool_errors), ("4_6_cancel", check_cancel),
              ("5_cancel_in_sync_tool", check_cancel_in_tool), ("9_usage_limits", check_usage),
              ("10_errors_retries_timeouts", check_errors), ("11_tracing", check_tracing),
              ("12_guard", check_guard), ("13_budgets", check_budgets), ("14_deps", check_deps)]
    only = os.environ.get("ONLY")
    for name, fn in checks:
        if only and not name.startswith(only):
            continue
        t = time.monotonic()
        try:
            OUT[name] = await fn()
        except Exception as exc:
            import traceback
            OUT[name] = {"crashed": traceback.format_exc(limit=8)}
        print(f"== {name} ({time.monotonic() - t:.1f}s)")
        print(json.dumps(OUT[name], ensure_ascii=False, indent=1, default=str)[:3000])
    OUT["outbound_hosts"] = [h for h in AUDIT.hosts()]
    print("== outbound", OUT["outbound_hosts"])
    dump("checks_fake" + (f"_{only}" if only else ""), OUT)


if __name__ == "__main__":
    asyncio.run(main())
