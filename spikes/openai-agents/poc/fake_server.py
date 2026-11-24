"""A tiny OpenAI-compatible chat-completions server on raw asyncio sockets.

It is deliberately below any HTTP framework so it can observe the TCP connection itself: each
request records when the client's FIN/RST arrives (`closed_at`), how many SSE chunks were written
before that, and the request body. Scripts are consumed one per request.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Writer = Callable[[dict | str], Awaitable[None]]


@dataclass
class Req:
    body: dict
    started: float
    closed_at: float | None = None
    chunks_sent: int = 0
    finished: bool = False  # server wrote the terminal chunk itself


def chunk(delta: dict | None = None, finish: str | None = None, usage: dict | None = None,
          choices: bool = True) -> dict:
    out: dict[str, Any] = {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
                           "created": 0, "model": "fake-model"}
    out["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if choices else []
    if usage is not None:
        out["usage"] = usage
    return out


USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
         "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100}


# --- scripts: async functions (write) -> None -----------------------------------------------
def text(s: str, *, interval: float = 0.0, usage: dict | None = USAGE, reasoning: str = ""):
    async def run(write: Writer) -> None:
        await write(chunk({"role": "assistant", "content": ""}))
        if reasoning:
            await write(chunk({"reasoning_content": reasoning}))
        for word in s.split(" "):
            await write(chunk({"content": word + " "}))
            if interval:
                await asyncio.sleep(interval)
        await write(chunk(finish="stop"))
        if usage is not None:
            await write(chunk(usage=usage, choices=False))
        await write("[DONE]")
    return run


def forever(interval: float = 0.02, usage_every: int = 0):
    """Text that never ends (cancellation tests)."""
    async def run(write: Writer) -> None:
        await write(chunk({"role": "assistant", "content": ""}))
        i = 0
        while True:
            i += 1
            await write(chunk({"content": f"w{i} "}))
            await asyncio.sleep(interval)
    return run


def tool_calls(calls: list[tuple[str, str]], *, split: int = 3, interval: float = 0.0,
               usage: dict | None = USAGE, then_hang: bool = False, content: str = ""):
    """`calls`: (name, raw JSON arguments). Arguments are cut into `split`-char fragments, the way
    providers stream them; the id and name come only in the first fragment of each call."""
    async def run(write: Writer) -> None:
        await write(chunk({"role": "assistant", "content": content}))
        for i, (name, args) in enumerate(calls):
            await write(chunk({"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                               "function": {"name": name, "arguments": ""}}]}))
            for k in range(0, len(args), split):
                await write(chunk({"tool_calls": [{"index": i, "function": {"arguments": args[k:k + split]}}]}))
                if interval:
                    await asyncio.sleep(interval)
        if then_hang:
            await asyncio.sleep(3600)
        await write(chunk(finish="tool_calls"))
        if usage is not None:
            await write(chunk(usage=usage, choices=False))
        await write("[DONE]")
    return run


def http_error(status: int, message: str = "fake failure"):
    return ("error", status, message)


def hang():
    """Headers sent, then nothing (read timeout)."""
    async def run(write: Writer) -> None:
        await asyncio.sleep(3600)
    return run


def cut_after(n_words: int):
    """Some text, then the TCP connection is dropped without a terminal chunk."""
    async def run(write: Writer) -> None:
        await write(chunk({"role": "assistant", "content": ""}))
        for i in range(n_words):
            await write(chunk({"content": f"w{i} "}))
        raise ConnectionAbortedError("server drops the connection")
    return run


@dataclass
class FakeServer:
    scripts: list[Any] = field(default_factory=list)
    default: Any = None
    requests: list[Req] = field(default_factory=list)
    port: int = 0
    _server: asyncio.base_events.Server | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def __aenter__(self) -> "FakeServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        assert self._server is not None
        self._server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:  # keep-alive: several requests per connection
                head = await reader.readuntil(b"\r\n\r\n")
                headers = {}
                for line in head.decode().split("\r\n")[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                body = json.loads(await reader.readexactly(int(headers.get("content-length", "0"))) or b"{}")
                req = Req(body=body, started=time.monotonic())
                self.requests.append(req)
                if not await self._respond(req, reader, writer):
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        finally:
            writer.close()

    async def _respond(self, req: Req, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        script = self.scripts.pop(0) if self.scripts else self.default or text("ok.")
        if isinstance(script, tuple) and script[0] == "error":
            payload = json.dumps({"error": {"message": script[2], "type": "server_error"}}).encode()
            writer.write(f"HTTP/1.1 {script[1]} Error\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
            await writer.drain()
            req.finished = True
            return True
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
        await writer.drain()

        async def write(data: dict | str) -> None:
            payload = ("data: " + (data if isinstance(data, str) else json.dumps(data)) + "\n\n").encode()
            writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            await writer.drain()
            req.chunks_sent += 1

        # watch for the client going away while we stream
        async def watch() -> None:
            data = await reader.read(1)
            if not data:
                req.closed_at = time.monotonic()

        watcher = asyncio.ensure_future(watch())
        producer = asyncio.ensure_future(script(write))
        try:
            done, _ = await asyncio.wait({watcher, producer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            pass
        if watcher in done:  # client closed mid-stream
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            return False
        exc = producer.exception()
        if exc is not None:  # cut_after: drop the TCP connection
            watcher.cancel()
            writer.transport.abort()
            return False
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        req.finished = True
        # the connection may be reused; keep watching for close only via the next readuntil
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        return True
