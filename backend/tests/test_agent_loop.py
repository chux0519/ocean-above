"""Agent loop replayed against a scripted fake LLM (design §4, §10): tool dispatch, invalid
arguments, guard failure -> replace, MAX_STEPS, LLM errors, event order, and the proof that the
agent cannot book. The airline, app.db and locations.db (a copy of the real one) are real."""
from __future__ import annotations

import ast
import asyncio
import json
import random
import threading
from pathlib import Path

import pytest

import app.agent
from app.agent import MAX_STEPS, Agent, LLMError, encode, encode_stream
from app.agent.context import build_context
from app.agent.loop import LLM_UNAVAILABLE, MAX_USER_CHARS
from app.agent.templates import CLICK_TO_BOOK, NO_ANSWER
from app.airline import Airline, BookingError, NotFound

from .agent_helpers import (Call, FakeLLM, Finish, Think, Wait, assert_stream, final_text, last_quote_id, names,
                            run_turn, tool_results)
from .airline_helpers import FIXTURES, World, fixture_schemas

SCHEMAS = fixture_schemas()
ASK = "I want to fly from Guangzhou to Lima"
QUOTE = Call("quote_route", origin="Guangzhou", destination="Lima")


class SpyAirline(Airline):
    """Counts every attempt to book. The tests act as the user's click through `click()`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.book_calls = 0

    def book(self, *args, **kwargs):
        self.book_calls += 1
        return super().book(*args, **kwargs)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, real=True, airline_cls=SpyAirline)


def make(world, *steps, default=None, **kwargs):
    llm = FakeLLM(*steps, default=default)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    agent = Agent(world.airline, llm, sleep=sleep, **kwargs)
    agent.sleeps = sleeps
    return agent, llm


def turn(agent, world, text=ASK, user=None, conv=None):
    events = run_turn(agent, user or world.alice, conv or world.conv, text)
    return events, assert_stream(events, SCHEMAS)


def good_reply(messages):
    """A reply that copies the display values of the newest quote."""
    quote = [r for r in tool_results(messages) if "price_display" in r][-1]
    via = ", ".join(quote["transfers"])
    return [f"The cheapest route goes via {via}: ", f"{quote['distance_display']} for ",
            f"{quote['price_display']}. ", "Shall I book it?"]


def stored(world, conv=None):
    return world.store.list_messages(world.alice if (conv or world.conv) == world.conv else world.bob, conv or world.conv)


# ============================================================ the happy path, event order ====
def test_quote_turn_events_follow_the_contract(world):
    agent, llm = make(world, [QUOTE], good_reply)
    events, message = turn(agent, world)

    assert names(events) == ["user_message", "status", "card", "delta", "delta", "done"]
    assert events[0].data["text"] == ASK
    assert events[1].data == {"text": "Looking up routes…"}
    card = events[2].data["card"]
    assert card["type"] == "quote" and (card["origin"], card["destination"]) == ("Guangzhou", "Lima")
    assert card["state"] == {"status": "quoted", "bookable": False, "nonce": None, "ticket_id": None}
    # the text is released sentence by sentence and carries the record's values
    assert message["text"] == "".join(e.data["text"] for e in events if e.name == "delta")
    assert card["price_display"] in message["text"] and card["distance_display"] in message["text"]
    # `done`: the same card, now presented and bookable (the nonce exists only from here on)
    final = message["cards"][0]
    assert final["id"] == card["id"] and final["state"]["status"] == "presented"
    assert final["state"]["bookable"] is True and final["state"]["nonce"].startswith("n_")
    assert world.status(card["id"]) == "presented" and world.ticket_count() == 0

    # stored: user message, assistant message without guard problems, tool call, two LLM calls
    rows = stored(world)
    assert [(m["role"], m["text"]) for m in rows] == [("user", ASK), ("assistant", message["text"])]
    assert rows[1]["guard_problems"] is None and rows[1]["id"] == message["id"]
    calls = world.store.list_tool_calls(world.alice, rows[0]["id"])
    assert [(c["name"], c["args"], c["result"]["code"], c["error"]) for c in calls] == [
        ("quote_route", {"origin": "Guangzhou", "destination": "Lima"}, "ok", None)]
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert [c["model"] for c in logged] == ["fake-llm", "fake-llm"] and logged[0]["tokens"] == 110
    assert logged[0]["response"]["tool_calls"][0]["name"] == "quote_route"
    assert logged[1]["response"]["content"] == message["text"]
    # the conversation got its title from the first message
    assert world.store.get_conversation(world.alice, world.conv)["title"] == ASK


def test_the_stream_has_the_shape_of_the_contract_fixture(world):
    """Same event names in the same order as contract/fixtures/sse_quote_turn.txt (without its
    ping), and the wire form parses back to the same data."""
    fixture = (FIXTURES / "sse_quote_turn.txt").read_text(encoding="utf-8")
    expected = [line[len("event: "):] for line in fixture.splitlines() if line.startswith("event: ")]
    agent, _ = make(world, [QUOTE], good_reply)
    events, _ = turn(agent, world)
    assert names(events) == [n for n in expected if n != "ping"]
    for event in events:
        wire = encode(event)
        head, data, blank, end = wire.split("\n")
        assert head == f"event: {event.name}" and (blank, end) == ("", "")
        assert data.startswith("data: ") and json.loads(data[len("data: "):]) == event.data
    assert "…" in encode(events[1])  # not \u-escaped, as in the fixture

    async def wire():
        async def source():
            for e in events:
                yield e
        return "".join([chunk async for chunk in encode_stream(source())])

    assert asyncio.run(wire()) == "".join(encode(e) for e in events)


def test_request_booking_shows_the_confirm_card_and_nothing_is_booked(world):
    agent, llm = make(
        world, [QUOTE], good_reply,
        lambda m: [Call("request_booking", quote_id=last_quote_id(m))],
        ["Please check the card and click **Confirm booking**."])
    _, first = turn(agent, world)
    events, message = turn(agent, world, "yes, book it")

    assert names(events) == ["user_message", "status", "card", "delta", "delta", "done"]
    assert events[1].data == {"text": "Preparing your confirmation…"}
    assert events[4].data == {"text": "\n\n" + CLICK_TO_BOOK}  # appended by code, always
    assert message["text"].endswith(CLICK_TO_BOOK)
    card = message["cards"][0]
    assert card["type"] == "booking_request" and card["id"] == first["cards"][0]["id"]
    assert card["state"]["bookable"] and card["state"]["nonce"] != first["cards"][0]["state"]["nonce"]
    assert world.ticket_count() == 0 and world.airline.book_calls == 0  # "yes, book it" books nothing
    assert world.status(card["id"]) == "presented"

    # only the user's click (the HTTP layer's call) issues the ticket
    out = world.airline.book(world.alice, card["id"], card["state"]["nonce"])
    assert out["ticket"]["quote_id"] == card["id"] and world.ticket_count() == 1


def test_parallel_tool_calls_run_in_order_and_each_pushes_its_cards(world):
    agent, llm = make(world, [
        Call("quote_route", origin="Guangzhou", destination="Lima"),
        Call("quote_route", origin="Abu Dhabi", destination="London"),
        Call("find_city", name="san jose"),
        Call("list_my_tickets"),
    ], ["Here are the results on the cards."])
    events, message = turn(agent, world, "several things")
    assert names(events) == ["user_message", "status", "card", "status", "card", "status", "card",
                             "status", "card", "delta", "done"]
    assert [e.data["text"] for e in events if e.name == "status"] == [
        "Looking up routes…", "Looking up routes…", "Looking up the city…", "Looking up your tickets…"]
    assert [c["type"] for c in message["cards"]] == ["quote", "no_service", "ambiguous_city", "ticket_list"]
    assert message["cards"][1]["city"] == "Abu Dhabi" and len(message["cards"][1]["nearest"]) == 3
    # the second LLM call got one tool message per call, matched by id, in order
    sent = llm.requests[1]
    assert sent[-5]["role"] == "assistant" and len(sent[-5]["tool_calls"]) == 4
    assert [m["tool_call_id"] for m in sent[-4:]] == [c["id"] for c in sent[-5]["tool_calls"]]
    assert [r["code"] for r in tool_results(sent)] == ["ok", "no_service", "ambiguous_city", "ok"]


def test_a_reply_without_tools(world):
    agent, llm = make(world, ["Where would you like ", "to fly from?"])
    events, message = turn(agent, world, "I want to go to Sydney")
    assert names(events) == ["user_message", "delta", "done"]
    assert message["text"] == "Where would you like to fly from?" and message["cards"] == []
    # "Sydney" passes the guard only because the user wrote it
    assert llm.requests[0][-1] == {"role": "user", "content": "I want to go to Sydney"}
    assert llm.requests[0][0]["role"] == "system" and "2026-11-23" in llm.requests[0][0]["content"]
    assert llm.tools_seen[0] == ["find_city", "quote_route", "request_booking", "list_my_tickets"]


def test_text_before_a_tool_call_is_kept_and_separated(world):
    agent, llm = make(world, ["Let me check.", QUOTE], ["It is on the card."])
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "delta", "status", "card", "delta", "done"]
    assert message["text"] == "Let me check.\n\nIt is on the card."


# ============================================================ invalid arguments ==============
def test_invalid_arguments_go_back_to_the_llm_which_corrects_them(world):
    agent, llm = make(
        world,
        [Call("quote_route", origin="Guangzhou")],  # destination missing
        [Call("quote_route", raw='{"origin": "Guangzhou", "destination": ')],  # not JSON
        [Call("quote_route", origin="Guangzhou", destination="Lima", price="1.00")],  # a number smuggled in
        [QUOTE], good_reply)
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "status", "status", "status", "status", "card", "delta",
                             "delta", "done"]
    assert [c["type"] for c in message["cards"]] == ["quote"]
    results = tool_results(llm.requests[-1])
    assert [r["code"] for r in results] == ["invalid_arguments"] * 3 + ["ok"]
    assert "destination" in results[0]["message"] and "JSON" in results[1]["message"]
    assert "price" in results[2]["message"]
    calls = world.store.list_tool_calls(world.alice, events[0].data["id"])
    assert [bool(c["error"]) for c in calls] == [True, True, True, False]
    assert calls[1]["args"] == '{"origin": "Guangzhou", "destination": '  # raw text kept for the log
    assert len(world.sql("select id from quotes")) == 1  # refused calls created nothing


def test_an_unknown_tool_is_an_error_record_not_a_crash(world):
    agent, llm = make(world, [Call("get_weather", city="Lima")], ["I can only help with flights."])
    events, message = turn(agent, world, "weather in Lima?")
    assert names(events) == ["user_message", "status", "delta", "done"]
    assert events[1].data == {"text": "Working on it…"}
    assert tool_results(llm.requests[1])[0]["code"] == "unknown_tool"


def test_a_tool_that_fails_becomes_a_tool_failed_record(world):
    world.db.write_bytes(b"this is not a database")
    world.bump_mtime()
    agent, llm = make(world, [QUOTE], ["Flight data is unavailable right now."])
    # the guard's own city index needs the database too, so the whole turn fails cleanly
    events = run_turn(agent, world.alice, world.conv, ASK)
    assert names(events) == ["user_message", "error"]
    assert events[-1].data["code"] == "internal_error"
    assert [m["role"] for m in stored(world)] == ["user"]


# ============================================================ ReplyGuard -> replace ==========
def template_for(world, events):
    card = [e.data["card"] for e in events if e.name == "card"][0]
    via = ", ".join(card["transfers"][:-1]) + " and " + card["transfers"][-1]
    return (f"The cheapest route from Guangzhou to Lima goes via {via}: "
            f"{card['distance_display']}, {card['price_display']}.\n\n{CLICK_TO_BOOK}")


def test_a_wrong_number_replaces_the_reply_with_the_template(world):
    agent, llm = make(world, [QUOTE], ["Here is your route. ", "It costs only $99.99. ", "Book it?"])
    events, message = turn(agent, world)
    # the first sentence was already out; the second fails; the third is never sent
    assert names(events) == ["user_message", "status", "card", "delta", "replace", "done"]
    assert events[3].data == {"text": "Here is your route. "}
    template = template_for(world, events)
    assert events[4].data == {"text": template} and message["text"] == template
    assert "99.99" not in final_text(events)
    row = stored(world)[-1]
    assert row["text"] == template and row["guard_problems"] == [{"kind": "number", "value": "99.99"}]
    # the facts are still there: the card is presented and bookable
    assert message["cards"][0]["state"]["bookable"] is True
    # the rejected text is kept in the LLM call log for the evals
    rejected = world.store.list_llm_calls(world.alice, world.conv)[-1]["response"]
    assert rejected["aborted"] is True and "99.99" in rejected["content"]
    assert rejected["guard_problems"] == row["guard_problems"]


def test_a_city_from_the_llms_own_geography_replaces_the_reply(world):
    agent, llm = make(world, [Call("quote_route", origin="Abu Dhabi", destination="London")],
                      ["Abu Dhabi has no flights, but nearby Dubai does."])
    events, message = turn(agent, world, "I need a flight from Abu Dhabi to London")
    assert names(events) == ["user_message", "status", "card", "replace", "done"]
    nearest = message["cards"][0]["nearest"]
    assert "Dubai" not in message["text"] and "Dubai" not in [n["city"] for n in nearest]
    assert message["text"].startswith("Ocean Above has no flights to or from Abu Dhabi.")
    assert all(n["city"] in message["text"] and n["distance_display"] in message["text"] for n in nearest)
    assert stored(world)[-1]["guard_problems"] == [{"kind": "city", "value": "Dubai"}]


@pytest.mark.parametrize("chunks", [
    ["The price is $1,1", "60.71"],  # filled with the real values below
    ["It costs ", "PRICE", ".", " Distance: ", "DIST", "."],
])
def test_values_split_over_chunks_are_checked_whole(world, chunks):
    def reply(messages):
        quote = tool_results(messages)[-1]
        price, dist = quote["price_display"], quote["distance_display"]
        if chunks[0].startswith("The price"):
            return [f"The price is {price[:3]}", price[3:] + "."]
        return [c.replace("PRICE", price).replace("DIST", dist) for c in chunks]

    agent, _ = make(world, [QUOTE], reply)
    events, message = turn(agent, world)
    assert "replace" not in names(events) and stored(world)[-1]["guard_problems"] is None


def test_a_wrong_last_sentence_without_final_punctuation_is_caught_at_the_end(world):
    agent, _ = make(world, [QUOTE], ["All good. ", "Total 12345 km"])
    events, message = turn(agent, world)
    assert names(events)[-3:] == ["delta", "replace", "done"]
    assert "12345" not in message["text"]


def test_an_empty_reply_becomes_the_template(world):
    agent, _ = make(world, [QUOTE], ["  \n"])
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "status", "card", "replace", "done"]
    assert message["text"] == template_for(world, events)
    assert stored(world)[-1]["guard_problems"] == [{"kind": "empty_reply", "value": ""}]

    agent, _ = make(world, [])  # nothing at all, and no records either
    events, message = turn(agent, world, "hello")
    assert names(events) == ["user_message", "replace", "done"] and message["text"] == NO_ANSWER


def test_numbers_of_earlier_turns_stay_traceable(world):
    """The guard sees the tool results that are in the context, not only this turn's."""
    def recall(messages):
        return [f"The earlier quote was {tool_results(messages)[0]['price_display']}."]

    agent, _ = make(world, [QUOTE], good_reply, recall, ["It was $1.23."])
    turn(agent, world)
    events, _ = turn(agent, world, "what was the price again?")
    assert "replace" not in names(events)
    events, _ = turn(agent, world, "and again?")
    assert "replace" in names(events)


