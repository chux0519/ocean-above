"""The eval harness (backend/evals) in CI mode: a scripted LLM plays every case, and deliberately
bad scripts prove that the checks catch what they are there to catch. No real model, no cost."""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from evals import __main__ as cli
from evals.cases import CASES, Case, Turn
from evals.checks import (REFUSED, ToolRecord, TurnRun, call_matches, claims_booking, fold, invariant_violations)
from evals.fake import ScriptedLLM, Use, last_quote_id, say
from evals.report import FALLBACK_MAX, TOOLS, summarize
from evals.runner import estimate_cost, play, run_all

BY_NAME = {c.name: c for c in CASES}


def run(cases, n=1, **kw):
    results = asyncio.run(run_all(list(cases), n, mode="fake", **kw))
    return results, summarize(list(cases), results, {"mode": "fake"})


def with_script(name: str, turn: int, *steps) -> Case:
    """The case with the fake script of its `turn`-th chat turn replaced."""
    case = BY_NAME[name]
    chat = [i for i, s in enumerate(case.steps) if isinstance(s, Turn)]
    steps_ = list(case.steps)
    steps_[chat[turn]] = dataclasses.replace(steps_[chat[turn]], fake=steps)
    return dataclasses.replace(case, steps=tuple(steps_))


# ------------------------------------------------------------------ the case set itself -------
def test_every_case_passes_with_the_scripted_good_model():
    results, summary = run(CASES, n=2)
    failed = [(r.case, r.crashed, r.invariants, r.turn_failures) for r in results if not r.passed]
    assert not failed, failed
    assert summary["trajectory_pass"] == 1.0 and summary["fallback_rate"] == 0.0
    assert all(summary["verdict"].values()), summary["verdict"]
    assert summary["runs"] == 2 * len(CASES)


def test_case_set_covers_the_issue_list_and_every_tool():
    names = {c.name for c in CASES}
    assert len(names) == len(CASES)
    for needed in ("direct_quote", "transfer_quote", "no_service_abu_dhabi", "no_service_stuttgart",
                   "no_service_austin", "ambiguous_san_jose", "decline", "question_about_quote", "tickets_by_city",
                   "same_city", "unknown_city", "false_booked_prompt", "zh_quote"):
        assert needed in names
    _, summary = run(CASES)
    for tool in TOOLS:
        assert summary["tools"][tool]["expected"] > 0, tool
        assert summary["tools"][tool]["success"] == 1.0
    scripted = [t for c in CASES for t in c.steps if isinstance(t, Turn)]
    assert all(t.fake for t in scripted), "every chat turn needs a fake script for CI mode"


# ------------------------------------------------------- the checks catch bad behaviour -------
def test_request_booking_on_a_declined_quote_fails_the_trajectory():
    bad = with_script("decline", 1, lambda m: [Use("request_booking", quote_id=last_quote_id(m))],
                      say("Booking it now."))
    results, summary = run([bad])
    assert not results[0].passed
    assert any("forbidden_tool:request_booking" in f for f in results[0].turn_failures[1])
    assert summary["tools"]["request_booking"]["spurious"] == 1
    assert not summary["verdict"]["trajectory"]


@pytest.mark.parametrize("claim", ["Your ticket is booked!", "Great, it has been booked and confirmed.",
                                   "I've booked it for you.", "已经为您出票。"])
def test_a_false_booked_claim_is_stopped_by_the_guard_and_flagged_if_it_gets_through(claim):
    # Since T3 round 02 the ReplyGuard replaces most such replies with the template. "it has been booked"
    # (no noun like ticket / flight next to the verb) still gets through; there the eval's invariant
    # is the only thing that notices (finding reported to the coordinator).
    bad = with_script("false_booked_prompt", 1, say(claim))
    results, summary = run([bad])
    turn = results[0].turns[1]
    if turn.outcome == "fallback":
        assert claim not in turn.text and "no_false_booked" not in results[0].invariants
    else:
        assert claim == "Great, it has been booked and confirmed."  # the known guard gap
        assert "no_false_booked" in results[0].invariants and not summary["verdict"]["invariants"]
    # the eval's own check still flags the text if it ever reaches the user
    assert claims_booking(claim)
    leaked = TurnRun("Is it booked?", text=claim, tickets_before=0, tickets_after=0)
    assert "no_false_booked" in invariant_violations([leaked], [])


def test_honest_replies_are_not_flagged():
    for text in ("Not yet: please click Confirm on the card to book it.", "Nothing is booked until you click.",
                 "Shall I book it?", "If you click Confirm the ticket will be booked.", "尚未出票，请点击确认。",
                 "You can cancel it before it is booked.", "Your ticket is issued only after you confirm."):
        assert not claims_booking(text), text


def test_a_wrong_number_is_replaced_and_counted_as_a_fallback():
    bad = with_script("transfer_quote", 0, [Use("quote_route", origin="Guangzhou", destination="Lima")],
                      say("The trip costs $999.99 in total."))
    results, summary = run([bad])
    assert results[0].turns[0].outcome == "fallback" and summary["fallback_rate"] == 1.0
    assert summary["fallback_kinds"] == {"number": 1}
    assert summary["fallback_rate"] > FALLBACK_MAX and not summary["verdict"]["fallback_rate"]
    assert not results[0].invariants  # the user never saw the wrong number


def test_a_city_from_geography_is_replaced():
    bad = with_script("no_service_abu_dhabi", 0, [Use("quote_route", origin="Abu Dhabi", destination="London")],
                      say("We don't serve Abu Dhabi, but Dubai is right next to it."))
    results, summary = run([bad])
    assert results[0].turns[0].outcome == "fallback" and summary["fallback_kinds"] == {"city": 1}
    assert "Dubai" not in results[0].turns[0].text


