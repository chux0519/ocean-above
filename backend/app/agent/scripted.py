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
that HTTP status; an `LLMError` without one is a connection error (with `received=n`, n characters
of reasoning are streamed first, then the stream is cut); an `LLMError` after output cuts the stream
(read error). Any other exception is raised as it is, inside the client (the loop treats it
as a failed call, like an SDK parser error).

`streams` records each streamed response: "open" while it is read, "done" once `[DONE]` was sent,
"closed early" when the client closed it before that (at the moment it closed it).
A script that ends without a `Completion` ends the stream without a finish reason.

Test support only: it ships with the app (evals and bench import it) but the server never builds
one; `routes.install` makes a `ChatModel` from the environment unless a test passes an LLM in.
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


DONE = b"data: [DONE]\n\n"


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
        super().__init__(config, transport=httpx2.MockTransport(self._handle))
        self.bodies: list[dict] = []  # every request body, as sent
        self.streams: list[str] = []  # per streamed response: "open", then "done" or "closed early"

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
        return self._stream(first, gen)

    def _stream(self, first: Any, gen: AsyncIterator[Any], failure: LLMError | None = None) -> httpx2.Response:
        index = len(self.streams)
        self.streams.append("open")
        streams = self.streams

        class Body(httpx2.AsyncByteStream):
            """Closed by the client: `aclose` is called when it stops reading the response."""

            async def __aiter__(self) -> AsyncIterator[bytes]:
                async for data in lines():
                    if data == DONE:
                        streams[index] = "done"
                    yield data

            async def aclose(self) -> None:
                if streams[index] == "open":
                    streams[index] = "closed early"
                await gen.aclose()

        async def lines() -> AsyncIterator[bytes]:
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
                except LLMError as exc:  # a failure after output: the stream is cut
                    raise httpx2.ReadError(str(exc)) from None
            yield DONE

        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())


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