# ============================================================ MAX_STEPS ======================
def test_max_steps_ends_the_turn_with_the_template(world):
    agent, llm = make(world, default=[Call("find_city", name="Stuttgart")])
    events, message = turn(agent, world, "Do you fly to Stuttgart?")
    assert MAX_STEPS == 6 and len(llm.requests) == MAX_STEPS
    assert names(events) == ["user_message"] + ["status", "card"] * MAX_STEPS + ["replace", "done"]
    assert message["text"].startswith("Ocean Above has no flights to or from Stuttgart.")
    assert message["text"].count("Stuttgart") == 1  # the same sentence is not repeated six times
    assert stored(world)[-1]["guard_problems"] == [{"kind": "max_steps", "value": "6"}]
    assert len(message["cards"]) == MAX_STEPS

    agent, llm = make(world, default=[Call("find_city", name="Lima")], max_steps=2)
    events, message = turn(agent, world, "Lima?")
    assert len(llm.requests) == 2 and message["text"] == "Ocean Above flies to Lima."


# ============================================================ LLM errors =====================
def flaky(n, **kw):
    return [LLMError("LLM HTTP 503", retryable=True, status=503, **kw)] * n


def test_retryable_errors_are_retried_with_backoff(world):
    agent, llm = make(world, *flaky(2), ["Where to?"])
    events, message = turn(agent, world, "hi")
    assert names(events) == ["user_message", "delta", "done"] and message["text"] == "Where to?"
    assert len(llm.requests) == 3 and agent.sleeps == [0.5, 1.0]
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert [c["response"].get("error") for c in logged] == ["LLM HTTP 503", "LLM HTTP 503", None]
    assert [c["response"].get("attempt") for c in logged[:2]] == [1, 2]


