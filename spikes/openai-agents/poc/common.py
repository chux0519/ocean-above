"""Shared scaffolding for the OpenAI Agents SDK POC (#49 phase 1).

- `backend/` is put on sys.path so the POC reuses the real four tools (`app.agent.tools.Toolbox`),
  the airline, the store, the ReplyGuard and the history rebuild, unchanged.
- The API key is read by `app.agent.config.LLMConfig.from_env()` (OCEAN_ENV_FILE), never printed.
- Nothing in here sends traces anywhere: `quiet_tracing()` replaces the SDK's default processor list.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("OCEAN_ENV_FILE", "/Volumes/ZT/repos/llc2026/ocean-above/.env")

# --- outbound connection audit (installed before the SDK is imported) -------------------------
CONNECTS: list[tuple[float, Any]] = []


def _audit(event: str, args: tuple) -> None:
    if event == "socket.connect":
        try:
            CONNECTS.append((time.monotonic(), args[1]))
        except Exception:
            pass


sys.addaudithook(_audit)

import httpx2  # noqa: E402  (openai 3.x transport)
from openai import AsyncOpenAI  # noqa: E402
from agents import (  # noqa: E402
    Agent, FunctionTool, ModelSettings, OpenAIChatCompletionsModel, RunConfig, RunContextWrapper,
    RunHooks, Runner, set_trace_processors,
)
from agents.run_config import ToolExecutionConfig  # noqa: E402
from agents.tool_context import ToolContext  # noqa: E402
from agents.tracing import TracingProcessor  # noqa: E402

from app.agent.concurrency import Threads  # noqa: E402
from app.agent.config import LLMConfig  # noqa: E402
from app.agent.prompt import system_prompt  # noqa: E402
from app.agent.tools import STATUS_TEXT, TOOL_SCHEMAS, Outcome, Toolbox  # noqa: E402
from app.airline import Airline  # noqa: E402
from app.core.network import NetworkProvider  # noqa: E402
from app.store import Store  # noqa: E402

PRICE = {"hit": 0.006, "miss": 0.3, "out": 1.2}  # USD per 1M tokens, same table as backend/evals/runner.py
OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(exist_ok=True)


# --- tracing ------------------------------------------------------------------------------
class RecordingProcessor(TracingProcessor):
    """A custom processor (stand-in for an OTLP bridge, #35): records span kinds, nothing leaves."""

    def __init__(self) -> None:
        self.traces: list[str] = []
        self.spans: list[dict] = []

    def on_trace_start(self, trace) -> None:
        self.traces.append(trace.name)

    def on_trace_end(self, trace) -> None:
        pass

    def on_span_start(self, span) -> None:
        pass

    def on_span_end(self, span) -> None:
        data = span.span_data
        self.spans.append({"type": data.type, "name": getattr(data, "name", None),
                           "parent": span.parent_id is not None,
                           "export": data.export() if data.type in ("generation", "function") else None})

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


TRACES = RecordingProcessor()


def quiet_tracing() -> RecordingProcessor:
    """Replace the default processor list (BatchTraceProcessor -> api.openai.com) with ours only."""
    set_trace_processors([TRACES])
    return TRACES


# --- usage / cost -------------------------------------------------------------------------
class Cost:
    def __init__(self) -> None:
        self.hit = self.miss = self.out = 0

    def add_raw(self, raw: dict | None) -> None:
        if not raw:
            return
        hit = raw.get("prompt_cache_hit_tokens") or 0
        miss = raw.get("prompt_cache_miss_tokens")
        if miss is None:
            miss = (raw.get("prompt_tokens") or 0) - hit
        self.hit += hit
        self.miss += miss
        self.out += raw.get("completion_tokens") or 0

    @property
    def usd(self) -> float:
        return (self.hit * PRICE["hit"] + self.miss * PRICE["miss"] + self.out * PRICE["out"]) / 1e6


COST_FILE = OUT / "cost.json"


def record_cost(script: str, cost: Cost) -> None:
    data = json.loads(COST_FILE.read_text()) if COST_FILE.exists() else {}
    prev = data.get(script, {"usd": 0, "hit": 0, "miss": 0, "out": 0})
    data[script] = {"usd": prev["usd"] + cost.usd, "hit": prev["hit"] + cost.hit,
                    "miss": prev["miss"] + cost.miss, "out": prev["out"] + cost.out}
    data["_total_usd"] = sum(v["usd"] for k, v in data.items() if not k.startswith("_"))
    COST_FILE.write_text(json.dumps(data, indent=2))


# --- model --------------------------------------------------------------------------------
@dataclass
class HttpLog:
    """httpx2 event hooks: every request the SDK's OpenAI client sends, and when its response
    stream is closed (response.aclose is wrapped)."""
    requests: list[tuple[float, str]] = field(default_factory=list)
    responses: list[tuple[float, int]] = field(default_factory=list)
    closed: list[float] = field(default_factory=list)
    bodies: list[dict] = field(default_factory=list)  # request JSON only (no headers: the key is a header)

    def client(self, **kw: Any) -> httpx2.AsyncClient:
        async def on_request(request: httpx2.Request) -> None:
            self.requests.append((time.monotonic(), request.url.path))
            try:
                self.bodies.append(json.loads(request.content or b"{}"))
            except Exception:
                self.bodies.append({})

        async def on_response(response: httpx2.Response) -> None:
            self.responses.append((time.monotonic(), response.status_code))
            original = response.aclose

            async def aclose() -> None:
                if not response.is_closed:
                    self.closed.append(time.monotonic())
                await original()

            response.aclose = aclose  # type: ignore[method-assign]

        return httpx2.AsyncClient(event_hooks={"request": [on_request], "response": [on_response]}, **kw)


def real_config() -> LLMConfig:
    return LLMConfig.from_env()


def make_model(base_url: str, api_key: str, model: str, *, http: HttpLog | None = None,
               max_retries: int = 0, timeout: float | None = None,
               trust_env: bool = True) -> tuple[OpenAIChatCompletionsModel, AsyncOpenAI]:
    """`max_retries=0`: the openai client's own retries are off; retries are the runner's
    (ModelSettings.retry) or ours, so they can be counted against the budget."""
    kw: dict[str, Any] = {"trust_env": trust_env}
    http_client = http.client(**kw) if http else httpx2.AsyncClient(**kw)
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=max_retries,
                         http_client=http_client, **({"timeout": timeout} if timeout else {}))
    return OpenAIChatCompletionsModel(model=model, openai_client=client), client


