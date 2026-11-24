"""Shared harness of the Pydantic AI POC (issue #49 phase 1). Not shipped.

- `llm_config()`: the backend's own `LLMConfig.from_env` (reads OCEAN_ENV_FILE inside the process;
  the key is never printed and never part of a repr).
- `make_model()`: `OpenAIChatModel` + `DeepSeekProvider` (an OpenAI-compatible provider), for the
  real DeepSeek or for the local fake server.
- `World`: a temporary app.db + a copy of locations.db + the real `Airline`.
- `make_agent()`: the four backend tools as Pydantic AI tools. The tool schemas are the backend's
  `TOOL_SCHEMAS` verbatim and every call goes through the backend's `Toolbox.run` (its own
  validation, its structured errors). user_id / conversation_id come from `deps` (RunContext),
  never from the model.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.agent.config import LLMConfig  # noqa: E402
from app.agent.tools import TOOL_SCHEMAS, Outcome, Toolbox  # noqa: E402
from app.airline import Airline  # noqa: E402
from app.core.network import NetworkProvider  # noqa: E402
from app.store import Store  # noqa: E402

from pydantic_ai import Agent, RunContext, Tool  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.providers.deepseek import DeepSeekProvider  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

RESULTS = HERE.parent / "results"
RESULTS.mkdir(exist_ok=True)


def llm_config() -> LLMConfig:
    return LLMConfig.from_env()


def make_model(base_url: str | None = None, api_key: str | None = None, *, model_name: str | None = None,
               http_client: Any = None) -> OpenAIChatModel:
    """Real DeepSeek when base_url is None (key read from OCEAN_ENV_FILE), else the fake server."""
    if base_url is None:
        cfg = llm_config()
        base_url, api_key, model_name = cfg.base_url, cfg.api_key, model_name or cfg.model
    kwargs: dict[str, Any] = {"api_key": api_key or "sk-fake", "base_url": base_url}
    if http_client is not None:
        kwargs["http_client"] = http_client
    return OpenAIChatModel(model_name or "deepseek-flash", provider=DeepSeekProvider(**kwargs))


def settings(effort: str = "high", **extra: Any) -> ModelSettings:
    """The backend's settings (llm.py body()): temperature 0, max_tokens 4096, thinking level."""
    s: ModelSettings = {"temperature": 0, "max_tokens": 4096, **extra}  # type: ignore[typeddict-item]
    if effort == "none":
        s["thinking"] = False
    elif effort != "omit":
        s["thinking"] = effort  # type: ignore[typeddict-item]
    return s


@dataclass
class World:
    dir: Path
    store: Store
    airline: Airline
    user_id: str
    conversation_id: str

    @classmethod
    def create(cls, name: str = "poc") -> "World":
        d = Path(tempfile.mkdtemp(prefix=f"pa-{name}-"))
        shutil.copyfile(REPO / "locations.db", d / "locations.db")
        store = Store(d / "app.db")
        airline = Airline(store, NetworkProvider(d / "locations.db"))
        user = store.upsert_user("poc", name, "POC")["id"]
        conv = store.create_conversation(user)["id"]
        return cls(d, store, airline, user, conv)

    def other_user(self) -> str:
        return self.store.upsert_user("poc", "someone-else", "Other")["id"]


@dataclass
class Deps:
    """Trusted run context: set by our code, never by the model."""

    user_id: str
    conversation_id: str
    toolbox: Toolbox
    outcomes: list[Outcome] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)  # (name, thread, start, end)


def _tool_fn(name: str):
    def run(ctx: RunContext[Deps], **kwargs: Any) -> str:
        started = time.monotonic()
        raw = json.dumps(kwargs, ensure_ascii=False)
        outcome = ctx.deps.toolbox.run(ctx.deps.user_id, ctx.deps.conversation_id, name, raw)
        ctx.deps.outcomes.append(outcome)
        ctx.deps.tool_log.append({"name": name, "args": kwargs, "code": outcome.code, "user_id": ctx.deps.user_id,
                                  "thread": threading.current_thread().name,
                                  "t0": started, "t1": time.monotonic()})
        return outcome.for_llm()
    run.__name__ = name
    return run


def backend_tools() -> list[Tool]:
    return [Tool.from_schema(_tool_fn(t["function"]["name"]), name=t["function"]["name"],
                             description=t["function"]["description"],
                             json_schema=t["function"]["parameters"], takes_ctx=True)
            for t in TOOL_SCHEMAS]


SYSTEM = ("You are the Ocean Above booking assistant. Use the tools for every fact; never compute "
          "distances or prices yourself; quote numbers exactly as the tool results show them.")


def make_agent(model: Any, *, tools: list[Tool] | None = None, instructions: str = SYSTEM,
               retries: int = 1) -> Agent[Deps, str]:
    return Agent(model, deps_type=Deps, tools=tools if tools is not None else backend_tools(),
                 instructions=instructions, retries=retries)


def deps_for(world: World) -> Deps:
    return Deps(world.user_id, world.conversation_id, Toolbox(world.airline))


def dump(name: str, data: Any) -> Path:
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


# --------------------------------------------------------------------- outbound audit ---------
class SocketAudit:
    """Every socket.connect / getaddrinfo the process makes (sys.addaudithook). Proves what the
    SDK contacts by default (tracing, price data, tiktoken downloads ...)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        sys.addaudithook(self._hook)

    def _hook(self, event: str, args: tuple) -> None:
        if event == "socket.connect":
            self.events.append((event, repr(args[1])[:80]))
        elif event == "socket.getaddrinfo":
            self.events.append((event, repr(args[0])[:80]))

    def hosts(self) -> list[str]:
        return sorted({a for _, a in self.events})
