"""The model layer (app/agent/model.py over Pydantic AI, ADR-001) against a raw
OpenAI-compatible wire: one turn through the real Agent with an httpx2 mock transport, checked on
the request body, the SSE events and the stored llm_calls. Before ADR-001 these were tests of the
hand-written httpx client (`OpenAIChatClient`); each keeps its intent (see migration/STATE.md).
Also: the env config and the tools."""
from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from app.agent import Agent, ChatModel, LLMConfig, LLMNotConfigured
from app.agent.config import DEFAULT_BASE_URL, DEFAULT_MODEL, read_env_file
from app.agent.tools import TOOL_SCHEMAS, Toolbox, slim, validate

from .airline_helpers import World

KEY = "sk-test-0123456789abcdef"
CONFIG = LLMConfig(api_key=KEY, base_url="https://llm.example-host.test/v1", model="example-model")


def sse(*chunks, done=True) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta(finish=None, **fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": finish}]}


def text_reply(*pieces):
    return sse(delta(role="assistant", content=""), *[delta(content=p) for p in pieces], delta(finish="stop"),
               {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})


class Wire:
    """One turn against `handler(request, n)` (n = index of the request in the turn)."""

    def __init__(self, tmp_path, handler, *, config=CONFIG, effort=None, text="hi", **agent_kwargs):
        self.world = World(tmp_path)
        self.bodies: list[dict] = []
        self.requests: list[httpx2.Request] = []

        def transport(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            self.bodies.append(json.loads(request.content))
            return handler(request, len(self.bodies) - 1)

        async def no_sleep(seconds):
            pass

        async def go():
            llm = ChatModel(config, transport=httpx2.MockTransport(transport))
            agent = Agent(self.world.airline, llm, sleep=no_sleep, **agent_kwargs)
            try:
                events = [e async for e in agent.stream_turn(self.world.alice, self.world.conv, text,
                                                             reasoning_effort=effort)]
                await agent.wait_idle()
                return events
            finally:
                await llm.aclose()

        self.events = asyncio.run(go())
        self.calls = [c["response"] for c in self.world.store.list_llm_calls(self.world.alice, self.world.conv)]

    @property
    def deltas(self) -> list[str]:
        return [e.data["text"] for e in self.events if e.name == "delta"]

    @property
    def last(self):
        return self.events[-1]


def test_text_stream_and_request_shape(tmp_path):
    w = Wire(tmp_path, lambda r, n: httpx2.Response(200, content=text_reply("Hello", " there.")))
    assert "".join(w.deltas) == "Hello there." and w.last.name == "done"
    done = w.calls[-1]
    assert (done["content"], done["tool_calls"], done["finish_reason"]) == ("Hello there.", [], "stop")
    assert done["usage"]["total_tokens"] == 15

    request = w.requests[0]
    assert str(request.url) == "https://llm.example-host.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = w.bodies[0]
    assert body["model"] == "example-model" and body["stream"] is True and body["temperature"] == 0
    assert body["max_tokens"] == 4096 and "max_completion_tokens" not in body
    assert body["stream_options"] == {"include_usage": True}
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"  # default
    assert body["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in body["tools"]] == [
        "find_city", "quote_route", "request_booking", "list_my_tickets"]
    assert all("strict" not in t["function"] for t in body["tools"])  # our schemas, as they are
    assert [t["function"]["parameters"] for t in body["tools"]] == [t["function"]["parameters"] for t in TOOL_SCHEMAS]
    assert KEY not in json.dumps(body)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]  # no SDK text added


def test_tool_calls_are_assembled_from_fragments(tmp_path):
    def part(index, **f):
        return delta(tool_calls=[{"index": index, **f}])

    def handler(request, n):
        if n:
            return httpx2.Response(200, content=text_reply("Done."))
        return httpx2.Response(200, content=sse(
            part(0, id="call_a", type="function", function={"name": "quote_route", "arguments": ""}),
            part(0, function={"arguments": '{"origin": "Guang'}),
            part(1, id="call_b", type="function", function={"name": "find_city", "arguments": '{"na'}),
            part(0, function={"arguments": 'zhou", "destination": "Lima"}'}),
            part(1, function={"arguments": 'me": "Lima"}'}),
            delta(finish="tool_calls")))

    w = Wire(tmp_path, handler)
    first = w.calls[0]
    assert first["finish_reason"] == "tool_calls" and first["content"] == ""
    assert first["tool_calls"] == [
        {"id": "call_a", "name": "quote_route", "arguments": '{"origin": "Guangzhou", "destination": "Lima"}'},
        {"id": "call_b", "name": "find_city", "arguments": '{"name": "Lima"}'}]
    assistant = w.bodies[1]["messages"][2]
    assert assistant["tool_calls"][0] == {"id": "call_a", "type": "function", "function": {
        "name": "quote_route", "arguments": '{"origin":"Guangzhou","destination":"Lima"}'}}
    rows = w.world.store.list_tool_calls(w.world.alice, w.events[0].data["id"])
    assert [r["name"] for r in rows] == ["quote_route", "find_city"]  # run in call order


def test_usage_is_stored_in_the_providers_shape(tmp_path):
    """llm_calls keeps DeepSeek's usage fields (the evals price cache hits and reasoning from them)."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
             "prompt_tokens_details": {"cached_tokens": 640}, "completion_tokens_details": {"reasoning_tokens": 30},
             "prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 360}
    w = Wire(tmp_path, lambda r, n: httpx2.Response(200, content=sse(
        delta(content="Hi."), delta(finish="stop"), {"choices": [], "usage": usage})))
    stored = w.calls[-1]["usage"]
    assert stored == usage
    assert w.calls[-1]["budget"]["charged_tokens"] == 1050


def test_reasoning_content_is_kept_apart_and_passed_back_within_the_turn(tmp_path):
    def handler(request, n):
        if n == 0:
            return httpx2.Response(200, content=sse(
                delta(reasoning_content="thinking..."),
                delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                   "function": {"name": "find_city", "arguments": '{"name": "Lima"}'}}]),
                delta(finish="tool_calls")))
        return httpx2.Response(200, content=sse(delta(reasoning_content="more"), delta(content="Answer."),
                                                delta(finish="stop")))

    w = Wire(tmp_path, handler)
    assert w.deltas == ["Answer."]  # reasoning is never a text delta
    assert [c["reasoning_content"] for c in w.calls] == ["thinking...", "more"]  # stored verbatim
    assistant = w.bodies[1]["messages"][2]
    assert assistant["reasoning_content"] == "thinking..." and assistant["tool_calls"]


@pytest.mark.parametrize("configured, per_request, thinking, effort", [
    ("high", None, "enabled", "high"), ("low", None, "enabled", "low"), ("max", None, "enabled", "max"),
    ("none", None, "disabled", None),
    ("high", "none", "disabled", None), ("none", "max", "enabled", "max"), ("high", "low", "enabled", "low"),
    ("omit", "high", "enabled", "high"),
])
def test_reasoning_effort_from_config_and_per_request(configured, per_request, thinking, effort):
    """DeepSeek's parameter names: `thinking: {type}` and `reasoning_effort` (docs, 2026-11-23),
    sent as `extra_body` so the request is the one of before ADR-001."""
    llm = ChatModel(LLMConfig(api_key=KEY, reasoning_effort=configured))
    extra = llm.settings(per_request)["extra_body"]
    assert extra["thinking"] == {"type": thinking} and extra.get("reasoning_effort") == effort
    with pytest.raises(ValueError):
        llm.settings("extreme")
    with pytest.raises(ValueError):
        llm.settings("omit")  # a request cannot ask for it; it is a deployment setting
    assert "thinking" not in llm.settings() or configured != "omit"


def test_the_per_request_effort_reaches_the_wire(tmp_path):
    w = Wire(tmp_path, lambda r, n: httpx2.Response(200, content=text_reply("ok")), effort="low")
    assert w.last.name == "done" and w.bodies[0]["reasoning_effort"] == "low"
    assert w.bodies[0]["thinking"] == {"type": "enabled"}


def test_thinking_field_can_be_omitted(tmp_path):
    w = Wire(tmp_path, lambda r, n: httpx2.Response(200, content=text_reply("ok")),
             config=LLMConfig(api_key=KEY, base_url="https://llm.example-host.test/v1", reasoning_effort="omit"))
    assert "thinking" not in w.bodies[0] and "reasoning_effort" not in w.bodies[0]


@pytest.mark.parametrize("status, retryable", [(500, True), (502, True), (503, True), (429, True),
                                                (400, False), (401, False), (403, False), (404, False)])
def test_http_errors(tmp_path, status, retryable):
    def handler(request, n):
        return httpx2.Response(status, json={"error": {"message": f"bad things; key {KEY} seen"}})

    w = Wire(tmp_path, handler)
    assert w.last.name == "error" and w.last.data["code"] == "llm_unavailable"
    assert len(w.bodies) == (3 if retryable else 1)  # the loop retries (2) what is retryable, only that
    errors = [c["error"] for c in w.calls]
    assert all(e.startswith(f"LLM HTTP {status}") for e in errors)
    assert KEY not in json.dumps(w.calls)  # scrubbed (and not quoted at all for 401 / 403)
    assert all(c["budget"]["charged_tokens"] == 0 for c in w.calls)  # no response body: nothing generated


def test_transport_errors_and_cut_streams_are_retryable(tmp_path):
    def timeout(request, n):
        raise httpx2.ReadTimeout("slow", request=request)

    def refused(request, n):
        raise httpx2.ConnectError("no route", request=request)

    def cut(request, n):  # stream ends without finish_reason / [DONE]
        return httpx2.Response(200, content=sse(delta(content="Hel"), done=False))

    def stream_error(request, n):
        return httpx2.Response(200, content=sse({"error": {"message": "overloaded"}}))

    for i, handler in enumerate((timeout, refused, cut, stream_error)):
        w = Wire(tmp_path / str(i), handler)
        assert len(w.bodies) == 3, handler.__name__  # retried twice
        assert w.last.name == "error" and w.last.data["code"] == "llm_unavailable", handler.__name__
        assert all(c.get("error") for c in w.calls), handler.__name__


def test_a_stream_without_finish_reason_is_not_a_reply(tmp_path):
    """Pydantic AI reports such a stream as finish_reason "stop"; the loop reads the provider's own
    value and treats it as a broken call (ADR-001)."""
    def handler(request, n):
        if n == 0:
            return httpx2.Response(200, content=sse(delta(content="Partial"), done=False))
        return httpx2.Response(200, content=text_reply("Where to?"))

    w = Wire(tmp_path, handler)
    assert w.deltas == ["Where to?"] and w.last.name == "done"  # "Partial" was never released
    assert w.calls[0]["error"] == "LLM stream ended without a finish_reason"


def test_comment_lines_are_ignored_and_one_choice_is_asked_for(tmp_path):
    """Before ADR-001 the client also skipped choices with index > 0; Pydantic AI does not, so the
    request never asks for more than one (no `n`)."""
    def handler(request, n):
        body = b": keep-alive\n\n" + sse(delta(content="mine."), delta(finish="stop"))
        return httpx2.Response(200, content=body)

    w = Wire(tmp_path, handler)
    assert w.calls[-1]["content"] == "mine." and "n" not in w.bodies[0]


def test_the_model_calls_the_server_not_the_sdk_retries(tmp_path):
    """The provider SDK's own retries are off (`max_retries=0`): every attempt is the loop's,
    logged and charged (design §4.8)."""
    w = Wire(tmp_path, lambda r, n: httpx2.Response(503), retries=0)
    assert len(w.bodies) == 1 and len(w.calls) == 1


# --- config ---------------------------------------------------------------------------------
def test_config_from_env_and_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport DEEPSEEK_API_KEY='file-key'\nLLM_MODEL=file-model\n\nBROKEN LINE\n")
    assert read_env_file(env_file) == {"DEEPSEEK_API_KEY": "file-key", "LLM_MODEL": "file-model"}
    assert read_env_file(tmp_path / "missing") == {}

    from_file = LLMConfig.from_env({"OCEAN_ENV_FILE": str(env_file)})
    assert (from_file.api_key, from_file.model, from_file.base_url, from_file.reasoning_effort) == (
        "file-key", "file-model", DEFAULT_BASE_URL, "high")
    both = LLMConfig.from_env({"OCEAN_ENV_FILE": str(env_file), "LLM_API_KEY": "env-key",
                               "DEEPSEEK_API_KEY": "other", "LLM_BASE_URL": "https://llm.example-host.test/v1/",
                               "LLM_REASONING_EFFORT": "MAX"})
    assert (both.api_key, both.base_url, both.reasoning_effort) == ("env-key", "https://llm.example-host.test/v1", "max")
    with pytest.raises(ValueError):
        LLMConfig.from_env({"LLM_API_KEY": "k", "LLM_REASONING_EFFORT": "extreme"})
    assert DEFAULT_MODEL == "deepseek-flash"
    with pytest.raises(LLMNotConfigured):
        LLMConfig.from_env({"OCEAN_ENV_FILE": str(tmp_path / "missing")})


def test_the_key_is_not_in_the_repr():
    assert KEY not in repr(CONFIG) and KEY not in str(CONFIG)


# --- tools ----------------------------------------------------------------------------------
def test_tool_schemas_take_names_and_ids_only():
    assert [t["function"]["name"] for t in TOOL_SCHEMAS] == [
        "find_city", "quote_route", "request_booking", "list_my_tickets"]
    for tool in TOOL_SCHEMAS:
        params = tool["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert all(p["type"] == "string" for p in params["properties"].values())  # no numbers in
        assert not {"nonce", "price", "distance", "confirm"} & set(params["properties"])


@pytest.mark.parametrize("name, args, problem", [
    ("quote_route", {"origin": "A"}, "missing argument(s): destination"),
    ("quote_route", {"origin": "A", "destination": "  "}, "destination must not be empty"),
    ("quote_route", {"origin": "A", "destination": 5}, "destination must be a string"),
    ("quote_route", {"origin": "A", "destination": "D", "price": "1"}, "unknown argument(s): price"),
    ("quote_route", ["A", "D"], "arguments must be a JSON object"),
    ("quote_route", {"origin": "A" * 201, "destination": "D"}, "origin is too long"),
    ("request_booking", {"quote_id": "q_1", "nonce": "n_1"}, "unknown argument(s): nonce"),
    ("find_city", {}, "missing argument(s): name"),
])
def test_invalid_arguments(name, args, problem):
    clean, why = validate(name, args)
    assert clean is None and why.startswith(problem), why


def test_valid_arguments_are_trimmed_and_empty_optionals_dropped():
    assert validate("quote_route", {"origin": " A ", "destination": "D"}) == ({"origin": "A", "destination": "D"}, None)
    assert validate("list_my_tickets", {"city": "", "ticket_id": None}) == ({}, None)
    assert validate("list_my_tickets", {"city": "Lima"}) == ({"city": "Lima"}, None)


def test_slim_drops_high_precision_values_next_to_display_values():
    card = {"distance": "1.000", "distance_display": "1.0 km", "price": "0.13", "price_display": "$0.13",
            "legs": [{"from": "A", "to": "B", "distance": "1.000", "distance_display": "1.0 km"}],
            "other": {"price": "kept: no display sibling"}}
    assert slim(card) == {"distance_display": "1.0 km", "price_display": "$0.13",
                          "legs": [{"from": "A", "to": "B", "distance_display": "1.0 km"}],
                          "other": {"price": "kept: no display sibling"}}


def test_toolbox_dispatch_payloads_and_failures(tmp_path):
    world = World(tmp_path)
    tools = Toolbox(world.airline)

    quote = tools.run(world.alice, world.conv, "quote_route", '{"origin": "A", "destination": "D"}')
    assert quote.code == "ok" and quote.error is None and quote.args == {"origin": "A", "destination": "D"}
    assert quote.payload["code"] == "ok" and quote.payload["quote_id"].startswith("q_")
    assert quote.payload["leg_count"] == len(quote.payload["legs"]) == 4
    assert quote.payload["transfer_count"] == len(quote.payload["transfers"]) == 3
    assert json.loads(quote.for_llm()) == quote.payload
    assert [c["type"] for c in quote.cards] == ["quote"]
    # what the LLM gets: display values only, no 30-decimal strings, no nonce
    text = quote.for_llm()
    assert "nonce" not in text and quote.cards[0]["price"] not in text and quote.cards[0]["distance"] not in text
    assert quote.cards[0]["price_display"] in text

    booking = tools.run(world.alice, world.conv, "request_booking", json.dumps({"quote_id": quote.payload["quote_id"]}))
    assert booking.code == "ok" and [c["type"] for c in booking.cards] == ["booking_request"]
    assert "nonce" not in booking.for_llm() and world.ticket_count() == 0

    tickets = tools.run(world.alice, world.conv, "list_my_tickets", "")
    assert tickets.code == "ok" and tickets.payload["count"] == 0 and tickets.args == {}
    city = tools.run(world.alice, world.conv, "find_city", '{"name": "U"}')
    assert city.code == "no_service" and [c["type"] for c in city.cards] == ["no_service"]

    bad_json = tools.run(world.alice, world.conv, "quote_route", '{"origin": "A", ')
    assert bad_json.code == "invalid_arguments" and bad_json.args == '{"origin": "A", ' and bad_json.error
    missing = tools.run(world.alice, world.conv, "quote_route", '{"origin": "A"}')
    assert missing.code == "invalid_arguments" and "destination" in missing.payload["message"]
    unknown = tools.run(world.alice, world.conv, "book", '{"quote_id": "q"}')
    assert unknown.code == "unknown_tool" and unknown.cards == ()
    assert unknown.payload["available"] == ["find_city", "list_my_tickets", "quote_route", "request_booking"]

    # another user's conversation (NotFound) and a broken locations.db are tool failures, not crashes
    foreign = tools.run(world.alice, world.bob_conv, "quote_route", '{"origin": "A", "destination": "D"}')
    assert foreign.code == "tool_failed" and foreign.error.startswith("NotFound")
    world.db.write_bytes(b"not a database")
    world.bump_mtime()
    broken = tools.run(world.alice, world.conv, "find_city", '{"name": "A"}')
    assert broken.code == "tool_failed" and broken.error
    assert "locations" not in broken.for_llm()  # internals stay out of the LLM's view


# --- round 02 --------------------------------------------------------------------------------
def test_unexpected_stream_content_becomes_a_retryable_failed_call(tmp_path):
    """Every malformed stream is a failed LLM call: retried, each attempt logged and charged (review
    P2-2 / P2-8 of PR #53; an SDK parser error such as the one on a JSON array chunk included)."""
    def not_an_object(request, n):
        return httpx2.Response(200, content=b'data: [1, 2]\n\n')

    def garbage(request, n):
        return httpx2.Response(200, content=b"data: {not json\n\n")

    def bad_bytes(request, n):
        return httpx2.Response(200, content=b'data: {"choices": []}\n\n\xff\xfe\n\n')

    for i, handler in enumerate((not_an_object, garbage, bad_bytes)):
        w = Wire(tmp_path / str(i), handler)
        assert w.last.name == "error" and w.last.data["code"] == "llm_unavailable", handler.__name__
        assert len(w.bodies) == 3 and len(w.calls) == 3, handler.__name__
        assert all(c["error"].startswith("LLM ") for c in w.calls), handler.__name__
        assert [m["role"] for m in w.world.store.list_messages(w.world.alice, w.world.conv)] == ["user"]


def test_a_200_response_that_is_not_a_stream_is_charged(tmp_path):
    """Review P2-2: a body was read (a gateway's HTML, a non-streamed JSON reply): it may have
    cost tokens, so it is charged by estimate, not as "no response"."""
    for i, body in enumerate((b"<html>gateway</html>", b'{"choices": [{"message": {"content": "hi"}}]}')):
        w = Wire(tmp_path / str(i), lambda r, n, b=body: httpx2.Response(200, content=b), retries=0)
        assert w.calls[0]["budget"]["estimated"] is True and w.calls[0]["budget"]["charged_tokens"] > 0


def test_outbound_headers_are_the_ones_of_before(tmp_path):
    """Review P2-6: no SDK fingerprint headers (OS, architecture, Python / package versions)."""
    w = Wire(tmp_path, lambda r, n: httpx2.Response(200, content=text_reply("ok")))
    headers = {k.lower(): v for k, v in w.requests[0].headers.items()}
    assert not [k for k in headers if k.startswith("x-stainless")]
    assert headers["user-agent"] == "ocean-above"
    assert headers["authorization"] == f"Bearer {KEY}" and headers["content-type"] == "application/json"


@pytest.mark.parametrize("cuts", [[100], [3, 7, 12, 20, 30], [1] * 80])
def test_an_inline_think_block_is_moved_out_of_the_text(tmp_path, cuts):
    """Review #15: servers that put the reasoning in `content` as <think>...</think> (the loop's
    ThinkSplitter; the SDK only splits a tag that arrives as its own chunk)."""
    raw = "  <think>The user wants 9000 km via Dubai.</think>\n\nIt is on the card."
    pieces, pos = [], 0
    for n in cuts:
        pieces.append(raw[pos:pos + n])
        pos += n
    pieces.append(raw[pos:])
    pieces = [p for p in pieces if p]

    def handler(request, n):
        return httpx2.Response(200, content=sse(*[delta(content=p) for p in pieces], delta(finish="stop")))

    w = Wire(tmp_path, handler)
    assert "".join(w.deltas) == "It is on the card." and w.calls[-1]["content"].strip() == "It is on the card."
    assert w.calls[-1]["reasoning_content"].strip() == "The user wants 9000 km via Dubai."
    assert "Dubai" not in json.dumps([e.data for e in w.events])


def test_text_without_think_and_an_unclosed_think_block(tmp_path):
    w = Wire(tmp_path / "a", lambda r, n: httpx2.Response(200, content=sse(
        delta(content="<b"), delta(content="ig> news."), delta(finish="stop"))))
    assert w.calls[-1]["content"] == "<big> news."
    w = Wire(tmp_path / "b", lambda r, n: httpx2.Response(200, content=sse(
        delta(content="<think>never ends"), delta(finish="length"))))
    done = w.calls[-1]
    assert done["content"] == "" and done["reasoning_content"] == "never ends" and done["finish_reason"] == "length"
    assert w.last.name == "done"  # cut off ("length") with no text: the template, as before
    assert w.world.store.list_messages(w.world.alice, w.world.conv)[-1]["guard_problems"] == [
        {"kind": "truncated", "value": "length"}]


def test_tool_calls_go_back_to_the_api_in_a_wire_safe_form():
    """Review #11: a broken call must not make every later request fail."""
    from app.agent.model import wire_tool_call

    assert wire_tool_call("c1", "quote_route", '{"origin": "A"}')["function"] == {
        "name": "quote_route", "arguments": '{"origin": "A"}'}
    assert wire_tool_call("c1", "weird tool!", "{not json")["function"] == {"name": "invalid_tool", "arguments": "{}"}
    assert wire_tool_call("c1", "", "[1]")["function"] == {"name": "invalid_tool", "arguments": "{}"}
    assert wire_tool_call("c1", "x" * 65, {"a": 1})["function"] == {"name": "invalid_tool", "arguments": '{"a":1}'}
    assert wire_tool_call("c1", "ok", "[" * 100_000)["function"]["arguments"] == "{}"


def test_deeply_nested_or_huge_arguments_are_refused_not_raised(tmp_path):
    world = World(tmp_path)
    tools = Toolbox(world.airline)
    deep = tools.run(world.alice, world.conv, "find_city", "[" * 100_000)
    assert deep.code == "invalid_arguments" and len(deep.args) <= 2000
    nested = tools.run(world.alice, world.conv, "find_city", "[" * 1500)
    assert nested.code == "invalid_arguments"


def test_ticket_lists_for_the_llm_are_capped_but_count_is_full():
    from app.agent.tools import MAX_TICKETS_FOR_LLM, _payload

    tickets = [{"id": f"t_{i:016x}", "price_display": "$1.00"} for i in range(MAX_TICKETS_FOR_LLM + 7)]
    out = _payload("ok", {"count": len(tickets), "filter": {}, "tickets": tickets})
    assert out["count"] == MAX_TICKETS_FOR_LLM + 7 and len(out["tickets"]) == MAX_TICKETS_FOR_LLM
    assert out["tickets_shown"] == MAX_TICKETS_FOR_LLM
