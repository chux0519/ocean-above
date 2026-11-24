"""The LLM of the agent (ADR-001): a Pydantic AI `OpenAIChatModel` for an OpenAI-compatible chat
completions API (DeepSeek by default), plus the small pieces of our own the loop needs around it.

Pydantic AI owns the HTTP stream, chunk parsing, tool-call assembly and inline `<think>` blocks.
Ours, here:
- `ChatModel`: the model and the request settings of today's wire format (temperature, max_tokens,
  DeepSeek's `thinking` + `reasoning_effort` via `extra_body`, no SDK-side retries: the loop retries,
  logs and charges every attempt itself, design §4.8);
- `Completion`: what one LLM call produced, in the shape stored in `llm_calls` (context.py reads it);
- `LLMError` and `llm_error()`: SDK and transport failures mapped to "retryable or not" and "how much
  output was received", the two things the loop's retry and charge rules need;
- `to_model_messages()`: the OpenAI-style context the loop keeps (built from app.db by context.py)
  as Pydantic AI `message_history`.
The API key only ever goes into the client's Authorization header.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # no promotional banner on stdout (ADR-001)

import httpx2  # noqa: E402  (after the banner switch)
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.messages import (ModelMessage, ModelRequest, ModelResponse, SystemPromptPart,  # noqa: E402
                                  TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, UserPromptPart)
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.profiles import ModelProfile, merge_profile  # noqa: E402
from pydantic_ai.providers.deepseek import DeepSeekProvider  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

from .budget import AgentBudget  # noqa: E402
from .config import EFFORTS, LLMConfig  # noqa: E402

MAX_TOKENS = AgentBudget().max_output_tokens_per_call  # includes the reasoning tokens of thinking mode
DEFAULT_TIMEOUT = httpx2.Timeout(connect=10, read=60, write=30, pool=10)


# ------------------------------------------------------------------ one LLM call -------------
@dataclass
class Completion:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "arguments": str}]
    finish_reason: str | None = None
    usage: dict | None = None
    reasoning: str = ""

    def assistant_message(self) -> dict:
        """The message the context gets for this call (tool calls in their wire-safe form)."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.tool_calls:
            message["tool_calls"] = [wire_tool_call(c["id"], c["name"], c["arguments"]) for c in self.tool_calls]
        return message

    def as_record(self) -> dict:
        """What is stored in llm_calls: the reasoning is kept verbatim for provenance and to pass
        it back in later requests; it is never shown to the user and never guarded as reply text."""
        return {"content": self.content, "reasoning_content": self.reasoning,
                "tool_calls": self.tool_calls, "finish_reason": self.finish_reason, "usage": self.usage}

    @property
    def output_chars(self) -> int:
        """Characters the model produced: text, reasoning, tool call names and arguments."""
        return len(self.content) + len(self.reasoning) + sum(
            len(str(c.get("name") or "")) + len(str(c.get("arguments") or "")) for c in self.tool_calls)

    @classmethod
    def from_response(cls, response: ModelResponse, content: str) -> "Completion":
        """`content`: the visible text as streamed (the response's text parts joined). The raw
        `finish_reason` is the provider's (None when the stream ended without one: Pydantic AI
        reports that as "stop", we do not)."""
        reasoning = "".join(p.content for p in response.parts if isinstance(p, ThinkingPart))
        calls = [{"id": p.tool_call_id or f"call_{i}", "name": p.tool_name,
                  "arguments": p.args if isinstance(p.args, str) else json.dumps(p.args or {}, ensure_ascii=False)}
                 for i, p in enumerate(response.parts) if isinstance(p, ToolCallPart)]
        finish = (response.provider_details or {}).get("finish_reason")
        return cls(content, calls, finish, usage_dict(response), reasoning)


def usage_dict(response: ModelResponse) -> dict | None:
    """The provider's usage in the OpenAI shape budget.py reads; None when none was reported."""
    u = response.usage
    if not (u.input_tokens or u.output_tokens):
        return None
    out: dict[str, Any] = {"prompt_tokens": u.input_tokens, "completion_tokens": u.output_tokens,
                           "total_tokens": u.input_tokens + u.output_tokens}
    if u.details:
        out["details"] = dict(u.details)
    return out


_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
INVALID_TOOL_NAME = "invalid_tool"


def wire_tool_call(call_id: str, name: str, arguments: Any) -> dict:
    """A tool call as it is sent back to the LLM. A name the API may reject (OpenAI:
    `^[a-zA-Z0-9_-]{1,64}$`) becomes `invalid_tool`, and arguments that are not a JSON object
    become `{}`, so one broken call cannot make every later request of the conversation fail.
    The raw values stay in `tool_calls` (app.db) and in the tool result's error."""
    if not isinstance(name, str) or not _TOOL_NAME.match(name):
        name = INVALID_TOOL_NAME
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    try:
        ok = isinstance(json.loads(arguments), dict)
    except (ValueError, RecursionError):
        ok = False
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments if ok else "{}"}}