def test_llm_unavailable_after_the_retries_keeps_the_user_message(world):
    agent, llm = make(world, *flaky(3))
    events = run_turn(agent, world.alice, world.conv, ASK)
    assert assert_stream(events, SCHEMAS) is None
    assert names(events) == ["user_message", "error"]
    assert events[-1].data == {"code": "llm_unavailable", "message": LLM_UNAVAILABLE}
    assert len(llm.requests) == 3 and agent.sleeps == [0.5, 1.0]
    assert [(m["role"], m["text"]) for m in stored(world)] == [("user", ASK)]

    # the user sends it again; the context now has both user messages and the turn works
    agent2, llm2 = make(world, [QUOTE], good_reply)
    events, message = turn(agent2, world)
    assert [m["role"] for m in llm2.requests[0]] == ["system", "user", "user"]
    assert [m["role"] for m in stored(world)] == ["user", "user", "assistant"]


def test_a_non_retryable_error_is_not_retried(world):
    agent, llm = make(world, LLMError("LLM HTTP 401", retryable=False, status=401))
    events = run_turn(agent, world.alice, world.conv, ASK)
    assert names(events) == ["user_message", "error"] and events[-1].data["code"] == "llm_unavailable"
    assert len(llm.requests) == 1 and agent.sleeps == []


