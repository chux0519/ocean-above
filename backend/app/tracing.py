"""Opt-in OpenTelemetry tracing (#35, design §4.11).

Off by default. `OTEL_TRACING_ENABLED=1` builds one TracerProvider with a batch OTLP/HTTP exporter
(endpoint from the standard `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_ENDPOINT`,
default `http://localhost:4318`). Off: the OpenTelemetry SDK is not even imported, so there is no
provider, no exporter and no background thread; `span()` is a shared no-op.

What goes into spans (privacy, design §4.11): ids that are already opaque (run id, message id),
a hash of the conversation id, codes, counts, durations, token counts and the locations.db
fingerprint. Never: the user's or the model's text, prompts, tool arguments or results, city
names, emails, user names, nonces, cookies, keys. Pydantic AI's own spans run with
`include_content=False`.

Failure: export runs in the SDK's batch thread; a collector that is down or slow only drops
spans (bounded queue). Chat and booking never wait for it, and a failing span call never
reaches the caller (`span()` swallows errors from the tracing layer itself).

The provider is process-wide state in this module (not OpenTelemetry's global provider, which
can be set only once per process): `use()` installs one, `provider()` returns it.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ENABLED_VAR = "OTEL_TRACING_ENABLED"
DEFAULT_ENDPOINT = "http://localhost:4318"
SERVICE_NAME = "ocean-above-api"
EXPORT_TIMEOUT_S = 5.0  # one OTLP request; the batch thread gives up after this
SHUTDOWN_TIMEOUT_S = 5.0  # app shutdown waits at most this long for the last spans (review P2-1)
UNKNOWN_TOOL = "unknown_tool"  # what a tool name the model invented is exported as (review P2-4)
MAX_QUEUE = 2048  # spans waiting for export; more are dropped, never waited for
SCOPE = "ocean-above"

_provider: Any = None  # the active TracerProvider, or None (tracing off)
_hash_key: bytes = secrets.token_bytes(32)  # `digest` key; the app derives a stable one (`set_hash_key`)


def enabled_in(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(ENABLED_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


def traces_endpoint(env: Mapping[str, str] | None = None) -> str:
    """The OTLP/HTTP traces URL per the OpenTelemetry spec: `..._TRACES_ENDPOINT` as is, else
    `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/traces`, else localhost."""
    env = os.environ if env is None else env
    exact = (env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or "").strip()
    if exact:
        return exact
    base = (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip() or DEFAULT_ENDPOINT
    return base.rstrip("/") + "/v1/traces"


def endpoint_is_local(url: str) -> bool:
    """Loopback, a private address, or a single-label host (a compose service such as `phoenix`)."""
    host = urlsplit(url).hostname or ""
    if host == "localhost" or (host and "." not in host and ":" not in host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def from_env(env: Mapping[str, str] | None = None, *, exporter: Any = None) -> Any:
    """A TracerProvider when tracing is enabled in `env` (default: the process environment),
    else None. `exporter` replaces the OTLP exporter (tests)."""
    env = os.environ if env is None else env
    return build(env, exporter=exporter) if enabled_in(env) else None


def build(env: Mapping[str, str] | None = None, *, exporter: Any = None) -> Any:
    """A TracerProvider exporting to the OTLP endpoint named in `env` (or to `exporter`)."""
    env = os.environ if env is None else env
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = traces_endpoint(env)
        if not endpoint_is_local(endpoint):
            log.warning("tracing: OTLP endpoint %s is not a local address; spans leave this host",
                        urlsplit(endpoint).hostname)
        session = None
        if endpoint_is_local(endpoint):
            # a local collector is reached directly, never through HTTP(S)_PROXY / ALL_PROXY from
            # the environment (review P1-1: spans would leave the host, or be lost, silently)
            import requests

            session = requests.Session()
            session.trust_env = False
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=EXPORT_TIMEOUT_S, session=session)
    exporter = Redacting(exporter)
    resource = Resource.create({"service.name": (env.get("OTEL_SERVICE_NAME") or "").strip() or SERVICE_NAME})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(
        exporter, max_queue_size=MAX_QUEUE, export_timeout_millis=int(EXPORT_TIMEOUT_S * 1000)))
    log.info("tracing: on (OTLP/HTTP)")
    return tracer_provider


# Attributes that carry message or tool content if content capture were ever switched on
# (`include_content=True`): dropped at the exporter whatever the setting.
CONTENT_KEYS = frozenset({"tool_arguments", "tool_response", "final_result", "gen_ai.tool.call.arguments",
                          "gen_ai.tool.call.result", "gen_ai.system_instructions"})


# Attributes holding message structure as JSON (roles, part types, tool names): a tool name the
# model invented is replaced there (review P2-4).
_MESSAGE_KEYS = ("gen_ai.input.messages", "gen_ai.output.messages", "pydantic_ai.all_messages")


class Redacting:
    """An exporter wrapper, the last step before spans leave the process. An exception recorded
    on a span keeps its type only (no message, no stack trace: a message can quote a provider
    response, an id or a path), the status keeps its code only, `CONTENT_KEYS` are dropped and a
    tool name that is not one of our tools (the model's own text) becomes `unknown_tool`.
    An error here drops the batch, never raises into the SDK's export thread."""

    def __init__(self, inner: Any):
        self.inner = inner

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            clean = [_redacted(s) for s in spans]
        except Exception:
            log.warning("tracing: redaction failed; %d span(s) dropped", len(spans), exc_info=True)
            return SpanExportResult.FAILURE
        return self.inner.export(clean)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flush = getattr(self.inner, "force_flush", None)
        return True if flush is None else flush(timeout_millis)


def _redacted(span: Any) -> Any:
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Status

    events = [Event(e.name, {k: v for k, v in (e.attributes or {}).items() if k == "exception.type"},
                    e.timestamp) if e.name == "exception" else e for e in span.events]
    attributes = {k: v for k, v in (span.attributes or {}).items() if k not in CONTENT_KEYS}
    known = _tool_names()
    if "gen_ai.tool.name" in attributes and attributes["gen_ai.tool.name"] not in known:
        attributes["gen_ai.tool.name"] = UNKNOWN_TOOL
    for key in _MESSAGE_KEYS:
        if isinstance(attributes.get(key), str):
            try:
                attributes[key] = json.dumps(_known_tools_only(json.loads(attributes[key]), known))
            except ValueError:
                del attributes[key]  # not the JSON we know: not exported
    return ReadableSpan(
        name=span.name, context=span.context, parent=span.parent, resource=span.resource,
        attributes=attributes, events=events, links=span.links, kind=span.kind,
        status=Status(span.status.status_code), start_time=span.start_time, end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope)


def _tool_names() -> frozenset[str]:
    from .agent.tools import TOOL_SCHEMAS  # (lazy: the agent imports this module)

    return frozenset(t["function"]["name"] for t in TOOL_SCHEMAS)


def _known_tools_only(value: Any, known: frozenset[str]) -> Any:
    if isinstance(value, list):
        return [_known_tools_only(v, known) for v in value]
    if isinstance(value, dict):
        out = {k: _known_tools_only(v, known) for k, v in value.items()}
        if isinstance(out.get("name"), str) and str(out.get("type", "")).startswith("tool_call") \
                and out["name"] not in known:
            out["name"] = UNKNOWN_TOOL
        return out
    return value


def use(tracer_provider: Any) -> Any:
    """Install `tracer_provider` (None: tracing off) for the business spans. Returns the one
    before (tests restore it)."""
    global _provider
    before, _provider = _provider, tracer_provider
    return before


def provider() -> Any:
    return _provider


def shutdown(timeout: float = SHUTDOWN_TIMEOUT_S) -> bool:
    """Flush and stop the active provider (app shutdown). Never raises, and returns within about
    `timeout` seconds even when the collector hangs (review P2-1: the SDK's own shutdown waits one
    export timeout per batch). True when it finished in time; else the rest of the queue is lost."""
    tracer_provider = use(None)
    if tracer_provider is None:
        return True

    def stop() -> None:
        try:
            tracer_provider.shutdown()
        except Exception:
            log.warning("tracing: shutdown failed", exc_info=True)

    worker = threading.Thread(target=stop, name="oa-tracing-shutdown", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log.warning("tracing: collector did not take the last spans within %gs; they are dropped", timeout)
        return False
    return True


class _NoSpan:
    """What `span()` yields when tracing is off: every call does nothing."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        pass

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        pass

    def set_attributes_from(self, compute: Any) -> None:
        pass  # off: the values are not even computed

    def is_recording(self) -> bool:
        return False


NO_SPAN = _NoSpan()


class _Span:
    """A live span behind a guard: an error inside the tracing layer is logged, never raised."""

    __slots__ = ("_span",)

    def __init__(self, span: Any):
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        try:
            self._span.set_attribute(key, value)
        except Exception:
            log.debug("tracing: set_attribute failed", exc_info=True)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def set_attributes_from(self, compute: Any) -> None:
        """`set_attributes(compute())`, with the computation itself behind the guard (review
        P2-2): a broken attribute expression can never reach business code."""
        try:
            attributes = compute()
        except Exception:
            log.debug("tracing: attribute computation failed", exc_info=True)
            return
        self.set_attributes(attributes)

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        try:
            self._span.add_event(name, {k: v for k, v in (attributes or {}).items() if v is not None})
        except Exception:
            log.debug("tracing: add_event failed", exc_info=True)

    def is_recording(self) -> bool:
        return True


# Phoenix (OpenInference) span kinds of our own spans; the SDK's spans carry GenAI semantic
# conventions, which Phoenix maps to AGENT / LLM / TOOL itself.
_KINDS = {"reply_guard": "GUARDRAIL"}


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None, *,
         start_time_ns: int | None = None) -> Iterator[Any]:
    """A child of the current span (or a new root) around the body; a no-op when tracing is off.
    An exception from the body is recorded on the span and re-raised unchanged; an error of the
    tracing layer itself never reaches the body or the caller."""
    tracer_provider = _provider
    if tracer_provider is None:
        yield NO_SPAN
        return
    try:
        tracer = tracer_provider.get_tracer(SCOPE)
        manager = tracer.start_as_current_span(
            name, attributes={"openinference.span.kind": _KINDS.get(name, "CHAIN"),
                              **{k: v for k, v in (attributes or {}).items() if v is not None}},
            start_time=start_time_ns, record_exception=True, set_status_on_exception=True)
        live = manager.__enter__()
    except Exception:
        log.debug("tracing: span %s not started", name, exc_info=True)
        yield NO_SPAN
        return
    try:
        yield _Span(live)
    except BaseException as exc:
        _exit(manager, type(exc), exc, exc.__traceback__)
        raise
    else:
        _exit(manager, None, None, None)


def _exit(manager: Any, *exc_info: Any) -> None:
    try:
        manager.__exit__(*exc_info)
    except Exception:
        log.debug("tracing: span end failed", exc_info=True)


def current() -> Any:
    """The current span (an SDK tool span, say) behind the same guard, or `NO_SPAN`."""
    if _provider is None:
        return NO_SPAN
    try:
        from opentelemetry import trace

        live = trace.get_current_span()
        return _Span(live) if live.is_recording() else NO_SPAN
    except Exception:
        return NO_SPAN


def monotonic_to_epoch_ns(moment: float) -> int:
    """A `time.monotonic()` moment as wall-clock nanoseconds (a span's start in the past)."""
    return time.time_ns() - int((time.monotonic() - moment) * 1e9)


def set_hash_key(key: bytes) -> None:
    """The key of `digest` (the app derives one from its session secret, so a conversation keeps
    its hash across restarts when SESSION_SECRET is set)."""
    global _hash_key
    _hash_key = hmac.new(key, b"ocean-above tracing conversation hash", hashlib.sha256).digest()


def digest(value: str) -> str:
    """A short keyed hash (HMAC-SHA256) of an opaque id (the conversation): traces of one
    conversation can be grouped, but without the server's key nobody can check whether a trace
    belongs to a conversation id they know (review P2-5)."""
    return hmac.new(_hash_key, value.encode(), hashlib.sha256).hexdigest()[:16]