# ------------------------------------------------------------------ errors -------------------
class LLMError(Exception):
    """A failed LLM call. `received` says what it may have cost (06 review P1-2): None when no
    response body was read (HTTP error status, connection / TLS failure, timeout before the
    response): the provider generated nothing. Otherwise the number of output characters
    (text, reasoning, tool call names and arguments) received before the stream broke. `usage`:
    the usage block, if the provider sent one before the failure."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None,
                 received: int | None = None, usage: dict | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.received = received
        self.usage = usage


def llm_error(exc: BaseException, *, received: int | None, scrub: str = "") -> LLMError:
    """An SDK / transport failure as an LLMError. `received`: output characters seen in this
    attempt, None when no output event arrived. HTTP errors read no body (received None); the
    body is quoted except for 401/403 (it may quote the key) and never contains `scrub`."""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        detail = ""
        if status not in (401, 403) and exc.body:
            body = exc.body if isinstance(exc.body, str) else json.dumps(exc.body, default=str)
            detail = ": " + (body.replace(scrub, "***") if scrub else body)[:300]
        return LLMError(f"LLM HTTP {status}{detail}", status=status,
                        retryable=status in (408, 409, 429) or status >= 500)
    if isinstance(exc, ModelAPIError):  # connection errors, timeouts, cut streams
        return LLMError(f"LLM transport error: {type(exc).__name__}", retryable=True, received=received)
    if isinstance(exc, UnexpectedModelBehavior):  # a chunk or response of an unexpected shape
        return LLMError(f"LLM stream could not be read: {type(exc).__name__}", retryable=True, received=received)
    return LLMError(f"LLM stream could not be read: {type(exc).__name__}", retryable=True, received=received)


# ------------------------------------------------------------------ the model ----------------
class ChatModel:
    """One instance per event loop (it keeps an HTTP client); `await aclose()` at shutdown.
    `transport`: an httpx2 transport for tests (the scripted fakes in `scripted.py`)."""

    def __init__(self, config: LLMConfig, *, transport: httpx2.AsyncBaseTransport | None = None,
                 timeout: httpx2.Timeout | None = None, temperature: float = 0,
                 max_tokens: int = MAX_TOKENS, provider: str | None = None):
        """`provider`: "deepseek" or "openai" (the request dialect); None: DeepSeek when the model
        or the base URL says so, else plain OpenAI."""
        self.config = config
        self.model = config.model  # the name, as llm_calls stores it
        self.reasoning_effort = config.reasoning_effort
        self.temperature = temperature
        self.max_tokens = max_tokens
        # trust_env: proxies from the environment, as before
        self._http = httpx2.AsyncClient(transport=transport, timeout=timeout or DEFAULT_TIMEOUT)
        provider_kwargs = {"api_key": config.api_key, "base_url": config.base_url, "http_client": self._http}
        # DeepSeek's provider knows its wire quirks (reasoning_content passed back with tool calls,
        # `max_tokens` instead of `max_completion_tokens`); any other server gets the plain OpenAI one
        deepseek = provider == "deepseek" if provider else (
            config.model.startswith("deepseek") or "deepseek" in config.base_url)
        provider = DeepSeekProvider(**provider_kwargs) if deepseek else OpenAIProvider(**provider_kwargs)
        # our context has mid-conversation system messages ([booking event], [earlier turns
        # omitted]); they go on the wire as system messages, as before, not as <system> blocks
        # inside user turns with an announcement prepended (the SDK's default for this dialect)
        profile = merge_profile(provider.model_profile(config.model), ModelProfile(supports_inline_system_prompts=True))
        self.chat = OpenAIChatModel(config.model, provider=provider, profile=profile)

    def settings(self, reasoning_effort: str | None = None) -> ModelSettings:
        """The request settings of one call: today's body fields (llm.py before ADR-001)."""
        if reasoning_effort is not None and reasoning_effort not in EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {', '.join(EFFORTS)}")
        effort = reasoning_effort or self.reasoning_effort
        extra: dict[str, Any] = {}
        if effort == "none":
            extra["thinking"] = {"type": "disabled"}
        elif effort != "omit":
            # thinking mode ignores `temperature` (DeepSeek docs); it is sent anyway, harmlessly
            extra["thinking"] = {"type": "enabled"}
            extra["reasoning_effort"] = effort
        settings: ModelSettings = {"temperature": self.temperature, "max_tokens": self.max_tokens,
                                   "max_retries": 0,  # the loop retries (and charges) itself
                                   "framing_announcement": "never"}  # no SDK text added to our prompt
        if extra:
            settings["extra_body"] = extra
        return settings

    def effort(self, reasoning_effort: str | None) -> str:
        return reasoning_effort or self.reasoning_effort

    async def aclose(self) -> None:
        await self._http.aclose()


# ------------------------------------------------------------------ history ------------------
def to_model_messages(messages: list[dict]) -> list[ModelMessage]:
    """The loop's OpenAI-style context (system / user / assistant / tool dicts, as context.py
    rebuilds it from app.db) as Pydantic AI messages. Assistant reasoning goes back as a
    DeepSeek `reasoning_content` thinking part, verbatim."""
    out: list[ModelMessage] = []
    pending: list[Any] = []
    names: dict[str, str] = {}

    def flush() -> None:
        if pending:
            out.append(ModelRequest(parts=list(pending)))
            pending.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            pending.append(SystemPromptPart(content=m.get("content") or ""))
        elif role == "user":
            pending.append(UserPromptPart(content=m.get("content") or ""))
        elif role == "tool":
            call_id = m.get("tool_call_id") or ""
            pending.append(ToolReturnPart(tool_name=names.get(call_id, INVALID_TOOL_NAME),
                                          content=m.get("content") or "", tool_call_id=call_id))
        elif role == "assistant":
            flush()
            parts: list[Any] = []
            if m.get("reasoning_content"):
                parts.append(ThinkingPart(content=m["reasoning_content"], id="reasoning_content",
                                          provider_name="deepseek"))
            if m.get("content"):
                parts.append(TextPart(content=m["content"]))
            for c in m.get("tool_calls") or []:
                names[c["id"]] = c["function"]["name"]
                parts.append(ToolCallPart(tool_name=c["function"]["name"], args=c["function"]["arguments"],
                                          tool_call_id=c["id"]))
            if not parts:
                parts.append(TextPart(content=""))
            out.append(ModelResponse(parts=parts, provider_name="deepseek"))
    flush()
    return out
