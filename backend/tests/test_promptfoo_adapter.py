"""The optional Promptfoo adapter (evals/promptfoo, #47 B2) without Promptfoo itself: the provider
plays one whole case, the assertions only read its deterministic verdict, the committed configs
match the spec and the case list. Fake models only, no network."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from evals.cases import CASES

PF = Path(__file__).resolve().parents[2] / "evals" / "promptfoo"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"pf_{name}", PF / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider, assertions, gen = load("provider"), load("assertions"), load("gen")


def call(case: str, **config):
    options = {"config": {"config_id": "deepseek-official", "mode": "fake", **config}}
    return asyncio.run(provider.call_api(case, options, {"vars": {"case": case}}))


def test_one_test_is_a_whole_conversation_with_its_clicks():
    out = call("tickets_by_city")
    text = out["output"]
    case = next(c for c in CASES if c.name == "tickets_by_city")
    users = [s.user for s in case.steps if hasattr(s, "user")]
    assert [line[6:] for line in text.split("\n") if line.startswith("User: ")] == users  # every turn, in order
    assert text.count("[user clicks Confirm on the newest card: tickets created: 1; expected: 1]") == 2
    assert assertions.safety(text)["pass"] and assertions.trajectory(text)["pass"]
    verdict = json.loads(text.rsplit(provider.SEPARATOR, 1)[1])
    assert verdict["passed"] and verdict["invariants"] == {} and verdict["cost_usd"] is None  # fake: no cost
    assert out["tokenUsage"]["numRequests"] == verdict["llm_calls"] > 0 and "cost" not in out


def test_the_stale_click_books_nothing_and_shows_it():
    text = call("stale_quote")["output"]
    assert "[locations.db changed:" in text
    assert "tickets created: 0; expected: 0]" in text and "tickets created: 1; expected: 1]" in text


def test_a_failed_trajectory_and_a_safety_violation_fail_their_assertions(monkeypatch):
    import dataclasses
    from evals.fake import Use, last_quote_id, say
    case = next(c for c in CASES if c.name == "quote_only_no_card")
    turn = dataclasses.replace(case.steps[0], fake=(
        [Use("quote_route", origin="Madrid", destination="Cairo")],
        lambda m: [Use("request_booking", quote_id=last_quote_id(m))], say("Please confirm on the card.")))
    monkeypatch.setitem(provider.BY_NAME, case.name, dataclasses.replace(case, steps=(turn,)))
    text = call(case.name)["output"]
    t = assertions.trajectory(text)
    assert not t["pass"] and "forbidden_tool:request_booking" in t["reason"]
    assert assertions.safety(text)["pass"]  # a card nobody asked for is not a safety violation
    bad = text.rsplit(provider.SEPARATOR, 1)[0] + provider.SEPARATOR + json.dumps(
        {**json.loads(text.rsplit(provider.SEPARATOR, 1)[1]), "invariants": {"no_agent_booking": ["x"]}})
    s = assertions.safety(bad)
    assert not s["pass"] and "no_agent_booking" in s["reason"]
    assert not assertions.safety("no verdict here")["pass"] and not assertions.trajectory("")["pass"]


def test_errors_and_the_real_mode_guards(tmp_path, monkeypatch):
    assert "unknown case" in call("no-such-case")["error"]
    assert "unknown configuration" in call("direct_quote", config_id="nope")["error"]
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert "no credentials: DEEPSEEK_API_KEY" in call("direct_quote", mode="real")["error"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-used")
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "deepseek-official.json").write_text(json.dumps({"tokens": 10, "cost": 0.25, "runs": 3}))
    out = call("direct_quote", mode="real", ledger_dir=str(ledger), max_cost=0.25)
    assert out["error"].startswith("skipped: deepseek-official reached its guard")  # no request was made
    (ledger / "deepseek-official.json").write_text(json.dumps({"tokens": 6_000_000, "cost": 0, "runs": 3}))
    assert "skipped" in call("direct_quote", mode="real", ledger_dir=str(ledger), max_cost=1)["error"]


@pytest.mark.parametrize("name, fake", [("promptfooconfig.fake.json", True), ("promptfooconfig.json", False)])
def test_the_committed_configs_are_the_generated_ones(name, fake):
    committed = json.loads((PF / name).read_text())
    assert committed == gen.build("12-bench.json", fake=fake, configs=[], cases=[], max_cost=0.25, max_tokens=5_000_000)
    assert len(committed["providers"]) == 3 and len(committed["tests"]) == len(CASES)
    assert committed["sharing"] is False and committed["evaluateOptions"]["cache"] is False
    asserts = committed["defaultTest"]["assert"]
    assert [a["value"] for a in asserts] == ["file://assertions.py:safety", "file://assertions.py:trajectory"]
    assert all(a["type"] == "python" for a in asserts)  # deterministic, no LLM judge
    text = json.dumps(committed)
    assert "http" not in text and "sk-" not in text


def test_promptfoo_is_not_a_dependency_of_the_app_or_of_pytest():
    backend = Path(__file__).resolve().parents[1]
    assert "promptfoo" not in (backend / "pyproject.toml").read_text().lower()
    assert "promptfoo" not in (backend.parent / "frontend" / "package.json").read_text().lower()
