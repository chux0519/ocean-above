"""Streaming client for an OpenAI-compatible chat completions API (plain httpx, no SDK).

`stream()` yields `TextDelta`s as the model writes and exactly one `Completion` at the end, with
the tool calls assembled from their fragments. Any failure is an `LLMError` that says whether a
retry makes sense. The API key only ever goes into the Authorization header.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

import httpx

from .config import EFFORTS, LLMConfig

MAX_TOKENS = 4096  # includes the reasoning tokens of thinking mode


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "arguments": str}]
    finish_reason: str | None = None
    usage: dict | None = None
    reasoning: str = ""

    def assistant_message(self) -> dict:
        """The message to append to the context before the tool results (tool calls in their
        wire-safe form, see `wire_tool_call`)."""
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
    def total_tokens(self) -> int | None:
        return (self.usage or {}).get("total_tokens")


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


class _ThinkSplitter:
    """Moves a `<think>...</think>` block at the start of `content` (servers that inline the
    reasoning instead of using `reasoning_content`) out of the visible text."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.state = "start"  # start -> think -> text
        self.buf = ""

    def feed(self, text: str) -> tuple[str, str]:
        """(visible text, reasoning text) for this chunk."""
        if self.state == "text":
            return text, ""
        if self.state == "lead":  # white space right after </think> is dropped
            text = text.lstrip()
            if text:
                self.state = "text"
            return text, ""
        self.buf += text
        if self.state == "start":
            head = self.buf.lstrip()
            if self.OPEN.startswith(head):  # may still become "<think>"
                return "", ""
            if not head.startswith(self.OPEN):
                self.state, out, self.buf = "text", self.buf, ""
                return out, ""
            self.state, self.buf = "think", head[len(self.OPEN):]
        end = self.buf.find(self.CLOSE)
        if end < 0:  # keep a possible partial "</think>" at the end
            keep = max((k for k in range(1, len(self.CLOSE)) if self.buf.endswith(self.CLOSE[:k])), default=0)
            reasoning, self.buf = self.buf[:len(self.buf) - keep], self.buf[len(self.buf) - keep:]
            return "", reasoning
        reasoning, rest = self.buf[:end], self.buf[end + len(self.CLOSE):].lstrip()
        self.state, self.buf = ("text" if rest else "lead"), ""
        return rest, reasoning

    def finish(self) -> tuple[str, str]:
        rest, self.buf = self.buf, ""
        if self.state == "think":
            return "", rest
        return rest, ""


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLM(Protocol):
    model: str

    def stream(self, messages: list[dict], tools: list[dict], *,
               reasoning_effort: str | None = None) -> AsyncIterator[TextDelta | Completion]:
        """`reasoning_effort`: per-request thinking level (none / low / high / max); None = the
        client's configured default."""
        ...


class OpenAIChatClient:
    """One instance per event loop (it keeps an httpx.AsyncClient); `await aclose()` at shutdown."""

    def __init__(self, config: LLMConfig, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None, temperature: float = 0,
                 max_tokens: int = MAX_TOKENS):
        self.config = config
        self.model = config.model
        self.reasoning_effort = config.reasoning_effort
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._transport = transport
        self._timeout = timeout or httpx.Timeout(connect=10, read=60, write=30, pool=10)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def body(self, messages: list[dict], tools: list[dict], reasoning_effort: str | None = None) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        effort = reasoning_effort or self.reasoning_effort
        if reasoning_effort is not None and reasoning_effort not in EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {', '.join(EFFORTS)}")
        if effort == "none":
            body["thinking"] = {"type": "disabled"}
        elif effort != "omit":
            # thinking mode ignores `temperature` (DeepSeek docs); it is sent anyway, harmlessly
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = effort
        return body

    def _scrub(self, text: str) -> str:
        return text.replace(self.config.api_key, "***") if self.config.api_key else text

    async def stream(self, messages: list[dict], tools: list[dict], *,
                     reasoning_effort: str | None = None) -> AsyncIterator[TextDelta | Completion]:
        body = self.body(messages, tools, reasoning_effort)
        done = Completion()
        think = _ThinkSplitter()
        calls: dict[int, dict] = {}
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Accept": "text/event-stream"}
        try:
            async with self._http().stream("POST", f"{self.config.base_url}/chat/completions",
                                           json=body, headers=headers) as response:
                if response.status_code != 200:
                    status = response.status_code
                    detail = ""
                    if status not in (401, 403):  # an auth error body may quote the key
                        detail = ": " + self._scrub((await response.aread()).decode("utf-8", "replace"))[:300]
                    raise LLMError(f"LLM HTTP {status}{detail}", status=status,
                                   retryable=status in (408, 409, 429) or status >= 500)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank separators, `: keep-alive` comments
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        raise LLMError("LLM sent a chunk that is not JSON", retryable=True) from None
                    if chunk.get("error"):
                        raise LLMError("LLM stream error: " + self._scrub(json.dumps(chunk["error"]))[:300],
                                       retryable=True)
                    if chunk.get("usage"):
                        done.usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):  # thinking: collected, never yielded
                            done.reasoning += delta["reasoning_content"]
                        if delta.get("content"):
                            visible, thought = think.feed(delta["content"])
                            done.reasoning += thought
                            if visible:
                                done.content += visible
                                yield TextDelta(visible)
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            function = part.get("function") or {}
                            call["id"] = part.get("id") or call["id"]
                            call["name"] += function.get("name") or ""
                            call["arguments"] += function.get("arguments") or ""
                        if choice.get("finish_reason"):
                            done.finish_reason = choice["finish_reason"]
        except LLMError:
            raise
        except (httpx.HTTPError, httpx.StreamError) as exc:  # timeouts, connections, cut streams
            raise LLMError(f"LLM transport error: {type(exc).__name__}", retryable=True) from None
        except Exception as exc:  # a chunk of an unexpected shape, undecodable bytes, ...
            raise LLMError(f"LLM stream could not be read: {type(exc).__name__}", retryable=True) from None
        visible, thought = think.finish()
        done.reasoning += thought
        if visible:
            done.content += visible
            yield TextDelta(visible)
        if done.finish_reason is None:
            raise LLMError("LLM stream ended without a finish_reason", retryable=True)
        for index in sorted(calls):
            call = calls[index]
            call["id"] = call["id"] or f"call_{index}"
            done.tool_calls.append(call)
        yield done