def test_llm_failure_after_tool_results_falls_back_to_the_template(world):
    agent, llm = make(world, [QUOTE], *flaky(3))
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "status", "card", "replace", "done"]
    assert message["text"] == template_for(world, events)
    assert message["cards"][0]["state"]["bookable"] is True  # the quote is not lost
    assert stored(world)[-1]["guard_problems"][0]["kind"] == "llm_error"
    assert len(llm.requests) == 4


def test_a_stream_that_breaks_after_released_text_is_not_retried(world):
    agent, llm = make(world, [QUOTE], ["It is on the card. ", LLMError("cut", retryable=True)],
                      ["never used"])
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "status", "card", "delta", "replace", "done"]
    assert len(llm.requests) == 2 and agent.sleeps == []  # a retry would repeat text already shown
    assert message["text"] == template_for(world, events)

    # ... but one that breaks before any sentence was released is
    agent, llm = make(world, ["Where", LLMError("cut", retryable=True)], ["Where to?"])
    events, message = turn(agent, world, "hi")
    assert names(events) == ["user_message", "delta", "done"] and message["text"] == "Where to?"


def test_a_hanging_llm_call_times_out_and_counts_as_a_retryable_error(world):
    agent, llm = make(world, [Wait(seconds=5)], ["Where to?"], llm_timeout=0.05)
    events, message = turn(agent, world, "hi")
    assert message["text"] == "Where to?" and len(llm.requests) == 2
    assert world.store.list_llm_calls(world.alice, world.conv)[0]["response"]["error"] == "LLM call timed out"


