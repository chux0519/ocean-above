"""The OpenAI-compatible streaming client against a mock transport, the env config, the tools."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.agent import LLMConfig, LLMError, LLMNotConfigured, OpenAIChatClient, TextDelta, Completion
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


def collect(handler, messages=None, tools=TOOL_SCHEMAS, config=CONFIG):
    async def go():
        client = OpenAIChatClient(config, transport=httpx.MockTransport(handler))
        try:
            return [e async for e in client.stream(messages or [{"role": "user", "content": "hi"}], tools)]
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_text_stream_and_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            delta(role="assistant", content=""), delta(content="Hello"), delta(content=" there."),
            delta(finish="stop"),
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}))

    events = collect(handler)
    assert events[:2] == [TextDelta("Hello"), TextDelta(" there.")]
    done = events[-1]
    assert isinstance(done, Completion) and len(events) == 3
    assert (done.content, done.tool_calls, done.finish_reason, done.total_tokens) == ("Hello there.", [], "stop", 15)
    assert done.assistant_message() == {"role": "assistant", "content": "Hello there."}

    assert seen["url"] == "https://llm.example-host.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    body = seen["body"]
    assert body["model"] == "example-model" and body["stream"] is True and body["temperature"] == 0
    assert body["stream_options"] == {"include_usage": True}
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"  # default
    assert body["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in body["tools"]] == [
        "find_city", "quote_route", "request_booking", "list_my_tickets"]
    assert KEY not in json.dumps(body)


def test_tool_calls_are_assembled_from_fragments():
    def part(index, **f):
        return delta(tool_calls=[{"index": index, **f}])

    def handler(request):
        return httpx.Response(200, content=sse(
            part(0, id="call_a", type="function", function={"name": "quote_route", "arguments": ""}),
            part(0, function={"arguments": '{"origin": "Guang'}),
            part(1, id="call_b", type="function", function={"name": "find_", "arguments": '{"na'}),
            part(0, function={"arguments": 'zhou", "destination": "Lima"}'}),
            part(1, function={"name": "city", "arguments": 'me": "Lima"}'}),
            delta(finish="tool_calls")))

    done = collect(handler)[-1]
    assert done.finish_reason == "tool_calls" and done.content == ""
    assert done.tool_calls == [
        {"id": "call_a", "name": "quote_route", "arguments": '{"origin": "Guangzhou", "destination": "Lima"}'},
        {"id": "call_b", "name": "find_city", "arguments": '{"name": "Lima"}'}]
    message = done.assistant_message()
    assert message["tool_calls"][0] == {"id": "call_a", "type": "function", "function": {
        "name": "quote_route", "arguments": '{"origin": "Guangzhou", "destination": "Lima"}'}}


def test_reasoning_content_is_kept_apart_and_passed_back_within_the_turn():
    def handler(request):
        return httpx.Response(200, content=sse(
            delta(reasoning_content="thinking..."), delta(content="Answer."), delta(finish="stop")))

    events = collect(handler)
    assert events[0] == TextDelta("Answer.") and len(events) == 2  # reasoning is never a text delta
    assert events[-1].as_record()["reasoning_content"] == "thinking..."  # stored verbatim
    assert events[-1].assistant_message() == {
        "role": "assistant", "content": "Answer.", "reasoning_content": "thinking..."}


@pytest.mark.parametrize("configured, per_request, thinking, effort", [
    ("high", None, "enabled", "high"), ("low", None, "enabled", "low"), ("max", None, "enabled", "max"),
    ("none", None, "disabled", None),
    ("high", "none", "disabled", None), ("none", "max", "enabled", "max"), ("high", "low", "enabled", "low"),
    ("omit", "high", "enabled", "high"),
])
def test_reasoning_effort_from_config_and_per_request(configured, per_request, thinking, effort):
    """DeepSeek's parameter names: `thinking: {type}` and `reasoning_effort` (docs, 2026-11-23)."""
    client = OpenAIChatClient(LLMConfig(api_key=KEY, reasoning_effort=configured))
    body = client.body([{"role": "user", "content": "hi"}], TOOL_SCHEMAS, per_request)
    assert body["thinking"] == {"type": thinking} and body.get("reasoning_effort") == effort
    with pytest.raises(ValueError):
        client.body([], [], "extreme")
    with pytest.raises(ValueError):
        client.body([], [], "omit")  # a request cannot ask for it; it is a deployment setting


