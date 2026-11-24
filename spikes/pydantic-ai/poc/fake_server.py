"""A local OpenAI-compatible chat-completions server (plain asyncio, HTTP/1.1, SSE) that records
every request and the exact moment each response connection is closed by the client.

The scenario is chosen by a tag `[[scenario]]` in the last user message. Multi-step scenarios
look at how many `tool` messages the request carries.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Conn:
    scenario: str
    opened: float
    closed: float | None = None  # client closed (EOF / reset) while we were still sending
    finished: float | None = None  # we finished the response normally
    chunks: int = 0
    first_chunk: float | None = None


@dataclass
class FakeServer:
    host: str = "127.0.0.1"
    port: int = 0
    requests: list[dict] = field(default_factory=list)
    conns: list[Conn] = field(default_factory=list)
    fail_counts: dict[str, int] = field(default_factory=dict)
    tick: float = 0.02  # seconds between streamed chunks

    # ---------------------------------------------------------------- lifecycle --------------
    def start(self) -> "FakeServer":
        ready = threading.Event()

        def run() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def main() -> None:
                self.server = await asyncio.start_server(self._handle, self.host, self.port)
                self.port = self.server.sockets[0].getsockname()[1]
                ready.set()
                await self.server.serve_forever()

            try:
                self.loop.run_until_complete(main())
            except asyncio.CancelledError:
                pass

        self.thread = threading.Thread(target=run, daemon=True, name="fake-openai")
        self.thread.start()
        ready.wait(5)
        return self

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def reset(self) -> None:
        self.requests.clear()
        self.conns.clear()
        self.fail_counts.clear()

    # ---------------------------------------------------------------- HTTP --------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:  # keep-alive: several requests per connection
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
                body = json.loads(await reader.readexactly(int(headers.get("content-length", 0))) or b"{}")
                keep = await self._respond(body, reader, writer)
                if not keep:
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _respond(self, body: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        msgs = body.get("messages") or []
        last_user = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
        text = last_user.get("content") if isinstance(last_user.get("content"), str) else json.dumps(last_user.get("content"))
        scenario = text.split("[[", 1)[1].split("]]", 1)[0] if "[[" in (text or "") else "hello"
        tools_seen = sum(1 for m in msgs if m.get("role") == "tool")
        retry_prompts = sum(1 for m in msgs if m.get("role") == "user" and m is not last_user)
        self.requests.append({"t": time.monotonic(), "scenario": scenario, "tools_seen": tools_seen,
                              "body": body})
        conn = Conn(scenario, time.monotonic())
        self.conns.append(conn)

        if scenario == "http500":
            n = self.fail_counts.get(scenario, 0)
            self.fail_counts[scenario] = n + 1
            if n < 2:
                payload = b'{"error":{"message":"boom","type":"server_error"}}'
                writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                             b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                await writer.drain()
                conn.finished = time.monotonic()
                return True
        if scenario == "http400":
            payload = b'{"error":{"message":"bad request","type":"invalid_request_error"}}'
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                         b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
            await writer.drain()
            conn.finished = time.monotonic()
            return True

        if not body.get("stream"):
            payload = json.dumps(self._aggregate(list(self._script(scenario, tools_seen, retry_prompts, body)),
                                                 body.get("model", "fake"))).encode()
            if payload == b"null":  # HANG / DROP scripts without a stream
                await asyncio.sleep(120 if scenario == "hang" else 0)
                writer.transport.abort()
                return False
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(payload)).encode() + b"\r\n\r\n" + payload)
            await writer.drain()
            conn.finished = time.monotonic()
            return True

        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: close\r\n\r\n")
        await writer.drain()
        eof = asyncio.ensure_future(reader.read(1))  # the client closing its side

        def on_eof(f: asyncio.Future) -> None:  # the exact moment the client closed
            if not f.cancelled() and conn.finished is None and conn.closed is None:
                conn.closed = time.monotonic()
        eof.add_done_callback(on_eof)

        async def send(obj: Any) -> None:
            if eof.done():
                raise ConnectionResetError("client closed")
            data = obj if isinstance(obj, str) else json.dumps(obj)
            writer.write(f"data: {data}\n\n".encode())
            await writer.drain()
            conn.chunks += 1
            conn.first_chunk = conn.first_chunk or time.monotonic()
            await asyncio.sleep(self.tick)

        try:
            for chunk in self._script(scenario, tools_seen, retry_prompts, body):
                if chunk == "HANG":
                    await asyncio.wait([eof], timeout=120)
                    raise ConnectionResetError("client closed")
                if chunk == "DROP":
                    conn.finished = time.monotonic()
                    writer.transport.abort()
                    return False
                await send(chunk)
            await send("[DONE]")
            conn.finished = time.monotonic()
        except (ConnectionError, OSError):
            conn.closed = conn.closed or time.monotonic()
        finally:
            if not eof.done():
                eof.cancel()
        return False

    @staticmethod
    def _aggregate(chunks: list, model: str) -> dict | None:
        """The non-streaming response equivalent to a script (agent.run() does not stream)."""
        if any(c in ("HANG", "DROP") for c in chunks):
            return None
        msg: dict[str, Any] = {"role": "assistant", "content": ""}
        calls: dict[int, dict] = {}
        finish, usage = None, None
        for c in chunks:
            usage = c.get("usage") or usage
            for ch in c.get("choices") or []:
                d = ch.get("delta") or {}
                msg["content"] += d.get("content") or ""
                if d.get("reasoning_content"):
                    msg["reasoning_content"] = msg.get("reasoning_content", "") + d["reasoning_content"]
                for tc in d.get("tool_calls") or []:
                    call = calls.setdefault(tc["index"], {"id": "", "type": "function",
                                                         "function": {"name": "", "arguments": ""}})
                    call["id"] = tc.get("id") or call["id"]
                    call["function"]["name"] += (tc.get("function") or {}).get("name") or ""
                    call["function"]["arguments"] += (tc.get("function") or {}).get("arguments") or ""
                finish = ch.get("finish_reason") or finish
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return {"id": "c1", "object": "chat.completion", "created": 0, "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish or "stop"}],
                "usage": usage}

    # ---------------------------------------------------------------- scripts ------------------
    def _script(self, scenario: str, tools_seen: int, retry_prompts: int, body: dict):
        model = body.get("model", "fake")

        def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> dict:
            c: dict[str, Any] = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": model,
                                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage is not None:
                c["usage"] = usage
            return c

        def usage(p: int = 100, c: int = 20) -> dict:
            return chunk({}, None, {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c})

        def text(*pieces: str):
            yield chunk({"role": "assistant", "content": ""})
            for p in pieces:
                yield chunk({"content": p})
            yield chunk({}, "stop")
            yield usage()

        def calls(*specs: tuple[str, str], split: int = 4):
            """Tool calls whose arguments arrive in `split` fragments (incremental assembly)."""
            yield chunk({"role": "assistant", "content": None, "reasoning_content": "thinking about tools"})
            for i, (name, args) in enumerate(specs):
                yield chunk({"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                             "function": {"name": name, "arguments": ""}}]})
                step = max(1, len(args) // split)
                for j in range(0, len(args), step):
                    yield chunk({"tool_calls": [{"index": i, "function": {"arguments": args[j:j + step]}}]})
            yield chunk({}, "tool_calls")
            yield usage(200, 40)

        if scenario == "hello":
            yield from text("Hello", " there.", " How can I help?")
        elif scenario == "infinite":
            yield chunk({"role": "assistant", "content": ""})
            for i in range(100000):
                yield chunk({"content": f"tok{i} "})
        elif scenario == "think":
            yield chunk({"role": "assistant", "content": "", "reasoning_content": "Let me think."})
            yield chunk({"reasoning_content": " Still thinking."})
            yield from text("Answer.")
        elif scenario == "tools2":  # two calls in one completion, then text
            if tools_seen == 0:
                yield from calls(("find_city", '{"name": "Paris"}'), ("find_city", '{"name": "Tokyo"}'))
            else:
                yield from text("Both cities checked.")
        elif scenario == "serial":  # one call per step, three steps
            if tools_seen < 2:
                yield from calls(("find_city", json.dumps({"name": ["Paris", "Tokyo"][tools_seen]})))
            else:
                yield from text("Done after two steps.")
        elif scenario == "malformed":  # broken JSON, then valid
            if tools_seen == 0:
                yield from calls(("find_city", '{"name": "Par'))
            else:
                yield from text("Recovered.")
        elif scenario == "malformed_always":
            yield from calls(("find_city", '{"name": "Par'))
        elif scenario == "badargs":  # valid JSON, unknown argument -> backend structured error
            if tools_seen == 0:
                yield from calls(("find_city", '{"city": "Paris"}'))
            else:
                yield from text("Saw the error.")
        elif scenario == "spoof":  # the model tries to act for another user
            if tools_seen == 0:
                yield from calls(("list_my_tickets", '{"user_id": "u_someone_else"}'))
            elif tools_seen == 1:
                yield from calls(("list_my_tickets", '{}'))
            else:
                yield from text("Listed.")
        elif scenario == "unknown_tool":
            if tools_seen == 0:
                yield from calls(("book_ticket", '{"quote_id": "q_1"}'))
            else:
                yield from text("No such tool.")
        elif scenario in ("raise", "modelretry", "slow_tool", "slow_then_more", "two_slow"):
            if scenario == "two_slow" and tools_seen == 0:
                yield from calls(("slow", '{"seconds": 1.0}'), ("slow", '{"seconds": 1.0}'))
            elif scenario == "slow_then_more" and tools_seen == 0:
                yield from calls(("slow", '{"seconds": 1.0}'), ("find_city", '{"name": "Paris"}'))
            elif tools_seen == 0 or (scenario == "modelretry" and tools_seen < 2):
                name = {"raise": "boom", "modelretry": "picky", "slow_tool": "slow"}[scenario]
                yield from calls((name, '{"seconds": 1.0}' if name == "slow" else '{"x": "1"}'))
            else:
                yield from text("After the tool.")
        elif scenario == "flood":
            if tools_seen == 0:
                yield from calls(*[("find_city", '{"name": "Paris"}')] * 20)
            else:
                yield from text("Flooded.")
        elif scenario == "six":
            if tools_seen == 0:
                yield from calls(*[("find_city", json.dumps({"name": c})) for c in
                                   ("Paris", "Tokyo", "Lima", "Delhi", "Dubai", "Moscow")])
            else:
                yield from text("Six asked.")
        elif scenario == "loop":  # always another tool call
            yield from calls(("find_city", '{"name": "Paris"}'))
        elif scenario == "http500":
            yield from text("Third time lucky.")
        elif scenario == "hang":
            yield chunk({"role": "assistant", "content": ""})
            yield "HANG"
        elif scenario == "hang0":
            yield "HANG"
        elif scenario == "drop":
            yield chunk({"role": "assistant", "content": "Partial"})
            yield "DROP"
        elif scenario == "no_finish":
            yield chunk({"role": "assistant", "content": "No finish reason"})
        elif scenario == "guard":
            yield from text("Hello there. ", "The price is $9,999.00.", " Dubai is close.", " More text.")
        else:
            yield from text("Unknown scenario.")