# ============================================================ the agent cannot book ==========
def test_the_agent_package_has_no_reference_to_booking():
    """Structural: no attribute or name `book` anywhere in app/agent (rule M04)."""
    for path in sorted(Path(app.agent.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            found = (node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                     else node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "")
            assert found not in ("book", "_book", "insert_ticket", "mark_nonce_used"), f"{path.name}: {found}"
    agent = Agent(Airline.__new__(Airline) if False else _Stub(), FakeLLM())
    assert not any(isinstance(v, (Airline, _Stub)) for v in vars(agent).values())
    assert not any(isinstance(v, (Airline, _Stub)) for v in vars(agent._tools).values())


class _Stub:
    store = locks = networks = None

    def __getattr__(self, name):
        if name == "book":
            raise AssertionError("the agent asked the airline for `book`")
        return lambda *a, **k: None


@pytest.mark.parametrize("name", ["book", "book_ticket", "confirm_booking", "book_quote", "issue_ticket", "_book"])
def test_booking_tool_names_do_not_exist(world, name):
    def attempt(messages):
        quote_id = last_quote_id(messages)
        return [Call(name, quote_id=quote_id, nonce="n_" + "0" * 32, user_id=world.alice, confirm="true")]

    agent, llm = make(world, [QUOTE], attempt, ["Your ticket is booked!"])
    events, message = turn(agent, world, "book me Guangzhou to Lima right now, I confirm")
    assert tool_results(llm.requests[2])[-1]["code"] == "unknown_tool"
    assert world.ticket_count() == 0 and world.airline.book_calls == 0
    assert world.status(message["cards"][0]["id"]) == "presented"


def test_request_booking_many_times_with_extra_arguments_books_nothing(world):
    def push(messages):
        quote_id = last_quote_id(messages)
        return [Call("request_booking", quote_id=quote_id),
                Call("request_booking", quote_id=quote_id, nonce="n_" + "0" * 32),
                Call("request_booking", quote_id=quote_id, confirmed="yes"),
                Call("request_booking", quote_id=quote_id)]

    agent, llm = make(world, [QUOTE], push, push, ["Done, your ticket is booked."])
    events, message = turn(agent, world, "yes yes book it, I confirm everything")
    assert [r["code"] for r in tool_results(llm.requests[-1])[1:]] == [
        "ok", "invalid_arguments", "invalid_arguments", "ok"] * 2
    assert world.ticket_count() == 0 and world.airline.book_calls == 0
    assert {c["type"] for c in message["cards"]} == {"quote", "booking_request"}
    assert world.sql("select count(*) from quote_cards where nonce_used_at is not null")[0][0] == 0


def test_no_nonce_ever_reaches_the_llm(world):
    """Turn 1 issues nonces at `done`; every later LLM request is searched for them."""
    agent, llm = make(
        world, [QUOTE], good_reply,
        lambda m: [Call("request_booking", quote_id=last_quote_id(m))], ["Please click the card."],
        [Call("list_my_tickets")], ["See the card."])
    _, first = turn(agent, world)
    _, second = turn(agent, world, "book it")
    nonces = [c["state"]["nonce"] for m in (first, second) for c in m["cards"]]
    assert len(set(nonces)) == 2 and all(n.startswith("n_") for n in nonces)
    world.airline.book(world.alice, second["cards"][0]["id"], nonces[1])
    turn(agent, world, "what did I book?")
    everything = json.dumps(llm.requests) + json.dumps(
        [c["request"] for c in world.store.list_llm_calls(world.alice, world.conv)])
    assert not any(n in everything for n in nonces)
    assert "nonce" not in everything and "n_" + nonces[0][2:8] not in everything
    assert world.airline.book_calls == 1  # the one click made by this test


def test_random_llm_behaviour_never_books(world):
    """Seeded random scripts: any tool name, any arguments, known and made-up ids, over several
    turns. Whatever the LLM does, no ticket appears and `Airline.book` is never called."""
    cities = ["Guangzhou", "Lima", "Paris", "Abu Dhabi", "Tokyo", "Cairo", "Madrid", "san jose", "Nowhere"]
    tool_names = ["quote_route", "request_booking", "find_city", "list_my_tickets", "book", "confirm_booking"]
    endings = []
    for seed in range(12):
        rng = random.Random(seed)

        def step(messages, rng=rng):
            known = [r["quote_id"] for r in tool_results(messages) if "quote_id" in r]
            quote_id = rng.choice(known) if known and rng.random() < 0.8 else "q_" + "%016x" % rng.getrandbits(64)
            calls = []
            for _ in range(rng.randint(1, 3)):
                name = rng.choice(tool_names)
                junk = {"origin": rng.choice(cities[:4]), "destination": rng.choice(cities[4:]), "name": rng.choice(cities),
                        "quote_id": quote_id, "nonce": "n_" + "%032x" % rng.getrandbits(128), "city": rng.choice(cities)}
                proper = {"quote_route": ("origin", "destination"), "request_booking": ("quote_id",),
                          "find_city": ("name",), "list_my_tickets": ("city",)}.get(name, ("quote_id", "nonce"))
                keep = proper if rng.random() < 0.7 else rng.sample(sorted(junk), rng.randint(0, len(junk)))
                calls.append(Call(name, **{k: junk[k] for k in keep}))
            return calls if rng.random() < 0.85 else ["Your ticket is booked."]

        conv = world.store.create_conversation(world.alice)["id"]
        agent, llm = make(world, default=step)
        for text in ("Guangzhou to Lima", "yes book it now", "confirm", "I said book it"):
            events = run_turn(agent, world.alice, conv, text)
            assert_stream(events, SCHEMAS)
            endings.append((events[-1].name, len(events)))
            assert world.ticket_count() == 0 and world.airline.book_calls == 0, seed
    done = dict(world.sql("select name || ':' || json_extract(result, '$.code'), count(*) from tool_calls group by 1"))
    # the scripts did real work: quotes, confirm cards the user could have clicked, booking attempts
    assert sum(done.values()) > 200 and done["quote_route:ok"] >= 10 and done["request_booking:ok"] >= 5
    assert done["book:unknown_tool"] + done["confirm_booking:unknown_tool"] > 50
    assert {name for name, _ in endings} == {"done"}
    assert world.sql("select count(*) from quote_cards")[0][0] > 10  # many bookable cards were shown
    assert world.sql("select count(*) from quotes where status = 'booked'")[0][0] == 0


# ============================================================ context across turns ===========
def test_history_is_rebuilt_from_the_store_for_the_next_turn(world):
    agent, llm = make(world, [QUOTE], good_reply,
                      lambda m: [Call("request_booking", quote_id=last_quote_id(m))], ["Please click."],
                      ["You have a ticket."])
    _, first = turn(agent, world)
    quote_id = first["cards"][0]["id"]
    _, second = turn(agent, world, "book it")
    request = llm.requests[2]  # first LLM call of turn 2
    assert [m["role"] for m in request] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert request[1]["content"] == ASK and request[5]["content"] == "book it"
    assert request[2]["tool_calls"][0]["function"] == {
        "name": "quote_route", "arguments": json.dumps({"origin": "Guangzhou", "destination": "Lima"},
                                                       separators=(",", ":"))}
    assert request[3]["tool_call_id"] == request[2]["tool_calls"][0]["id"]
    result = json.loads(request[3]["content"])
    assert result["quote_id"] == quote_id and result["price_display"] == first["cards"][0]["price_display"]
    assert request[4] == {"role": "assistant", "content": first["text"]}  # the LLM's own words

    # the user clicks; the booking shows up in the next context as an event, with display values
    out = world.airline.book(world.alice, quote_id, second["cards"][0]["state"]["nonce"])
    events, third = turn(agent, world, f"is ticket {out['ticket']['id']} mine?")
    request = llm.requests[-1]
    event = [m for m in request if m["role"] == "system"][1]
    assert event["content"].startswith("[booking event] ")
    booked = json.loads(event["content"][len("[booking event] "):])
    assert booked["event"] == "ticket_booked_by_user_click"
    assert booked["ticket"]["id"] == out["ticket"]["id"] and "distance" not in booked["ticket"]
    assert booked["ticket"]["price_display"] == out["ticket"]["price_display"]
    assert "replace" not in names(events)


def test_old_turns_are_shortened_when_the_history_exceeds_the_budget(world):
    agent, llm = make(world, default=None, *[s for _ in range(3) for s in ([QUOTE], good_reply)])
    quote_ids = [turn(agent, world, f"quote number {i} please")[1]["cards"][0]["id"] for i in range(3)]
    world.store.add_message(world.alice, world.conv, "user", "next")

    full = build_context(world.store, world.alice, world.conv)
    assert all("shortened" not in json.loads(m["content"]) for m in full.messages if m["role"] == "tool")
    small = build_context(world.store, world.alice, world.conv, budget=1500)
    results = [json.loads(m["content"]) for m in small.messages if m["role"] == "tool"]
    assert [r.get("shortened", False) for r in results] == [True, True, False]
    assert [r["quote_id"] for r in results] == quote_ids  # ids are always kept
    assert "legs" not in results[0] and "legs" in results[2]
    assert small.records == results  # the guard traces to exactly what the LLM is shown
    assert [m["role"] for m in small.messages] == [m["role"] for m in full.messages]
    assert small.messages[-1] == {"role": "user", "content": "next"}


# ============================================================ stream mechanics ===============
def test_pings_while_the_llm_is_slow(world):
    agent, _ = make(world, [Wait(seconds=0.2), "Where to?"], ping_interval=0.03)
    events, message = turn(agent, world, "hi")
    assert names(events)[0] == "user_message" and names(events)[-2:] == ["delta", "done"]
    assert names(events).count("ping") >= 2 and message["text"] == "Where to?"


def test_the_turn_finishes_and_is_stored_when_the_client_disconnects(world):
    async def go():
        gate = asyncio.Event()
        agent, _ = make(world, [Wait(gate), QUOTE], good_reply)
        stream = agent.stream_turn(world.alice, world.conv, ASK)
        first = await stream.__anext__()
        await stream.aclose()  # the browser went away after the first event
        assert first.name == "user_message" and [m["role"] for m in stored(world)] == ["user"]
        gate.set()
        await agent.wait_idle()

    asyncio.run(go())
    rows = stored(world)
    assert [m["role"] for m in rows] == ["user", "assistant"]
    quote_id = rows[1]["cards"][0]["id"]
    assert world.status(quote_id) == "presented"
    # reloading the conversation shows the card, bookable
    card = world.airline.messages(world.alice, world.conv)[1]["cards"][0]
    assert card["state"]["bookable"] is True


def test_a_booking_click_waits_for_the_running_turn(world):
    """Chat turn and booking endpoint share the conversation lock (design §4.6, B7)."""
    quote_id, nonce = world.presented("Guangzhou", "Lima")

    async def go():
        gate = asyncio.Event()
        agent, _ = make(world, [Wait(gate), "One moment."])
        stream = agent.stream_turn(world.alice, world.conv, "hello")
        await stream.__anext__()  # the turn holds the lock now
        with pytest.raises(BookingError) as busy:
            await world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=0.05)
        assert busy.value.code == "conversation_busy"
        assert world.ticket_count() == 0
        result = {}
        clicker = threading.Thread(
            target=lambda: result.update(world.airline.book(world.alice, quote_id, nonce, lock_timeout=None)))
        clicker.start()
        await asyncio.sleep(0.05)
        assert clicker.is_alive()  # blocked behind the turn
        gate.set()
        rest = [e async for e in stream]
        await asyncio.to_thread(clicker.join, 5)
        return rest, result

    rest, result = asyncio.run(go())
    assert names(rest) == ["delta", "done"] and result["ticket"]["quote_id"] == quote_id
    assert [m["role"] for m in stored(world)] == ["assistant", "user", "assistant", "system"]


def test_two_turns_of_one_conversation_run_one_after_the_other(world):
    async def go():
        gate = asyncio.Event()
        agent, llm = make(world, [Wait(gate), "First."], ["Second."])
        a = agent.stream_turn(world.alice, world.conv, "one")
        first = await a.__anext__()
        b = agent.stream_turn(world.alice, world.conv, "two")
        await asyncio.sleep(0.05)
        assert [m["text"] for m in stored(world)] == ["one"]  # "two" is not even stored yet
        gate.set()
        return [first] + [e async for e in a], [e async for e in b]

    a, b = asyncio.run(go())
    assert final_text(a) == "First." and final_text(b) == "Second."
    assert [m["text"] for m in stored(world)] == ["one", "First.", "two", "Second."]


def test_a_turn_that_cannot_get_the_lock_reports_busy(world):
    async def go():
        gate = asyncio.Event()
        agent, _ = make(world, [Wait(gate), "First."], lock_timeout=0.05)
        a = agent.stream_turn(world.alice, world.conv, "one")
        await a.__anext__()
        b = [e async for e in agent.stream_turn(world.alice, world.conv, "two")]
        gate.set()
        await agent.wait_idle()
        return b

    events = asyncio.run(go())
    # contract: conversation_busy comes before user_message and nothing is stored
    assert names(events) == ["error"] and events[0].data["code"] == "conversation_busy"
    assert set(events[0].data) == {"code", "message"}
    assert [m["text"] for m in stored(world)] == ["one", "First."]  # "two" was not stored


# ============================================================ input checks ===================
def test_ownership_and_text_are_checked_before_anything_is_stored(world):
    agent, llm = make(world)

    async def start(user, conv, text):
        return agent.stream_turn(user, conv, text)

    for user, conv in ((world.alice, world.bob_conv), (world.alice, "c_missing")):
        with pytest.raises(NotFound):
            asyncio.run(start(user, conv, "hi"))
    for text in ("", "   \n", "x" * (MAX_USER_CHARS + 1), None):
        with pytest.raises(ValueError):
            asyncio.run(start(world.alice, world.conv, text))
    assert stored(world) == [] and stored(world, world.bob_conv) == [] and llm.requests == []


def test_users_only_see_their_own_records(world):
    """Bob's turn cannot reach Alice's quote: request_booking answers not_found."""
    alice_quote = world.quote("Guangzhou", "Lima").data["quote_id"]
    agent, llm = make(world, [Call("request_booking", quote_id=alice_quote)], ["I couldn't find that quote."])
    events, message = turn(agent, world, f"book {alice_quote}", user=world.bob, conv=world.bob_conv)
    assert tool_results(llm.requests[1])[0] == {"code": "not_found", "quote_id": alice_quote}
    assert message["cards"] == [] and world.status(alice_quote) == "quoted"


# ============================================================ thinking mode ==================
def test_reasoning_is_stored_passed_back_and_never_shown_or_guarded(world):
    """DeepSeek thinking mode: `reasoning_content` is kept with the LLM call, passed back on
    the assistant messages of later requests (same turn and later turns), never sent to the user
    and never checked as reply text - it may 'think' numbers the reply must not contain."""
    think_1 = "The user wants Guangzhou to Lima. Maybe 9000 km via Dubai? Call the tool."
    think_2 = "Result is in. 4 legs. Do not compute; roughly $1,200 but copy the display value."
    agent, llm = make(world, [Think(think_1), QUOTE], lambda m: [Think(think_2), *good_reply(m)],
                      [Think("Tickets question."), Call("list_my_tickets")], ["See the card."])
    events, message = turn(agent, world)
    assert "replace" not in names(events)  # 9000, Dubai, $1,200 in the reasoning are not reply text
    shown = json.dumps([e.data for e in events])
    assert "9000" not in shown and "Dubai" not in shown and "Call the tool" not in shown
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert [c["response"]["reasoning_content"] for c in logged] == [think_1, think_2]  # verbatim
    assert logged[0]["request"]["turn"] == events[0].data["id"]
    # same turn: the tool-call message goes back with its reasoning
    assert llm.requests[1][-2]["reasoning_content"] == think_1 and llm.requests[1][-2]["tool_calls"]

    # next turn: both assistant messages of turn 1 carry their reasoning again
    turn(agent, world, "what are my tickets?")
    history = llm.requests[2]
    assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert history[2]["reasoning_content"] == think_1 and history[2]["content"] == ""
    assert history[4] == {"role": "assistant", "content": message["text"], "reasoning_content": think_2}
    assert "reasoning_content" not in history[3] and "reasoning_content" not in history[1]


def test_a_template_reply_carries_no_reasoning_into_the_history(world):
    """The reasoning belongs to text the user never saw; it is not passed back."""
    agent, llm = make(world, [Think("plan"), QUOTE], [Think("I will say 99.99"), "It costs $99.99."],
                      ["Anything else?"])
    events, message = turn(agent, world)
    assert "replace" in names(events)
    turn(agent, world, "ok")
    history = llm.requests[2]
    assert history[2]["reasoning_content"] == "plan"
    assert history[4] == {"role": "assistant", "content": message["text"]}
    assert "99.99" not in json.dumps(history)


def test_parallel_tool_calls_keep_their_grouping_in_the_history(world):
    agent, llm = make(world, [Think("two lookups"), Call("find_city", name="Lima"), Call("find_city", name="Paris")],
                      [Call("find_city", name="Tokyo")], ["All three are served."], ["Ok."])
    turn(agent, world, "Lima, Paris, Tokyo?")
    turn(agent, world, "thanks")
    history = llm.requests[3]
    assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "tool", "assistant", "tool",
                                            "assistant", "user"]
    assert len(history[2]["tool_calls"]) == 2 and history[2]["reasoning_content"] == "two lookups"
    assert [m["tool_call_id"] for m in history[3:5]] == [c["id"] for c in history[2]["tool_calls"]]
    assert "reasoning_content" not in history[5]


