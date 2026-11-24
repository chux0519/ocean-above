"""Follow-ups from the #47 benchmark (issue #59), each with a scripted (fake) LLM.

1. `tool_failure_retry`: after a `tool_failed` result the model asked the user whether to try
   again. Decision: the agent retries the same call once by itself, then tells the user. The prompt
   says so, and the refused record itself says so (the model reads it right where it decides).
2. `zh_ambiguous_latin_name`: "从 san jose 飞到利马" - the quote is ambiguous on the origin, and the
   Chinese reply naming "Lima" had no record to trace to (the user wrote 利马), so ReplyGuard
   replaced it with the English template. The ambiguous/unknown result now carries the resolved
   other endpoint (`resolved`), so the reply has a source.
Expected city names come from the airline's own resolver on locations.db, never from a model."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import date

import pytest

from app.agent.guard import CityIndex, ReplyGuard
from app.agent.prompt import system_prompt
from app.agent.tools import Toolbox
from app.airline import Airline
from app.core.network import NetworkProvider
from app.store import Store
from evals.cases import CASES, Turn
from evals.fake import Use, say
from evals.runner import run_all

from .conftest import REAL_DB

BY_NAME = {c.name: c for c in CASES}


def run(case):
    return asyncio.run(run_all([case], 1, mode="fake"))[0]


def with_script(name: str, turn: int, *steps):
    case = BY_NAME[name]
    chat = [i for i, s in enumerate(case.steps) if isinstance(s, Turn)]
    steps_ = list(case.steps)
    steps_[chat[turn]] = dataclasses.replace(steps_[chat[turn]], fake=steps)
    return dataclasses.replace(case, steps=tuple(steps_))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    work = tmp_path_factory.mktemp("followups")
    store = Store(work / "app.db")
    airline = Airline(store, NetworkProvider(REAL_DB))
    user = store.upsert_user("test", "followups", "Followups")["id"]
    return airline, Toolbox(airline), user, store.create_conversation(user)["id"]


# ------------------------------------------------------------ 1. retry a failed tool once ----
def test_the_prompt_tells_the_model_to_retry_a_failed_tool_once_without_asking():
    prompt = system_prompt(date(2026, 11, 24))
    rule = next(line for line in prompt.splitlines() if "could not run" in line)
    assert "once" in rule and "same arguments" in rule and "without asking" in rule


def test_a_tool_failed_record_says_to_retry_once():
    class Broken:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("locations.db unreadable")
            return fail
    out = Toolbox(Broken()).run("u", "c", "quote_route", json.dumps({"origin": "Madrid", "destination": "Cairo"}))
    assert out.code == "tool_failed"
    assert "once more with the same arguments" in out.payload["message"]
    assert "RuntimeError" not in out.for_llm()  # the exception stays in the log, not in the LLM's context


def test_asking_the_user_whether_to_retry_fails_the_retry_case():
    asks = with_script("tool_failure_retry", 0, [Use("quote_route", origin="Madrid", destination="Cairo")],
                       say("The flight data could not be loaded. Shall I try again?"))
    result = run(asks)
    assert not result.passed and "cards" in result.turn_failures[0]
    assert [t.code for t in result.turns[0].tools] == ["tool_failed"]


def test_one_retry_that_succeeds_passes_the_retry_case():
    result = run(BY_NAME["tool_failure_retry"])
    assert result.passed, result.turn_failures
    assert [t.code for t in result.turns[0].tools] == ["tool_failed", "ok"]


# -------------------------------------- 2. ambiguous result names the resolved other city ----
def test_an_ambiguous_quote_names_the_resolved_other_endpoint(world):
    airline, box, user, conv = world
    expected = airline.find_city("Lima").data["city"]
    out = box.run(user, conv, "quote_route", json.dumps({"origin": "san jose", "destination": "Lima"}))
    assert out.code == "ambiguous_city"
    assert out.payload["resolved"] == [{"role": "destination", "city": expected}]
    # swapped: the resolved endpoint is the origin
    swapped = box.run(user, conv, "quote_route", json.dumps({"origin": "Lima", "destination": "san jose"}))
    assert swapped.payload["resolved"] == [{"role": "origin", "city": expected}]


def test_an_unknown_city_quote_names_the_resolved_other_endpoint(world):
    airline, box, user, conv = world
    out = box.run(user, conv, "quote_route", json.dumps({"origin": "Atlantis", "destination": "Lima"}))
    assert out.code == "unknown_city"
    assert out.payload["resolved"] == [{"role": "destination", "city": airline.find_city("Lima").data["city"]}]


def test_both_endpoints_unresolved_name_nothing(world):
    _, box, user, conv = world
    out = box.run(user, conv, "quote_route", json.dumps({"origin": "san jose", "destination": "Atlantis"}))
    assert out.code in ("ambiguous_city", "unknown_city") and "resolved" not in out.payload


def test_the_resolved_city_is_a_record_for_the_guard_but_the_echoed_query_is_not(world):
    airline, box, user, conv = world
    out = box.run(user, conv, "quote_route", json.dumps({"origin": "san jose", "destination": "Lima"}))
    index = CityIndex(airline.networks.current().cities)
    reply = "有两个城市叫 San Jose：San Jose 和 San José。飞往 Lima 之前，请告诉我您说的是哪一个。"
    assert ReplyGuard(index, records=[out.payload], user_text="从 san jose 飞到利马多少钱？").check(reply) == []
    # a city that is in neither the records nor the user's message still fails
    assert ReplyGuard(index, records=[out.payload], user_text="从 san jose 飞到利马多少钱？").check(
        "飞往 Stuttgart 之前请告诉我是哪一个。") == [{"kind": "city", "value": "Stuttgart"}]


def test_a_chinese_reply_naming_the_resolved_city_is_sent_not_replaced():
    names_lima = with_script(
        "zh_ambiguous_latin_name", 0, [Use("quote_route", origin="san jose", destination="Lima")],
        say("有两个城市叫 San Jose：San Jose 和 San José。飞往 Lima 之前，请告诉我您说的是哪一个。"))
    result = run(names_lima)
    turn = result.turns[0]
    assert turn.outcome == "sent", turn.problems
    assert result.passed, result.turn_failures
