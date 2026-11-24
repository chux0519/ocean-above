"""Opt-in OpenTelemetry tracing (#35, app/tracing.py, design §4.11).

Off by default (nothing built, nothing imported, no thread); on: one trace per chat turn with the
SDK's spans and ours nested under `agent.turn`; nothing sensitive in any exported value; an
exporter or collector that fails never changes a chat or a booking; a Stop shows `run.stop`.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import tracing
from app.agent import Agent, LLMError
from app.api import Settings, session
from app.core import network
from app.core.network import NetworkDataError
from app.main import create_app

from .agent_helpers import Call, FakeLLM, Wait, assert_stream, names, run_turn
from .airline_helpers import SECRET, World

BACKEND = Path(__file__).resolve().parents[1]
SECRET_KEY = b"session-secret-0123456789"
USER_TEXT = "Fly me from Shanghai to Chengdu, my email is alice.private@example.com"
QUOTE = [[Call("quote_route", origin="Shanghai", destination="Chengdu")], ["Here is your route."]]


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OTEL_TRACING_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w", real=True)


@pytest.fixture
def traced():
    """Tracing on, exporting (through the real `Redacting` wrapper and batch processor) to memory."""
    memory = InMemorySpanExporter()
    provider = tracing.build({}, exporter=memory)
    before = tracing.use(provider)

    def spans():
        provider.force_flush()
        return list(memory.get_finished_spans())
    yield SimpleNamespace(provider=provider, memory=memory, spans=spans)
    tracing.use(before)
    provider.shutdown()


def by_name(spans, name):
    return [s for s in spans if s.name == name]


def one(spans, name):
    found = by_name(spans, name)
    assert len(found) == 1, (name, [s.name for s in spans])
    return found[0]


def parent_of(spans, span):
    if span.parent is None:
        return None
    return next(s for s in spans if s.context.span_id == span.parent.span_id)


def dump(spans) -> str:
    """Everything a collector receives: names, attributes, events, status, resource."""
    out = []
    for s in spans:
        out.append({"name": s.name, "attributes": {k: _plain(v) for k, v in (s.attributes or {}).items()},
                    "events": [{"name": e.name, "attributes": {k: _plain(v) for k, v in (e.attributes or {}).items()}}
                               for e in s.events],
                    "status": [str(s.status.status_code), s.status.description],
                    "resource": {k: _plain(v) for k, v in s.resource.attributes.items()},
                    "links": len(s.links)})
    return json.dumps(out, ensure_ascii=False)


def _plain(value):
    return list(value) if isinstance(value, tuple) else value


# ============================================================ off by default ====================
def test_settings_and_env_are_off_by_default():
    assert Settings().tracing is False
    assert Settings.from_env({}).tracing is False
    assert Settings.from_env({"OTEL_TRACING_ENABLED": "1"}).tracing is True
    assert tracing.from_env({}) is None
    assert tracing.from_env({"OTEL_TRACING_ENABLED": "0"}) is None
    assert tracing.provider() is None


def test_off_builds_no_provider_imports_no_sdk_and_starts_no_thread(tmp_path):
    """A whole app run (startup, login, a chat turn with tool spans, a booking, shutdown) in a
    fresh process without OTEL_TRACING_ENABLED: the OpenTelemetry SDK and exporter are never
    imported, no export thread exists and the SDK runtime has no instrumentation."""
    script = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(BACKEND)!r})
        from pathlib import Path
        from fastapi.testclient import TestClient
        from app import tracing
        from app.api import Settings, session
        from app.main import create_app
        from pydantic_ai.capabilities import Instrumentation
        from tests.agent_helpers import Call, FakeLLM
        from tests.airline_helpers import World
        world = World(Path({str(tmp_path)!r}) / "w", real=True)
        llm = FakeLLM([Call("quote_route", origin="Shanghai", destination="Chengdu")], ["Here is your route."])
        settings = Settings.from_env({{"APP_DB": str(world.dir / "app.db"), "DEV_AUTH_USER": "demo", "DEV_AUTH_PASSWORD": "pw"}})
        with TestClient(create_app(world.db, settings, clock=world.clock, llm=llm)) as c:
            c.cookies.set(session.COOKIE, session.issue(settings.session_secret, world.alice, 3600))
            res = c.post(f"/api/conversations/{{world.conv}}/messages", json={{"text": "Shanghai to Chengdu"}})
            assert res.status_code == 200 and "event: done" in res.text, res.text[-300:]
            agent = c.app.state.agent
            caps = agent._runtime.root_capability.capabilities  # private SDK attribute (ADR-001 upgrade check)
            print("provider", c.app.state.tracer_provider, tracing.provider())
            print("instrumented", any(isinstance(x, Instrumentation) for x in caps))
            print("threads", sorted(t.name for t in threading.enumerate() if "span" in t.name.lower() or "otel" in t.name.lower()))
        print("sdk", sorted(m for m in sys.modules if m.startswith(("opentelemetry.sdk", "opentelemetry.exporter"))))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["OCEAN_ENV_FILE"] = str(tmp_path / "no-such.env")
    env.pop("LLM_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=BACKEND,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = dict(line.split(" ", 1) for line in out.stdout.strip().splitlines())
    assert lines == {"provider": "None None", "instrumented": "False", "threads": "[]", "sdk": "[]"}, out.stdout


def test_off_span_calls_are_a_shared_no_op():
    with tracing.span("anything", {"a": 1}) as span:
        span.set_attribute("b", 2)
        span.add_event("c")
    assert span is tracing.NO_SPAN and tracing.current() is tracing.NO_SPAN


# ============================================================ one quoted itinerary ==============
def test_one_quote_turn_is_one_trace_with_nested_sdk_and_business_spans(world, traced):
    llm = FakeLLM(*QUOTE)
    agent = Agent(world.airline, llm)
    stream_ids = {}

    async def go():
        stream = agent.stream_turn(world.alice, world.conv, USER_TEXT)
        stream_ids["run"] = stream.run_id
        events = [e async for e in stream]
        await agent.wait_idle()
        return events

    events = asyncio.run(go())
    message = assert_stream(events)
    assert message["text"] == "Here is your route."
    spans = traced.spans()
    assert len({s.context.trace_id for s in spans}) == 1  # one trace for the whole turn

    root = one(spans, "agent.turn")
    assert root.parent is None
    attrs = dict(root.attributes)
    assert attrs["ocean.run_id"] == stream_ids["run"]  # = SSE header X-Run-Id
    assert attrs["ocean.message_id"] == events[0].data["id"]  # = app.db llm_calls / tool_calls rows
    assert attrs["ocean.conversation_hash"] == tracing.digest(world.conv) != world.conv
    assert attrs["ocean.outcome"] == "completed" and "ocean.error_code" not in attrs
    assert attrs["ocean.budget.steps"] == 2 and attrs["ocean.budget.tool_calls"] == 1

    run = one(spans, "invoke_agent ocean-above")  # Pydantic AI's agent run span
    assert parent_of(spans, run) is root
    # the SDK groups by the conversation hash (Phoenix sessions); it never reaches the wire
    assert run.attributes["gen_ai.conversation.id"] == attrs["session.id"] == attrs["ocean.conversation_hash"]
    assert llm.bodies and attrs["ocean.conversation_hash"] not in json.dumps(llm.bodies)
    assert root.attributes["openinference.span.kind"] == "CHAIN"
    assert one(spans, "core.routing").attributes["openinference.span.kind"] == "CHAIN"
    chats = [s for s in spans if s.name.startswith("chat ")]
    assert len(chats) == 2 and all(parent_of(spans, c) is run for c in chats)
    tool = one(spans, "execute_tool quote_route")
    assert parent_of(spans, tool) is run
    assert (tool.attributes["ocean.tool.code"], tool.attributes["ocean.tool.cards"],
            tool.attributes["ocean.tool.refused"]) == ("ok", 1, False)
    quote = one(spans, "airline.quote_route")
    assert parent_of(spans, quote) is tool
    assert (quote.attributes["ocean.result.code"], quote.attributes["ocean.quote.legs"]) == ("ok", len(
        message["cards"][0]["legs"]))
    routing = one(spans, "core.routing")
    assert parent_of(spans, routing) is quote
    assert routing.attributes["ocean.network.fingerprint"] == world.networks.current().fingerprint.id
    assert routing.attributes["ocean.routing.algorithm"] == "hub-candidates+enumeration"
    assert routing.attributes["ocean.routing.code"] == "ok"
    assert routing.attributes["ocean.routing.legs"] == quote.attributes["ocean.quote.legs"]
    assert routing.attributes["ocean.routing.candidates"] >= 1
    guards = by_name(spans, "reply_guard")
    assert guards and all(g.attributes["ocean.guard.result"] == "pass" for g in guards)
    assert all(g.attributes["openinference.span.kind"] == "GUARDRAIL" for g in guards)
    # no duplicate spans for one operation
    assert sorted({s.name for s in spans}) == sorted([
        "agent.turn", "invoke_agent ocean-above", "chat fake-llm", "execute_tool quote_route",
        "airline.quote_route", "core.routing", "reply_guard"])


def test_booking_spans_carry_the_status_only(world, traced):
    agent = Agent(world.airline, FakeLLM(*QUOTE))
    events = run_turn(agent, world.alice, world.conv, USER_TEXT)
    card = assert_stream(events)["cards"][0]
    traced.spans()
    traced.memory.clear()
    from app.airline import BookingError

    with pytest.raises(BookingError):
        world.airline.book(world.alice, card["id"], "n_wrong")
    world.airline.book(world.alice, card["id"], card["state"]["nonce"])
    spans = traced.spans()
    books = by_name(spans, "airline.book")
    assert [b.attributes["ocean.book.status"] for b in books] == ["invalid_nonce", "booked"]
    assert all(b.parent is None for b in books)  # the click is its own trace (an HTTP request)
    recheck = one(spans, "booking.recheck")
    assert parent_of(spans, recheck) is books[1]
    assert recheck.attributes["ocean.recheck.result"] == "same"
    assert recheck.attributes["ocean.network.fingerprint"] == world.networks.current().fingerprint.id
    assert parent_of(spans, one(spans, "core.routing")) is recheck  # B6 recomputes the route


def test_a_changed_network_shows_the_recheck_reason_category_without_city_names(world, traced):
    agent = Agent(world.airline, FakeLLM(*QUOTE))
    card = assert_stream(run_turn(agent, world.alice, world.conv, USER_TEXT))["cards"][0]
    world.edit_db(("UPDATE locations SET coordinate = '1,1' WHERE city_name = ?", ("Chengdu",)))
    from app.airline import BookingError

    with pytest.raises(BookingError) as refused:
        world.airline.book(world.alice, card["id"], card["state"]["nonce"])
    assert refused.value.code == "stale"
    recheck = by_name(traced.spans(), "booking.recheck")[-1]
    assert recheck.attributes["ocean.recheck.result"] == "changed"
    assert recheck.attributes["ocean.recheck.reason"] == "coordinate changed"
    assert "Chengdu" not in dump(traced.spans())


# ============================================================ privacy ===========================
def test_privacy_scan_over_http_finds_no_sensitive_value(world, traced, monkeypatch):
    """The whole flow through the HTTP API with a real user (Google-style, with an email): login
    cookie, a chat turn that quotes, the confirm click with its nonce. No exported value contains
    the user's text, email, name, ids, the cookie, the nonce, keys, the reply, quote / ticket ids
    or the cities of the route."""
    monkeypatch.setattr(tracing, "build", lambda *a, **k: traced.provider)
    user = world.store.upsert_user("google", "sub-4711", "Alice Privateperson", email="alice.private@example.com")
    llm = FakeLLM(*QUOTE)
    settings = Settings(app_db=world.dir / "app.db", session_secret=SECRET_KEY, nonce_secret=SECRET,
                        ping_interval=0.01, tracing=True, google_client_id="example-client.apps.example-google.test")
    conv = world.store.create_conversation(user["id"])["id"]
    cookie = session.issue(SECRET_KEY, user["id"], 3600)
    with TestClient(create_app(world.db, settings, clock=world.clock, llm=llm)) as client:
        assert client.app.state.tracer_provider is traced.provider
        client.cookies.set(session.COOKIE, cookie)
        res = client.post(f"/api/conversations/{conv}/messages", json={"text": USER_TEXT})
        assert res.status_code == 200
        run_id = res.headers.get("x-run-id")
        done = [json.loads(b.split("data: ", 1)[1]) for b in res.text.split("\n\n") if b.startswith("event: done")]
        card = done[0]["message"]["cards"][0]
        nonce = card["state"]["nonce"]
        booked = client.post(f"/api/quotes/{card['id']}/book", json={"nonce": nonce})
        assert booked.status_code == 200, booked.text
        ticket = booked.json()["ticket"]
    spans = traced.memory.get_finished_spans()  # flushed by the app's shutdown
    names_seen = {s.name for s in spans}
    assert {"agent.turn", "airline.quote_route", "core.routing", "airline.book", "booking.recheck"} <= names_seen
    text = dump(spans)
    assert run_id and run_id in text  # the correlation id is there on purpose
    needles = {
        "user text": USER_TEXT, "user text part": "my email is", "email": "alice.private@example.com",
        "email local part": "alice.private", "user name": "Privateperson", "user id": user["id"],
        "provider sub": "sub-4711", "conversation id": conv, "cookie": cookie, "nonce": nonce,
        "nonce body": nonce[2:], "session secret": SECRET_KEY.decode(), "nonce secret": SECRET.decode(errors="ignore"),
        "llm key": llm.config.api_key, "reply": "Here is your route.", "quote id": card["id"],
        "ticket id": ticket["id"], "origin": "Shanghai", "destination": "Chengdu",
        "price": str(card["price"]), "distance": str(card["distance"]), "price shown": card["price_display"],
        "distance shown": card["distance_display"],
    }
    leaked = {label for label, needle in needles.items() if needle and needle in text}
    assert not leaked, leaked


def test_exception_messages_and_status_descriptions_are_redacted(world, traced):
    """A failure inside a span is exported with its type only: a message can quote anything."""
    def broken(*args, **kwargs):  # raised inside the SDK's tool span (writing the tool_calls row)
        raise RuntimeError("leaked alice.private@example.com sk-live-0123")
    world.store.add_tool_call = broken
    agent = Agent(world.airline, FakeLLM(*QUOTE))
    events = run_turn(agent, world.alice, world.conv, USER_TEXT)
    assert names(events)[-1] in ("done", "error")
    spans = traced.spans()
    text = dump(spans)
    assert "RuntimeError" in text  # the type is kept
    assert "alice.private" not in text and "sk-live" not in text
    for s in spans:
        assert s.status.description is None
        for e in s.events:
            if e.name == "exception":
                assert set(e.attributes) <= {"exception.type"}


def test_content_keys_are_dropped_even_if_content_capture_were_on():
    memory = InMemorySpanExporter()
    provider = tracing.build({}, exporter=memory)
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("x", attributes={"tool_arguments": "secret", "gen_ai.tool.call.result": "s",
                                                        "ok": 1}):
        pass
    provider.force_flush()
    assert dict(memory.get_finished_spans()[0].attributes) == {"ok": 1}
    provider.shutdown()


# ============================================================ failure modes =====================
def _quote_and_book(tmp_path, name):
    world = World(tmp_path / name, real=True)
    agent = Agent(world.airline, FakeLLM(*QUOTE))
    events = run_turn(agent, world.alice, world.conv, USER_TEXT)
    message = assert_stream(events)
    card = message["cards"][0]
    booked = world.airline.book(world.alice, card["id"], card["state"]["nonce"])
    return {"events": names(events), "text": message["text"], "price": card["price"],
            "legs": [(leg["from"], leg["to"]) for leg in card["legs"]],
            "ticket": (booked["ticket"]["price_display"], booked["ticket"]["legs"]), "tickets": world.ticket_count()}


class Raising:
    def __init__(self):
        self.calls = 0

    def export(self, spans):
        self.calls += 1
        raise ConnectionError("collector exploded")

    def shutdown(self):
        pass


def test_an_exporter_that_raises_changes_no_chat_or_booking_result(tmp_path):
    baseline = _quote_and_book(tmp_path, "off")
    exporter = Raising()
    provider = tracing.build({}, exporter=exporter)
    before = tracing.use(provider)
    try:
        traced_run = _quote_and_book(tmp_path, "on")
        provider.force_flush()
    finally:
        tracing.use(before)
        provider.shutdown()
    assert exporter.calls >= 1  # the export really ran, and failed
    assert traced_run == baseline


def test_a_collector_that_is_down_changes_no_chat_or_booking_result(tmp_path):
    """Real OTLP/HTTP exporter to a closed port: spans are dropped in the background, the turn
    and the booking are as fast and as correct as without tracing."""
    baseline = _quote_and_book(tmp_path, "off")
    provider = tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"})
    before = tracing.use(provider)
    try:
        started = time.monotonic()
        traced_run = _quote_and_book(tmp_path, "on")
        elapsed = time.monotonic() - started
    finally:
        tracing.use(before)
        stopped = time.monotonic()
        provider.shutdown()  # bounded by the export timeout
        shutdown_s = time.monotonic() - stopped
    assert traced_run == baseline
    assert elapsed < 3.0  # nothing waits for the collector
    assert shutdown_s < tracing.EXPORT_TIMEOUT_S + 5


def test_a_broken_tracing_layer_never_reaches_the_turn(world):
    class Broken:
        def get_tracer(self, *a, **k):
            raise RuntimeError("tracing broke")
    before = tracing.use(Broken())
    try:
        agent = Agent(world.airline, FakeLLM(*QUOTE), tracer_provider=None)
        message = assert_stream(run_turn(agent, world.alice, world.conv, USER_TEXT))
    finally:
        tracing.use(before)
    assert message["text"] == "Here is your route." and message["cards"][0]["state"]["bookable"]


# ============================================================ outcomes ==========================
def test_stop_shows_run_stop_under_a_cancelled_root(world, traced):
    gate = asyncio.Event()
    llm = FakeLLM(["Let me check that. ", "Still thinking", Wait(gate)])

    async def go():
        agent = Agent(world.airline, llm)
        events = agent.stream_turn(world.alice, world.conv, USER_TEXT)
        seen = []
        async for e in events:
            seen.append(e)
            if e.name == "delta":
                assert agent.cancel_run(world.alice, world.conv, events.run_id) == "cancelling"
                break
        seen += [e async for e in events]
        await agent.wait_idle()
        return seen

    seen = asyncio.run(go())
    assert names(seen)[-1] == "cancelled"
    spans = traced.spans()
    root = one(spans, "agent.turn")
    assert root.attributes["ocean.outcome"] == "cancelled"
    stop = one(spans, "run.stop")
    assert parent_of(spans, stop) is root
    attrs = dict(stop.attributes)
    assert attrs["ocean.stop.stored"] is True and attrs["ocean.stop.stop_to_end_ms"] >= 0
    assert attrs["ocean.stop.drain_ms"] >= 0 and attrs["ocean.stop.tokens_after_stop"] >= 0
    assert stop.end_time >= stop.start_time and stop.start_time >= root.start_time


def test_a_guard_fallback_is_the_fallback_outcome_with_the_guard_category(world, traced):
    agent = Agent(world.airline, FakeLLM(*QUOTE[:1], ["It costs 123456 dollars."]))
    message = assert_stream(run_turn(agent, world.alice, world.conv, USER_TEXT))
    assert message["text"] != "It costs 123456 dollars."
    spans = traced.spans()
    root = one(spans, "agent.turn")
    assert (root.attributes["ocean.outcome"], root.attributes["ocean.fallback_kind"]) == ("fallback", "number")
    failed = [g for g in by_name(spans, "reply_guard") if g.attributes["ocean.guard.result"] == "fail"]
    assert len(failed) == 1 and tuple(failed[0].attributes["ocean.guard.problems"]) == ("number",)
    assert "123456" not in dump(spans)


def test_a_failed_llm_is_the_failed_outcome_with_each_attempt_as_an_event(world, traced):
    error = LLMError("upstream 503", retryable=True)
    agent = Agent(world.airline, FakeLLM(error, error, error), backoff=0)
    events = run_turn(agent, world.alice, world.conv, USER_TEXT)
    assert events[-1].name == "error" and events[-1].data["code"] == "llm_unavailable"
    root = one(traced.spans(), "agent.turn")
    assert (root.attributes["ocean.outcome"], root.attributes["ocean.error_code"]) == ("failed", "llm_unavailable")
    attempts = [e for e in root.events if e.name == "llm.attempt_failed"]
    assert [e.attributes["ocean.llm.attempt"] for e in attempts] == [1, 2, 3]
    assert [e.attributes["ocean.llm.retryable"] for e in attempts] == [True, True, True]


# ============================================================ configuration =====================
def test_endpoint_follows_the_otlp_environment_variables():
    assert tracing.traces_endpoint({}) == "http://localhost:4318/v1/traces"
    assert tracing.traces_endpoint({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://phoenix:6006/"}) == \
        "http://phoenix:6006/v1/traces"
    assert tracing.traces_endpoint({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:1",
                                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://b:2/x"}) == "http://b:2/x"


@pytest.mark.parametrize("url,local", [
    ("http://localhost:4318/v1/traces", True), ("http://127.0.0.1:6006", True), ("http://phoenix:6006", True),
    ("http://192.168.1.5:4318", True), ("http://[::1]:4318", True),
    ("https://collector.example-traces.com/v1/traces", False), ("http://8.8.8.8:4318", False),
])
def test_a_non_local_endpoint_is_flagged(url, local):
    assert tracing.endpoint_is_local(url) is local


def test_a_non_local_endpoint_logs_a_warning(caplog):
    provider = tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example-traces.com"})
    provider.shutdown()
    assert any("not a local address" in r.getMessage() for r in caplog.records)


# ============================================================ locations.db read deadline ========
def test_sql_on_locations_db_past_its_deadline_is_a_data_error(world):
    with pytest.raises(NetworkDataError, match="interrupted"):
        network._read_rows(world.db, deadline=-1.0)
    locations, links = network._read_rows(world.db)  # the normal deadline is plenty
    assert len(locations) > 0 and len(links) > 0


# ============================================================ review fixes (review-otel.md) =====
class _Recorder:
    """A tiny TCP server that records the first request line of each connection. `reply`: answer
    200 (an OTLP collector or a proxy); None: accept and never answer (a hung collector)."""

    def __init__(self, reply: bool = True):
        import socket
        import threading

        self.lines: list[str] = []
        self.reply = reply
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._held = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            data = conn.recv(65536)
            self.lines.append(data.split(b"\r\n", 1)[0].decode(errors="replace"))
            if self.reply:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                conn.close()
            else:
                self._held.append(conn)  # never answered

    def close(self):
        self._sock.close()
        for conn in self._held:
            conn.close()


@pytest.mark.parametrize("variable", ["HTTP_PROXY", "ALL_PROXY", "HTTPS_PROXY", "http_proxy"])
def test_p1_1_a_local_collector_is_reached_directly_whatever_proxy_the_environment_names(monkeypatch, variable):
    collector, proxy = _Recorder(), _Recorder()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy",
                 "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy.port}")
    provider = tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": f"http://localhost:{collector.port}"})
    before = tracing.use(provider)
    try:
        with tracing.span("agent.turn", {"ocean.run_id": "r_probe"}):
            pass
        assert provider.force_flush(5000)
    finally:
        tracing.use(before)
        provider.shutdown()
        collector.close()
        proxy.close()
    assert proxy.lines == []
    assert collector.lines == ["POST /v1/traces HTTP/1.1"]


def test_p1_1_only_a_local_collector_ignores_the_environment_proxy(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    sessions = {}
    real = trace_exporter.OTLPSpanExporter

    def spy(*args, endpoint, session=None, **kwargs):
        sessions[endpoint] = session
        return real(*args, endpoint=endpoint, session=session, **kwargs)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", spy)
    for endpoint in ("https://collector.example-traces.com", "http://phoenix:6006"):
        tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint}).shutdown()
    assert sessions["https://collector.example-traces.com/v1/traces"] is None  # requests' default: env proxies
    assert sessions["http://phoenix:6006/v1/traces"].trust_env is False


def test_p2_1_shutdown_returns_within_its_bound_when_the_collector_hangs():
    """2000 spans queued (4 batches) and a collector that accepts but never answers: the SDK alone
    waits one export timeout per batch (20 s); `tracing.shutdown` returns within its bound."""
    hung = _Recorder(reply=False)
    provider = tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{hung.port}"})
    before = tracing.use(provider)
    try:
        for i in range(2000):
            with tracing.span("core.routing", {"i": i}):
                pass
        started = time.monotonic()
        finished = tracing.shutdown(timeout=1.0)
        took = time.monotonic() - started
    finally:
        tracing.use(before)
        hung.close()
    assert finished is False and took < 2.0
    assert tracing.provider() is None


def test_p2_1_shutdown_with_a_healthy_collector_finishes_and_flushes():
    collector = _Recorder()
    provider = tracing.build({"OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.port}"})
    tracing.use(provider)
    try:
        with tracing.span("agent.turn"):
            pass
        assert tracing.shutdown() is True
    finally:
        tracing.use(None)
        collector.close()
    assert collector.lines == ["POST /v1/traces HTTP/1.1"]


def _turn_ends_in_time(agent, world, timeout=10.0):
    async def go():
        stream = agent.stream_turn(world.alice, world.conv, USER_TEXT)

        async def consume():
            return [e async for e in stream]
        events = await asyncio.wait_for(consume(), timeout)  # a stream that never ends fails here
        await asyncio.wait_for(agent.wait_idle(), timeout)
        return events
    return asyncio.run(go())


def test_p2_2_a_failing_trace_end_keeps_the_terminal_event_retirement_lock_and_slots(world, traced, monkeypatch):
    from app.agent import loop

    def broken(*args, **kwargs):
        raise RuntimeError("trace end broke")
    monkeypatch.setattr(loop, "_trace_end", broken)
    agent = Agent(world.airline, FakeLLM(*QUOTE))
    events = _turn_ends_in_time(agent, world)
    assert names(events)[-1] == "done" and assert_stream(events)["cards"][0]["state"]["bookable"]
    assert agent._runs == {} and agent._running == {} and agent._user_turns == {}
    assert agent.slots.active == 0 and not world.airline.locks.locked(world.conv)
    assert one(traced.spans(), "agent.turn").parent is None  # the span still ended


def _flow(tmp_path, name, **agent_kwargs):
    """Every path that carries our spans: a quote turn, a wrong-nonce click, a `book_async`
    booking, a failed LLM turn, a guard fallback, a Stop. Returns what the user gets."""
    from app.airline import BookingError

    world = World(tmp_path / name, real=True)
    agent = Agent(world.airline, FakeLLM(*QUOTE), **agent_kwargs)
    first = _turn_ends_in_time(agent, world)
    card = assert_stream(first)["cards"][0]
    try:
        world.airline.book(world.alice, card["id"], "n_wrong")
        wrong = "no error"
    except BookingError as exc:
        wrong = exc.code
    booked = asyncio.run(world.airline.book_async(world.alice, card["id"], card["state"]["nonce"]))
    error = LLMError("upstream 503", retryable=True)
    failed = _turn_ends_in_time(Agent(world.airline, FakeLLM(error, error, error), backoff=0, **agent_kwargs), world)
    fallback = _turn_ends_in_time(Agent(world.airline, FakeLLM(QUOTE[0], ["It costs 123456 dollars."]),
                                        **agent_kwargs), world)
    gate = asyncio.Event()
    stopper = Agent(world.airline, FakeLLM(["Let me check that. ", "Still", Wait(gate)]), **agent_kwargs)

    async def stop():
        stream = stopper.stream_turn(world.alice, world.conv, "and back?")
        seen = []
        async for e in stream:
            seen.append(e.name)
            if e.name == "delta":
                stopper.cancel_run(world.alice, world.conv, stream.run_id)
        await stopper.wait_idle()
        return seen
    stopped = asyncio.run(asyncio.wait_for(stop(), 10))
    return {"first": names(first), "wrong": wrong, "ticket": booked["ticket"]["price_display"],
            "tickets": world.ticket_count(), "failed": [(e.name, e.data.get("code")) for e in failed],
            "fallback": names(fallback),
            "fallback_text": assert_stream(fallback)["text"], "stopped": stopped,
            "left": (agent._runs, stopper._runs, stopper.slots.active), "locked": world.airline.locks.locked(world.conv)}


@pytest.mark.parametrize("methods", [
    ("set_attribute", "set_attributes", "add_event", "end", "record_exception", "set_status", "is_recording"),
    # spans still look recording, so `current()` hands them out and every call reaches them
    ("set_attribute", "set_attributes", "add_event", "end", "record_exception", "set_status"),
], ids=["all", "all-but-is_recording"])
def test_p2_3_every_span_method_raising_changes_no_result(tmp_path, monkeypatch, methods):
    """The reviewer's probe A as a test: every method of a real SDK span raises (set_attribute(s),
    add_event, end, record_exception, set_status, and so __exit__). Our spans only: with
    `tracer_provider=None` the SDK's own spans are off (their failures are the SDK's)."""
    from opentelemetry.sdk import trace as sdk_trace

    baseline = _flow(tmp_path, "off")
    provider = tracing.build({}, exporter=InMemorySpanExporter())
    before = tracing.use(provider)

    def boom(*args, **kwargs):
        raise RuntimeError("span method broke")
    for method in methods:
        monkeypatch.setattr(sdk_trace.Span, method, boom)
    try:
        broken = _flow(tmp_path, "on", tracer_provider=None)
    finally:
        monkeypatch.undo()
        tracing.use(before)
        provider.shutdown()
    assert broken == baseline
    assert baseline["failed"][-1] == ("error", "llm_unavailable")
    assert baseline["wrong"] == "invalid_nonce" and baseline["tickets"] == 1 and baseline["stopped"][-1] == "cancelled"


def test_p2_2_a_failing_attribute_computation_changes_no_booking_result(world, traced, monkeypatch):
    """`_reason_kind` runs inside the booking transaction: if it raised, the stale verdict must
    still be stored and returned as before."""
    from app.airline import BookingError, service

    def broken(problem):
        raise RuntimeError("attribute computation broke")
    monkeypatch.setattr(service, "_reason_kind", broken)
    card = assert_stream(run_turn(Agent(world.airline, FakeLLM(*QUOTE)), world.alice, world.conv, USER_TEXT))["cards"][0]
    world.edit_db(("UPDATE locations SET coordinate = '1,1' WHERE city_name = ?", ("Chengdu",)))
    with pytest.raises(BookingError) as refused:
        world.airline.book(world.alice, card["id"], card["state"]["nonce"])
    assert refused.value.code == "stale" and world.status(card["id"]) == "stale"
    recheck = by_name(traced.spans(), "booking.recheck")[-1]
    assert "ocean.recheck.reason" not in recheck.attributes  # dropped, not raised


def test_p2_3_a_cancellation_through_our_span_ends_it_and_restores_the_context(traced):
    from opentelemetry import trace

    async def same_task():
        with tracing.span("agent.turn"):
            outer_id = trace.get_current_span().get_span_context().span_id
            with pytest.raises(asyncio.CancelledError):
                with tracing.span("booking.recheck"):
                    raise asyncio.CancelledError()  # a BaseException, not an Exception
            assert trace.get_current_span().get_span_context().span_id == outer_id
    asyncio.run(same_task())
    spans = traced.spans()
    assert one(spans, "booking.recheck").end_time <= one(spans, "agent.turn").end_time
    traced.memory.clear()

    async def go():
        with tracing.span("agent.turn") as outer:
            outer_id = trace.get_current_span().get_span_context().span_id

            async def inner():
                with tracing.span("run.stop"):
                    await asyncio.sleep(10)
            task = asyncio.ensure_future(inner())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert trace.get_current_span().get_span_context().span_id == outer_id
        assert not trace.get_current_span().is_recording()
    asyncio.run(go())
    stop = one(traced.spans(), "run.stop")  # exported at all: it was ended
    assert stop.end_time is not None and stop.status.description is None
    assert all(set(e.attributes) <= {"exception.type"} for e in stop.events)


def test_p2_3_an_exception_through_our_own_span_is_exported_with_its_type_only(world, traced):
    """NotFound(conversation_id) crosses `airline.quote_route`; the OTel SDK would write
    "NotFound: c_..." into the status description and the exception event."""
    from app.store import NotFound

    with pytest.raises(NotFound):
        world.airline.quote_route(world.bob, world.conv, "Shanghai", "Chengdu")
    spans = traced.spans()
    quote = one(spans, "airline.quote_route")
    assert str(quote.status.status_code) == "StatusCode.ERROR" and quote.status.description is None
    assert [dict(e.attributes) for e in quote.events] == [{"exception.type": "app.store.db.NotFound"}]
    assert world.conv not in dump(spans) and world.bob not in dump(spans)


def test_p2_3_the_http_click_records_its_status_through_book_async(world, traced, monkeypatch):
    monkeypatch.setattr(tracing, "build", lambda *a, **k: traced.provider)
    settings = Settings(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw", session_secret=SECRET_KEY,
                        nonce_secret=SECRET, ping_interval=0.01, tracing=True)
    with TestClient(create_app(world.db, settings, clock=world.clock, llm=FakeLLM(*QUOTE))) as client:
        client.cookies.set(session.COOKIE, session.issue(SECRET_KEY, world.alice, 3600))
        res = client.post(f"/api/conversations/{world.conv}/messages", json={"text": USER_TEXT})
        done = [json.loads(b.split("data: ", 1)[1]) for b in res.text.split("\n\n") if b.startswith("event: done")]
        card = done[0]["message"]["cards"][0]
        assert client.post(f"/api/quotes/{card['id']}/book", json={"nonce": "n_wrong"}).status_code == 409
        assert client.post(f"/api/quotes/{card['id']}/book", json={"nonce": card["state"]["nonce"]}).status_code == 200
    books = by_name(traced.memory.get_finished_spans(), "airline.book")
    assert [b.attributes["ocean.book.status"] for b in books] == ["invalid_nonce", "booked"]


def test_p2_7_a_cancelled_click_is_recorded_as_interrupted(world, traced):
    card = assert_stream(run_turn(Agent(world.airline, FakeLLM(*QUOTE)), world.alice, world.conv, USER_TEXT))["cards"][0]

    async def go():
        async with world.airline.locks.hold_async(world.conv, timeout=1):  # a running turn
            click = asyncio.ensure_future(world.airline.book_async(world.alice, card["id"], card["state"]["nonce"]))
            await asyncio.sleep(0.05)
            click.cancel()
            with pytest.raises(asyncio.CancelledError):
                await click
    asyncio.run(go())
    book = one(traced.spans(), "airline.book")
    assert book.attributes["ocean.book.status"] == "interrupted"
    assert world.ticket_count() == 0


def test_p2_4_a_tool_name_the_model_invented_is_exported_as_unknown_tool(world, traced):
    llm = FakeLLM([Call("exfiltrate_alice_private_example_com", x=1)], ["Here is your route."])
    events = run_turn(Agent(world.airline, llm), world.alice, world.conv, USER_TEXT)
    assert names(events)[-1] == "done"
    text = dump(traced.spans())
    assert "exfiltrate" not in text and "alice_private" not in text
    assert tracing.UNKNOWN_TOOL in text


def test_p2_5_the_conversation_hash_is_keyed():
    import hashlib

    value = "c_0123456789abcdef"
    tracing.set_hash_key(b"k" * 32)
    first = tracing.digest(value)
    assert first == tracing.digest(value) and len(first) == 16
    assert first != hashlib.sha256(value.encode()).hexdigest()[:16]  # not checkable without the key
    tracing.set_hash_key(b"other-key" * 4)
    assert tracing.digest(value) != first