def test_reasoning_effort_can_be_set_per_request(world):
    agent, llm = make(world, ["Where to?"], ["Where to?"], [QUOTE], ["On the card."])
    run_turn(agent, world.alice, world.conv, "hi")
    assert llm.efforts == [None]  # None = the client's configured default (LLM_REASONING_EFFORT)

    async def go(effort):
        events = [e async for e in agent.stream_turn(world.alice, world.conv, "hi", reasoning_effort=effort)]
        await agent.wait_idle()
        return events

    asyncio.run(go("none"))
    asyncio.run(go("max"))
    assert llm.efforts == [None, "none", "max", "max"]  # every LLM call of the turn
    logged = world.store.list_llm_calls(world.alice, world.conv)
    assert [c["request"]["reasoning_effort"] for c in logged] == [None, "none", "max", "max"]

    async def bad():
        agent.stream_turn(world.alice, world.conv, "hi", reasoning_effort="extreme")

    with pytest.raises(ValueError):
        asyncio.run(bad())


def test_a_city_the_user_named_in_an_earlier_turn_may_be_mentioned(world):
    """Real-model probe case: turn 1 "Abu Dhabi to London" (no service), turn 2 the reply offers
    a quote "to London". London is in no record, but the user brought it up."""
    agent, llm = make(world, [Call("quote_route", origin="Abu Dhabi", destination="London")],
                      ["Abu Dhabi has no flights; see the card."],
                      ["I can quote Tirana to London if you like."], ["Or from Dubai to London."])
    turn(agent, world, "Abu Dhabi to London")
    events, _ = turn(agent, world, "which cities are near?")
    assert "replace" not in names(events)
    events, _ = turn(agent, world, "hmm")
    assert stored(world)[-1]["guard_problems"] == [{"kind": "city", "value": "Dubai"}]


