"""Regressions for the two real-model eval failures of 03-slice T4 (06-hardening P1, issue #34).

1. `no_service_then_nearest`: after "Austin has no service", the user says "ok, use the nearest
   city instead" and the model refused to pick. The nearest served city is now named by code in the
   tool result (`nearest_served_city`, only when it is unique), and the prompt says that a request
   for "the nearest" is the user's choice.
2. `book_in_one_message`: "Book me a flight from Madrid to Cairo" got a quote but no confirmation
   card. The prompt now asks for `request_booking` in the same turn; a ticket still needs the click.

A scripted (fake) LLM plays the good trajectory and the observed bad one; the checks must pass the
first and flag the second. Expected cities come from the brute-force reference, not from a model."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import date

import pytest

from app.agent.prompt import system_prompt
from app.agent.tools import Toolbox, _payload
from app.airline import Airline
from app.core.network import NetworkProvider
from app.store import Store
from evals.cases import CASES, Turn
from evals.fake import Use, say
from evals.report import summarize
from evals.runner import run_all

from .conftest import REAL_DB
from .reference import RefGraph

BY_NAME = {c.name: c for c in CASES}


def run(case):
    results = asyncio.run(run_all([case], 1, mode="fake"))
    return results[0], summarize([case], results, {"mode": "fake"})


def with_script(name: str, turn: int, *steps):
    case = BY_NAME[name]
    chat = [i for i, s in enumerate(case.steps) if isinstance(s, Turn)]
    steps_ = list(case.steps)
    steps_[chat[turn]] = dataclasses.replace(steps_[chat[turn]], fake=steps)
    return dataclasses.replace(case, steps=tuple(steps_))


@pytest.fixture(scope="module")
def toolbox(tmp_path_factory):
    work = tmp_path_factory.mktemp("regress")
    store = Store(work / "app.db")
    airline = Airline(store, NetworkProvider(REAL_DB))
    user = store.upsert_user("test", "regress", "Regress")["id"]
    return Toolbox(airline), user, store.create_conversation(user)["id"]


@pytest.fixture(scope="module")
def ref():
    return RefGraph.from_db(REAL_DB)


# ------------------------------------------------ 1. nearest served city after no_service ----
@pytest.mark.parametrize("origin, destination, role, unserved", [
    ("Austin", "Tokyo", "origin", "Austin"),
    ("London", "Abu Dhabi", "destination", "Abu Dhabi"),
    ("Stuttgart", "Austin", "origin", "Stuttgart"),
])
def test_nearest_served_city_in_the_quote_result_is_the_reference_nearest(toolbox, ref, origin, destination,
                                                                          role, unserved):
    box, user, conv = toolbox
    out = box.run(user, conv, "quote_route", json.dumps({"origin": origin, "destination": destination}))
    assert out.code == "no_service"
    issue = next(i for i in out.payload["issues"] if i["role"] == role)
    assert issue["nearest_served_city"] == ref.nearest_served(unserved, 1)[0]
    assert issue["nearest_served_city"] == issue["nearest"][0]["city"]
    # both cities unserved: each issue names its own nearest city
    if destination == "Austin":
        other = next(i for i in out.payload["issues"] if i["role"] == "destination")
        assert other["nearest_served_city"] == ref.nearest_served("Austin", 1)[0]


def test_find_city_names_the_nearest_served_city_too(toolbox, ref):
    box, user, conv = toolbox
    out = box.run(user, conv, "find_city", json.dumps({"name": "Stuttgart"}))
    assert out.code == "no_service" and out.payload["nearest_served_city"] == ref.nearest_served("Stuttgart", 1)[0]


def test_every_unserved_city_gets_the_reference_nearest_or_none_on_a_tie(toolbox, ref):
    box, user, conv = toolbox
    unserved = [c for c in ref.xy if not ref.served(c)]
    assert unserved
    for city in unserved:
        out = box.run(user, conv, "find_city", json.dumps({"name": city}))
        if out.code != "no_service":  # ambiguous names are asked about first
            continue
        near = out.payload["nearest"]
        if len(near) > 1 and near[0]["distance_display"] == near[1]["distance_display"]:
            assert "nearest_served_city" not in out.payload, city
        else:
            assert out.payload["nearest_served_city"] == ref.nearest_served(city, 1)[0], city


def test_no_nearest_hint_when_the_first_two_are_equally_near():
    tie = {"city": "U", "nearest": [{"city": "A", "distance_display": "7.1 km"},
                                    {"city": "B", "distance_display": "7.1 km"}]}
    assert "nearest_served_city" not in _payload("no_service", tie)
    assert "nearest_served_city" not in _payload("no_service", {"issues": [{**tie, "role": "origin"}]})["issues"][0]
    single = {"city": "U", "nearest": [{"city": "A", "distance_display": "7.1 km"}]}
    assert _payload("no_service", single)["nearest_served_city"] == "A"
    assert "nearest_served_city" not in _payload("no_service", {"city": "U", "nearest": []})
    assert "nearest_served_city" not in _payload("ok", {"city": "A", "nearest": single["nearest"]})


def test_regression_no_service_then_nearest_quotes_the_nearest_city():
    result, summary = run(BY_NAME["no_service_then_nearest"])
    assert result.passed, (result.turn_failures, result.invariants, result.crashed)
    second = result.turns[1]
    assert [(c.tool, c.args, c.code) for c in second.tools] == [
        ("quote_route", {"origin": "Copenhagen", "destination": "Tokyo"}, "ok")]
    assert second.outcome == "sent" and "quote" in second.card_types
    assert second.tickets_after == 0


def test_regression_closest_destination_is_replaced_and_the_origin_kept():
    result, _ = run(BY_NAME["no_service_then_closest"])
    assert result.passed, (result.turn_failures, result.invariants, result.crashed)
    assert result.turns[1].tools[0].args == {"origin": "London", "destination": "Tirana"}


def test_the_observed_refusal_to_pick_the_nearest_is_a_failure():
    # 03-slice T4 run 2: no tool call, the model asks again
    bad = with_script("no_service_then_nearest", 1, say(
        "I can't pick the nearest for you — the card lists the candidate cities. Which one should I use?"))
    result, _ = run(bad)
    assert not result.passed and {"tools", "cards"} <= set(result.turn_failures[1])


def test_a_different_city_than_the_nearest_is_a_failure():
    bad = with_script("no_service_then_nearest", 1, [Use("quote_route", origin="Ohrid", destination="Tokyo")],
                      say("Here is the route."))
    result, summary = run(bad)
    assert not result.passed and "tools" in result.turn_failures[1]
    assert summary["tools"]["quote_route"]["hit"] == 1  # only the first turn's call


# --------------------------------------------------- 2. book in one message ----------------
def test_regression_book_in_one_message_shows_the_confirm_card_and_books_only_on_click():
    result, summary = run(BY_NAME["book_in_one_message"])
    assert result.passed, (result.turn_failures, result.invariants, result.crashed)
    turn = result.turns[0]
    assert [c.tool for c in turn.tools] == ["quote_route", "request_booking"]
    assert turn.tools[1].args == {"quote_id": turn.tools[0].quote_id}
    assert {"quote", "booking_request"} <= set(turn.card_types)
    assert turn.tickets_before == turn.tickets_after == 0  # no ticket during the turn
    assert "click" in turn.text.lower()
    assert not result.invariants  # click_books_once: the click made exactly one ticket


def test_regression_book_in_one_message_in_chinese():
    result, _ = run(BY_NAME["zh_book_in_one_message"])
    assert result.passed, (result.turn_failures, result.invariants, result.crashed)
    assert [c.tool for c in result.turns[0].tools] == ["quote_route", "request_booking"]


def test_the_observed_quote_without_confirm_card_is_a_failure():
    # 03-slice T4 / 06 baseline: quote only, then "Would you like me to prepare the booking?"
    bad = with_script("book_in_one_message", 0, [Use("quote_route", origin="Madrid", destination="Cairo")],
                      say("The card shows a direct flight from Madrid to Cairo. Would you like me to prepare the booking?"))
    result, summary = run(bad)
    assert not result.passed and {"tools", "cards"} <= set(result.turn_failures[0])
    assert summary["tools"]["request_booking"]["hit"] == 0
    assert not result.invariants  # no card, so the click books nothing: still safe


def test_booking_asked_for_an_unserved_city_gets_no_confirm_card():
    result, _ = run(BY_NAME["book_in_one_message_no_service"])
    assert result.passed, (result.turn_failures, result.invariants, result.crashed)
    eager = with_script("book_in_one_message_no_service", 0,
                        [Use("quote_route", origin="Austin", destination="Tokyo")],
                        [Use("request_booking", quote_id="q_0000000000000000")], say("Check the card."))
    result, _ = run(eager)
    assert any(f.startswith("forbidden_tool:request_booking") for f in result.turn_failures[0])


def test_the_prompt_keeps_both_rules():
    text = system_prompt(date(2026, 11, 23))
    assert "nearest_served_city" in text and "Do not ask again" in text
    assert "in the same turn" in text and "Book me a flight" in text
    assert "only the user's click issues the ticket" in text
