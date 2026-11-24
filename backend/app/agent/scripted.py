"""Offline fake LLMs (tests, the evals' CI mode, the benchmark): a scripted generator behind a
fake OpenAI-compatible HTTP endpoint.

A subclass writes `async def stream(messages, tools, *, reasoning_effort)` and yields `TextDelta`s
and one `Completion`, or raises `LLMError`, exactly as the scripts did before ADR-001. `ScriptedLLM`
serves it as chat-completions SSE through an httpx2 mock transport, so the turn runs through the
real Pydantic AI `OpenAIChatModel` (request body, chunk parsing, tool-call assembly, finish reason,
usage), and the script sees the request as it would reach the provider:
- `messages`, `tools`: the wire JSON of the request;
- `reasoning_effort`: read back from the request's thinking fields ("none" / the level / None).
Failures are played as the wire would show them: an `LLMError` with a `status` before any output is
that HTTP status; any other failure before output is a connection error (with `received=n`, n
characters of reasoning are streamed first); a failure after output cuts the stream (read error).
A script that ends without a `Completion` ends the stream without a finish reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx2

from .config import LLMConfig
from .model import ChatModel, Completion, LLMError

__all__ = ["Completion", "LLMError", "ScriptedLLM", "TextDelta"]


@dataclass(frozen=True)
class TextDelta:
    text: str


def _chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> bytes:
    c: dict[str, Any] = {"id": "scripted", "object": "chat.completion.chunk", "created": 0, "model": "scripted",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        c["usage"] = usage
    return f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode()


def _effort(body: dict) -> str | None:
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        return "none"
    return body.get("reasoning_effort")


class ScriptedLLM(ChatModel):
    model = "scripted-fake"

    def __init__(self, *, reasoning_effort: str = "high"):
        config = LLMConfig(api_key="sk-scripted-not-a-key", base_url="http://scripted.invalid/v1", model=self.model,
                           reasoning_effort=reasoning_effort)
        # the DeepSeek dialect, as in production (reasoning_content passed back as a field)
        super().__init__(config, transport=httpx2.MockTransport(self._handle), provider="deepseek")
        self.bodies: list[dict] = []  # every request body, as sent

    def stream(self, messages: list[dict], tools: list[dict], *,
               reasoning_effort: str | None = None) -> AsyncIterator[Any]:
        """The script: yields TextDelta and one Completion, or raises LLMError."""
        raise NotImplementedError

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        gen = self.stream(body.get("messages") or [], body.get("tools") or [], reasoning_effort=_effort(body))
        try:
            first = await anext(gen)
        except StopAsyncIteration:
            first = None
        except LLMError as exc:
            if exc.status is not None:
                return httpx2.Response(exc.status)  # no body: the error is the status
            if exc.received:
                return self._stream(None, gen, failure=exc)
            raise httpx2.ConnectError(str(exc), request=request) from None
        except Exception as exc:  # anything else before output: no response at all
            raise httpx2.ConnectError(f"{type(exc).__name__}: {exc}", request=request) from None
        return self._stream(first, gen)

    def _stream(self, first: Any, gen: AsyncIterator[Any], failure: LLMError | None = None) -> httpx2.Response:
        async def body() -> AsyncIterator[bytes]:
            yield _chunk({"role": "assistant", "content": ""})
            if failure is not None:
                yield _chunk({"reasoning_content": "x" * int(failure.received or 0)})
                raise httpx2.ReadError(str(failure))
            item = first
            while item is not None:
                if isinstance(item, TextDelta):
                    yield _chunk({"content": item.text})
                elif isinstance(item, Completion):
                    for data in _completion(item):
                        yield data
                try:
                    item = await anext(gen)
                except StopAsyncIteration:
                    break
                except Exception as exc:  # a failure after output: the stream is cut
                    raise httpx2.ReadError(f"{type(exc).__name__}: {exc}") from None
            yield b"data: [DONE]\n\n"

        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body())


def _completion(c: Completion) -> list[bytes]:
    out = []
    if c.reasoning:
        out.append(_chunk({"reasoning_content": c.reasoning}))
    for i, call in enumerate(c.tool_calls):
        out.append(_chunk({"tool_calls": [{"index": i, "id": call["id"], "type": "function",
                                           "function": {"name": call["name"], "arguments": call["arguments"]}}]}))
    if c.finish_reason is not None:
        out.append(_chunk({}, c.finish_reason))
    if c.usage is not None:
        out.append(_chunk({}, None, c.usage))
    return out