# ============================================================ round 02 =======================
FALSE_CLAIMS = ["Done! Your ticket from Guangzhou to Lima is booked and confirmed.",
                "I've booked it for you.", "已为您出票。", "Your booking is confirmed."]


@pytest.mark.parametrize("claim", FALSE_CLAIMS)
def test_a_false_booking_claim_never_reaches_the_user(world, claim):
    """Review #4: no tool, no click - the LLM just says it booked."""
    agent, _ = make(world, [claim])
    events, message = turn(agent, world, "book Guangzhou to Lima now")
    assert claim not in json.dumps([e.data for e in events], ensure_ascii=False)
    assert "replace" in names(events) and stored(world)[-1]["guard_problems"][0]["kind"] == "booking_claim"
    assert world.ticket_count() == 0


@pytest.mark.parametrize("claim", FALSE_CLAIMS)
def test_a_false_claim_after_request_booking_is_replaced_and_the_click_hint_stays(world, claim):
    agent, _ = make(world, [QUOTE, Call("request_booking", quote_id="PLACEHOLDER")], [claim])
    agent.llm.steps[0] = lambda m: [QUOTE]
    agent.llm.steps.insert(1, lambda m: [Call("request_booking", quote_id=last_quote_id(m))])
    events, message = turn(agent, world, "book Guangzhou to Lima now")
    assert claim not in final_text(events) and claim not in message["text"]
    assert message["text"].endswith(CLICK_TO_BOOK) and world.ticket_count() == 0


def test_after_the_users_click_the_llm_may_say_it_is_booked(world):
    agent, _ = make(world, [QUOTE], good_reply, lambda m: [Call("request_booking", quote_id=last_quote_id(m))],
                    ["Please click the card."], ["Your Guangzhou to Lima ticket is booked."])
    turn(agent, world)
    _, second = turn(agent, world, "book it")
    world.airline.book(world.alice, second["cards"][0]["id"], second["cards"][0]["state"]["nonce"])
    events, message = turn(agent, world, "is it done?")
    assert "replace" not in names(events) and message["text"] == "Your Guangzhou to Lima ticket is booked."