def deepseek_settings(effort: str = "high", **kw: Any) -> ModelSettings:
    """Same request fields as backend/app/agent/llm.py `OpenAIChatClient.body()`."""
    extra: dict[str, Any] = {}
    if effort == "none":
        extra["thinking"] = {"type": "disabled"}
    elif effort != "omit":
        extra["thinking"] = {"type": "enabled"}
        extra["reasoning_effort"] = effort
    base = dict(temperature=0, max_tokens=4096, tool_choice="auto", include_usage=True,
                preserve_raw_usage=True, extra_body=extra or None)
    base.update(kw)
    return ModelSettings(**base)


# --- the world: private app.db + copy of locations.db -------------------------------------
class World:
    def __init__(self, name: str = "poc"):
        self.dir = Path(tempfile.mkdtemp(prefix=f"oa-{name}-"))
        shutil.copyfile(ROOT / "locations.db", self.dir / "locations.db")
        self.store = Store(self.dir / "app.db")
        self.networks = NetworkProvider(self.dir / "locations.db")
        self.airline = Airline(self.store, self.networks, nonce_secret=b"poc-secret-0123456789")
        self.user = self.store.upsert_user("dev", "poc-user", "POC")["id"]
        self.other_user = self.store.upsert_user("dev", "poc-other", "Other")["id"]
        self.conv = self.store.create_conversation(self.user)["id"]

    def close(self) -> None:
        d = self.dir
        if d and str(d).startswith(tempfile.gettempdir()):
            shutil.rmtree(d, ignore_errors=True)


# --- trusted run context + the four tools as SDK FunctionTools ----------------------------
@dataclass
class TurnCtx:
    """Passed as `Runner.run_streamed(context=...)`. Lives only in our process; the SDK never
    serialises it into the model request, so the model cannot see or set user_id / conversation_id."""
    user_id: str
    conversation_id: str
    toolbox: Toolbox
    threads: Threads = field(default_factory=Threads)
    outcomes: list[tuple[str, Outcome]] = field(default_factory=list)  # (call_id, outcome)
    log: list[str] = field(default_factory=list)
    max_tool_calls_per_turn: int = 12
    max_tool_calls_per_step: int = 4
    tool_calls: int = 0
    step_calls: int = 0
    refused: int = 0
    serial: asyncio.Lock = field(default_factory=asyncio.Lock)
    tool_delay: float = 0.0  # seconds a tool sleeps in its thread (cancellation tests)
    thread_done: list[tuple[str, float]] = field(default_factory=list)