def test_the_per_request_effort_reaches_the_wire():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(delta(content="ok"), delta(finish="stop")))

    async def go():
        client = OpenAIChatClient(CONFIG, transport=httpx.MockTransport(handler))
        events = [e async for e in client.stream([{"role": "user", "content": "hi"}], [], reasoning_effort="low")]
        await client.aclose()
        return events

    assert asyncio.run(go())[-1].content == "ok" and seen["body"]["reasoning_effort"] == "low"


def test_thinking_field_can_be_omitted_and_tools_left_out():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(delta(content="ok"), delta(finish="stop")))

    collect(handler, tools=[], config=LLMConfig(api_key=KEY, reasoning_effort="omit"))
    assert "thinking" not in seen["body"] and "reasoning_effort" not in seen["body"]
    assert "tools" not in seen["body"] and "tool_choice" not in seen["body"]


@pytest.mark.parametrize("status, retryable", [(500, True), (502, True), (503, True), (429, True),
                                                (400, False), (401, False), (403, False), (404, False)])
def test_http_errors(status, retryable):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": f"bad things; key {KEY} seen"}})

    with pytest.raises(LLMError) as err:
        collect(handler)
    assert err.value.retryable is retryable and err.value.status == status
    assert KEY not in str(err.value)  # scrubbed (and not quoted at all for 401 / 403)


def test_transport_errors_and_cut_streams_are_retryable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("no route", request=request)

    def cut(request):  # stream ends without finish_reason / [DONE]
        return httpx.Response(200, content=sse(delta(content="Hel"), done=False))

    def garbage(request):
        return httpx.Response(200, content=b"data: {not json\n\n")

    def stream_error(request):
        return httpx.Response(200, content=sse({"error": {"message": "overloaded"}}))

    for handler in (timeout, refused, cut, garbage, stream_error):
        with pytest.raises(LLMError) as err:
            collect(handler)
        assert err.value.retryable is True, handler.__name__


def test_comment_lines_and_other_choices_are_ignored():
    def handler(request):
        body = b": keep-alive\n\n" + sse(
            {"choices": [{"index": 1, "delta": {"content": "other"}, "finish_reason": None}]},
            delta(content="mine"), delta(finish="stop"))
        return httpx.Response(200, content=body)

    assert collect(handler)[-1].content == "mine"


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
def test_unexpected_stream_content_becomes_a_retryable_llm_error():
    def not_an_object(request):
        return httpx.Response(200, content=b'data: [1, 2]\n\n')

    def bad_bytes(request):
        return httpx.Response(200, content=b'data: {"choices": []}\n\n\xff\xfe\n\n')

    for handler in (not_an_object, bad_bytes):
        with pytest.raises(LLMError) as err:
            collect(handler)
        assert err.value.retryable is True, handler.__name__


@pytest.mark.parametrize("cuts", [[100], [3, 7, 12, 20, 30], [1] * 80])
def test_an_inline_think_block_is_moved_out_of_the_text(cuts):
    """Review #15: servers that put the reasoning in `content` as <think>...</think>."""
    raw = "  <think>The user wants 9000 km via Dubai.</think>\n\nIt is on the card."
    pieces, pos = [], 0
    for n in cuts:
        pieces.append(raw[pos:pos + n])
        pos += n
    pieces.append(raw[pos:])
    pieces = [p for p in pieces if p]

    def handler(request):
        return httpx.Response(200, content=sse(*[delta(content=p) for p in pieces], delta(finish="stop")))

    events = collect(handler)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "It is on the card." and events[-1].content == text
    assert events[-1].reasoning == "The user wants 9000 km via Dubai."


def test_text_without_think_and_an_unclosed_think_block():
    def plain(request):
        return httpx.Response(200, content=sse(delta(content="<b"), delta(content="ig> news"), delta(finish="stop")))

    def unclosed(request):
        return httpx.Response(200, content=sse(delta(content="<think>never ends"), delta(finish="length")))

    assert collect(plain)[-1].content == "<big> news"
    done = collect(unclosed)[-1]
    assert done.content == "" and done.reasoning == "never ends" and done.finish_reason == "length"


def test_tool_calls_go_back_to_the_api_in_a_wire_safe_form():
    """Review #11: a broken call must not make every later request fail."""
    from app.agent.llm import wire_tool_call

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