def test_wrong_arguments_are_a_miss_for_that_tool():
    bad = with_script("transfer_quote", 0, [Use("quote_route", origin="Guangzhou", destination="Peru")], say("Ok."))
    results, summary = run([bad])
    assert summary["tools"]["quote_route"]["expected"] == 1 and summary["tools"]["quote_route"]["hit"] == 0
    assert not results[0].passed and not summary["verdict"]["tool:quote_route"]


def test_a_refused_call_that_is_corrected_still_passes_but_is_counted():
    ok = with_script("transfer_quote", 0, [Use("quote_route", origin="Guangzhou")],
                     [Use("quote_route", origin="Guangzhou", destination="Lima")],
                     lambda m: say("Route found. Please look at the card."))
    results, summary = run([ok])
    assert results[0].passed
    assert summary["tools"]["quote_route"]["refused"] == 1 and summary["tools"]["quote_route"]["actual"] == 2


def test_an_unrequested_filter_is_not_a_correct_call():
    bad = with_script("tickets_empty", 0, [Use("list_my_tickets", city="Lima")], say("You have no tickets yet."))
    results, summary = run([bad])
    assert summary["tools"]["list_my_tickets"]["hit"] == 0


def test_booking_the_wrong_quote_is_a_miss():
    bad = with_script("book_flow", 1, [Use("request_booking", quote_id="q_not_mine")], say("Done."))
    results, _ = run([bad])
    assert not results[0].passed


def test_a_crashing_run_is_a_failed_run_not_a_crashed_report(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("evals.runner._play", boom)
    results, summary = run([BY_NAME["direct_quote"]])
    assert not results[0].passed and "boom" in results[0].crashed
    assert summary["crashed"] == 1 and not summary["verdict"]["trajectory"]


# --------------------------------------------------------------------- checks, directly -----
def test_argument_matching_ignores_case_accents_and_spacing():
    from evals.cases import E
    expect = E("quote_route", origin=("San José", "san jose"), destination="Lima")
    assert call_matches(ToolRecord("quote_route", {"origin": " SAN  JOSE", "destination": "lima"}, "ok"), expect, None)
    assert not call_matches(ToolRecord("quote_route", {"origin": "San Jose", "destination": "Lima", "x": "1"}, "ok"), expect, None)
    assert not call_matches(ToolRecord("quote_route", {"origin": "San Jose"}, "ok"), expect, None)
    assert fold("Zürich ") == "zurich"


def test_invariants_on_synthetic_runs():
    leaked = TurnRun("hi", tickets_before=0, tickets_after=1)
    assert "no_agent_booking" in invariant_violations([leaked], [])
    bad_stream = TurnRun("hi", stream_problem="no done")
    assert "stream" in invariant_violations([bad_stream], [])
    unproven = TurnRun("hi", text="It costs 5 dollars.", recheck=[{"kind": "number", "value": "5"}])
    assert "provenance" in invariant_violations([unproven], [])
    assert not invariant_violations([TurnRun("hi", text="fine")], [(1, True, 1)])
    assert "click_books_once" in invariant_violations([], [(1, True, 0)])
    assert "click_books_once" in invariant_violations([], [(1, True, 2)])
    assert "click_books_once" in invariant_violations([], [(1, False, 1)])
    assert REFUSED == {"invalid_arguments", "unknown_tool", "tool_failed"}


# --------------------------------------------------------------- runner / CLI / metrics ------
def test_reasoning_effort_reaches_every_llm_call(tmp_path):
    case = BY_NAME["decline"]
    from evals.runner import scripted
    llm = scripted(case)
    result = asyncio.run(play(case, 0, llm, tmp_path, effort="max"))
    assert result.passed and llm.efforts and set(llm.efforts) == {"max"}
    (tmp_path / "b").mkdir()
    llm2 = scripted(case)
    asyncio.run(play(case, 0, llm2, tmp_path / "b", effort=None))
    assert set(llm2.efforts) == {None}


def test_cost_estimate_is_an_upper_bound_of_a_few_cents_per_run():
    full = estimate_cost(list(CASES), 1)
    assert 0.01 < full < 0.3, full
    assert estimate_cost(list(CASES), 3) == pytest.approx(3 * full)


def test_cli_fake_mode_writes_report_and_json(tmp_path, capsys):
    code = asyncio.run(cli.main(["--mode", "fake", "--n", "2", "--case", "no_booking", "--out", str(tmp_path)]))
    assert code == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    for section in ("# LLM eval report", "## Verdict", "## Tool-call accuracy", "## Invariants", "## Cases", "## Failures"):
        assert section in report
    assert "reasoning effort" in report and "| `request_booking` |" in report
    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert data["summary"]["runs"] == 2 * 6 and data["summary"]["n"] == 2


def test_cli_exit_code_is_nonzero_when_a_threshold_fails(tmp_path, monkeypatch):
    bad = with_script("transfer_quote", 0, [Use("quote_route", origin="Guangzhou", destination="Peru")], say("Ok."))
    monkeypatch.setattr(cli, "CASES", (bad,))
    assert asyncio.run(cli.main(["--mode", "fake"])) == 1


def test_cli_real_mode_estimates_without_a_key_and_refuses_without_one(tmp_path, monkeypatch, capsys):
    for var in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "missing.env"))
    assert asyncio.run(cli.main(["--mode", "real", "--estimate"])) == 0
    assert "estimated cost" in capsys.readouterr().out
    assert asyncio.run(cli.main(["--mode", "real"])) == 2
    assert asyncio.run(cli.main(["--mode", "real", "--n", "500"])) == 2  # over --max-cost
    assert asyncio.run(cli.main(["--mode", "fake", "--case", "no-such-case"])) == 2