def _invoke(name: str):
    async def on_invoke_tool(tc: ToolContext[TurnCtx], raw_arguments: str) -> str:
        ctx = tc.context
        # budget: per-turn and per-step tool-call caps, checked before anything runs
        ctx.tool_calls += 1
        ctx.step_calls += 1
        if ctx.tool_calls > ctx.max_tool_calls_per_turn or ctx.step_calls > ctx.max_tool_calls_per_step:
            ctx.refused += 1
            limit = "per_turn" if ctx.tool_calls > ctx.max_tool_calls_per_turn else "per_step"
            return json.dumps({"code": "tool_budget_exceeded", "message": "not run", "limit": limit})
        async with ctx.serial:  # one tool at a time, in call order (design §4.3)
            ctx.log.append(f"status: {STATUS_TEXT.get(name)} [{tc.tool_call_id}]")

            def run() -> Outcome:
                if ctx.tool_delay:
                    time.sleep(ctx.tool_delay)
                out = ctx.toolbox.run(ctx.user_id, ctx.conversation_id, name, raw_arguments)
                ctx.thread_done.append((name, time.monotonic()))
                return out

            outcome = await ctx.threads.run(run)  # shielded + tracked: drain() can wait for it
            ctx.outcomes.append((tc.tool_call_id, outcome))
            return outcome.for_llm()

    return on_invoke_tool


def make_tools() -> list[FunctionTool]:
    tools = []
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        tools.append(FunctionTool(
            name=fn["name"], description=fn["description"], params_json_schema=fn["parameters"],
            on_invoke_tool=_invoke(fn["name"]),
            strict_json_schema=False,  # our schema as-is (optional list_my_tickets filters)
        ))
    return tools


class StepHooks(RunHooks[TurnCtx]):
    """Resets the per-step counter at every LLM call; records LLM call starts/ends."""

    def __init__(self) -> None:
        self.llm_starts: list[float] = []
        self.llm_ends: list[tuple[float, Any, Any]] = []  # (t, Usage, raw provider usage)

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        context.context.step_calls = 0
        self.llm_starts.append(time.monotonic())

    async def on_llm_end(self, context, agent, response) -> None:
        self.llm_ends.append((time.monotonic(), response.usage, getattr(response, "raw_usage", None)))


def make_agent(model, settings: ModelSettings, *, instructions: str | None = None,
               tools: list[FunctionTool] | None = None, **kw: Any) -> Agent[TurnCtx]:
    from datetime import date
    return Agent[TurnCtx](name="ocean-above", instructions=instructions or system_prompt(date.today()),
                          model=model, model_settings=settings,
                          tools=make_tools() if tools is None else tools, **kw)


def run_config(**kw: Any) -> RunConfig:
    base = dict(tracing_disabled=False, trace_include_sensitive_data=False,
                tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                tool_not_found_behavior="return_error_to_model", workflow_name="ocean-above turn")
    base.update(kw)
    return RunConfig(**base)


def chat_to_items(messages: list[dict]) -> list[dict]:
    """Narrow adapter: the chat-completions history `app.agent.context.build_context` rebuilds from
    app.db -> SDK input items (Responses format). Reasoning goes in as a reasoning item right before
    its assistant message, so the SDK's converter puts it back as `reasoning_content` (DeepSeek)."""
    items: list[dict] = []
    n = 0
    for m in messages:
        role = m["role"]
        if role in ("user", "system"):
            items.append({"role": role, "content": m["content"]})
        elif role == "assistant":
            if m.get("reasoning_content"):
                n += 1
                items.append({"type": "reasoning", "id": f"rs_hist_{n}",
                              "summary": [{"type": "summary_text", "text": m["reasoning_content"]}]})
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for c in m.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": c["id"], "name": c["function"]["name"],
                              "arguments": c["function"]["arguments"]})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
    return items


def hosts() -> list[str]:
    """Distinct (host, port) the process connected to, resolved back to names where possible."""
    out = []
    for _, addr in CONNECTS:
        if isinstance(addr, tuple):
            host = addr[0]
            if not (host.startswith("127.") or host == "::1"):
                host = "<non-loopback, redacted>"  # the dev machine's HTTP proxy; no real hosts in the repo
            out.append(f"{host}:{addr[1]}")
    return sorted(set(out))


def p(*parts: Any) -> None:
    print(*parts, flush=True)


__all__ = ["Agent", "Cost", "HttpLog", "OUT", "RunContextWrapper", "Runner", "StepHooks", "TRACES",
           "TurnCtx", "Toolbox", "World", "deepseek_settings", "hosts", "make_agent", "make_model",
           "make_tools", "p", "quiet_tracing", "real_config", "record_cost", "run_config", "socket"]