def test_chinese_sentences_keep_the_llms_layout(world):
    """Review #8: no blank line between CJK sentences; the reasoning still goes back."""
    agent, llm = make(world, [Think("先查。"), QUOTE],
                      lambda m: [Think("给出回复。"), "广州到利马的路线在卡片上。", "请核对。", "需要预订吗？"], ["好的。"])
    events, message = turn(agent, world, "我想从广州飞到利马")
    assert message["text"] == "广州到利马的路线在卡片上。请核对。需要预订吗？"
    assert [e.data["text"] for e in events if e.name == "delta"] == ["广州到利马的路线在卡片上。", "请核对。", "需要预订吗？"]
    turn(agent, world, "谢谢")
    assert llm.requests[2][4] == {"role": "assistant", "content": message["text"], "reasoning_content": "给出回复。"}


def test_text_of_two_steps_is_still_two_paragraphs(world):
    agent, _ = make(world, ["Let me check.", QUOTE], ["Found it. ", "See the card."])
    _, message = turn(agent, world)
    assert message["text"] == "Let me check.\n\nFound it. See the card."


@pytest.mark.parametrize("reason", ["length", "content_filter", "something_new"])
def test_a_truncated_reply_is_replaced_by_the_template(world, reason):
    """Review #9."""
    agent, _ = make(world, [QUOTE], ["The cheapest route goes via", Finish(reason)])
    events, message = turn(agent, world)
    assert names(events)[-2:] == ["replace", "done"] and message["text"] == template_for(world, events)
    assert stored(world)[-1]["guard_problems"] == [{"kind": "truncated", "value": reason}]


def test_a_plain_exception_mid_stream_after_output_falls_back_and_keeps_the_card(world):
    """Review #10: not an LLMError (a parser bug, a store error) after a card and a sentence."""
    agent, _ = make(world, [QUOTE], ["It is on the card. ", AttributeError("'list' object has no attribute 'get'")])
    events, message = turn(agent, world)
    assert names(events) == ["user_message", "status", "card", "delta", "replace", "done"]
    assert message["cards"][0]["state"]["bookable"] is True
    assert stored(world)[-1]["guard_problems"] == [{"kind": "internal_error", "value": "AttributeError"}]
    assert world.status(message["cards"][0]["id"]) == "presented"


def test_a_plain_exception_before_any_output_is_an_internal_error(world):
    agent, _ = make(world, [AttributeError("boom")])
    events = run_turn(agent, world.alice, world.conv, "hi")
    assert names(events) == ["user_message", "error"] and events[-1].data["code"] == "internal_error"
    assert [m["role"] for m in stored(world)] == ["user"]


def test_a_broken_tool_call_does_not_poison_later_requests(world):
    """Review #11: the next request carries a wire-safe version; app.db keeps the raw one."""
    agent, llm = make(world, [Call("weird tool!", raw="{not json")], ["Sorry."], ["Hello."])
    turn(agent, world, "hi")
    turn(agent, world, "again")
    for request in llm.requests[1:]:
        calls = [c for m in request if m["role"] == "assistant" for c in m.get("tool_calls", [])]
        assert [c["function"] for c in calls] == [{"name": "invalid_tool", "arguments": "{}"}]
    raw = world.store.list_tool_calls(world.alice, stored(world)[0]["id"])[0]
    assert (raw["name"], raw["args"]) == ("weird tool!", "{not json")


def test_the_history_has_a_hard_limit_and_keeps_the_ids(world):
    """Review #14."""
    steps = [s for _ in range(6) for s in ([QUOTE], good_reply)]
    agent, _ = make(world, *steps)
    quote_ids = [turn(agent, world, f"quote {i} " + "x" * 900)[1]["cards"][0]["id"] for i in range(6)]
    world.store.add_message(world.alice, world.conv, "user", "next")
    ctx = build_context(world.store, world.alice, world.conv, budget=500, hard_limit=3000)
    assert len(json.dumps(ctx.messages)) < 6000
    assert ctx.messages[0]["role"] == "system" and ctx.messages[0]["content"].startswith("[earlier turns omitted]")
    summary = ctx.records[0]
    assert summary["omitted_turns"] >= 3 and set(summary["quote_ids"]) | {
        json.loads(m["content"]).get("quote_id") for m in ctx.messages if m["role"] == "tool"} >= set(quote_ids)
    assert ctx.messages[-1] == {"role": "user", "content": "next"}


def test_a_turn_has_a_total_time_limit(world):
    """Review #16: slow steps add up; the turn ends with the template, the card stays bookable."""
    agent, llm = make(world, [Wait(seconds=0.08), QUOTE], default=[Wait(seconds=0.08), Call("find_city", name="Lima")],
                      turn_timeout=0.3, llm_timeout=5)
    events, message = turn(agent, world)
    assert names(events)[-2:] == ["replace", "done"] and len(llm.requests) <= 4
    assert stored(world)[-1]["guard_problems"] == [{"kind": "turn_timeout", "value": "0.3"}]
    assert message["cards"][0]["state"]["bookable"] is True

    agent, _ = make(world, [Wait(seconds=5)], turn_timeout=0.1, llm_timeout=5)
    events = run_turn(agent, world.alice, world.conv, "hi")
    assert names(events) == ["user_message", "error"] and events[-1].data["code"] == "llm_unavailable"


def test_lock_and_turn_limits_have_the_round_02_defaults(world):
    from app.agent.loop import LOCK_TIMEOUT, TURN_TIMEOUT

    agent, _ = make(world)
    assert (agent.lock_timeout, agent.turn_timeout, LOCK_TIMEOUT, TURN_TIMEOUT) == (10.0, 90.0, 10.0, 90.0)
    assert agent.turn_timeout < 15 * 60  # well inside a quote's validity
