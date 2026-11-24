"""The multi-model benchmark harness (#47): spec parsing, provider dialects, the price registry
(unknown prices), usage normalisation, metric aggregation, run order, guards and the report. Fake
models only, no network."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evals import bench, pricing, spec as spec_mod
from evals.cases import CASES
from evals.report import summarize
from evals.runner import RunResult, estimate_cost, estimate_tokens
from evals.spec import ConfigSpec, SpecError, parse

BY_NAME = {c.name: c for c in CASES}
SECRET = "sk-bench-secret-0123456789"
SECRET_URL = "https://secret-gateway.example-host.test/compatible-mode/v1"


def config(**kw):
    base = {"id": "a", "provider": "deepseek", "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY"}
    return {**base, **kw}


def spec(*configs, **kw):
    return parse({"name": "t", "version": 1, "configs": list(configs) or [config()], **kw})


# ------------------------------------------------------------------ spec ---------------------
def test_the_checked_in_spec_names_three_configurations_by_provider_and_model():
    s = spec_mod.load("12-bench.json")
    assert [(c.provider, c.model) for c in s.configs] == [
        ("deepseek", "deepseek-flash"), ("bailian", "deepseek-v4.1-flash-1017"), ("bailian", "qwen3.8-flash")]
    assert {c.api_key_env for c in s.configs} == {"DEEPSEEK_API_KEY", "BAILIAN_API_KEY"}
    assert [c.effective_dialect for c in s.configs] == ["deepseek", "bailian", "bailian"]
    assert all(c.unsupported() is None for c in s.configs)
    text = (spec_mod.EXPERIMENTS / "12-bench.json").read_text()
    assert "http" not in text and "sk-" not in text  # names of variables only
    assert s.pricing_version == pricing.load().version


@pytest.mark.parametrize("bad, message", [
    ({"configs": []}, "at least one"),
    ({"configs": [config(provider="openrouter")]}, "unknown provider"),
    ({"configs": [config(provider="bailian")]}, "base_url_env"),  # no default base URL
    ({"configs": [config(api_key_env="sk-live-123")]}, "variable name"),  # a value, not a name
    ({"configs": [config(base_url_env="https://x")]}, "variable name"),
    ({"configs": [config(), config()]}, "unique"),
    ({"configs": [config(reasoning_effort="extreme")]}, "reasoning_effort"),
    ({"configs": [config(dialect="martian")]}, "dialect"),
    ({"configs": [config(api_key="sk-123")]}, "unknown configuration fields"),
    ({"configs": [config()], "colour": "red"}, "unknown spec fields"),
    ({"configs": [config()], "n": 0}, "n:"),
    ({"configs": [config(id="a b")]}, "id"),
])
def test_bad_specs_are_refused(bad, message):
    with pytest.raises(SpecError, match=message):
        parse({"name": "t", **bad})


def test_a_setting_the_dialect_cannot_express_is_unsupported_not_run():
    s = spec(config(id="ds-budget", thinking_budget=1024),  # DeepSeek has no thinking budget
             config(id="plain", dialect="plain"),  # plain sends no thinking fields: only "omit"
             config(id="plain-omit", dialect="plain", reasoning_effort="omit"),
             config(id="ok"))
    assert [c.id for c in s.configs if c.unsupported()] == ["ds-budget", "plain"]
    runs = asyncio.run(bench.run_bench(s, [BY_NAME["direct_quote"]], 1, fake=True, env={}, max_tokens=None,
                                       max_cost=None))
    status = {r.spec.id: r.status for r in runs}
    assert status == {"ds-budget": "unsupported", "plain": "unsupported", "plain-omit": "ready", "ok": "ready"}
    assert {r.spec.id: len(r.results) for r in runs} == {"ds-budget": 0, "plain": 0, "plain-omit": 1, "ok": 1}


def test_versions_are_recorded_and_a_pinned_mismatch_is_refused():
    s = spec()
    versions = spec_mod.check_versions(s, CASES, "2026-11-24")
    assert set(versions) == {"dataset_version", "prompt_version", "pricing_version"}
    assert versions["dataset_version"] == spec_mod.dataset_version(CASES)  # stable across calls (no ids, no lambdas)
    assert spec_mod.dataset_version(CASES[:-1]) != versions["dataset_version"]
    with pytest.raises(SpecError, match="dataset_version"):
        spec_mod.check_versions(spec(dataset_version="000000000000"), CASES, "2026-11-24")
    with pytest.raises(SpecError, match="pricing_version"):
        spec_mod.check_versions(spec(pricing_version="1999-01-01"), CASES, "2026-11-24")


def test_credentials_are_read_by_name_and_a_missing_one_is_reported():
    c = parse({"configs": [config(id="b", provider="bailian", model="qwen3.8-flash", api_key_env="BAILIAN_API_KEY",
                                  base_url_env="BAILIAN_OPENAI_API_BASE")]}).configs[0]
    llm = spec_mod.llm_config(c, {"BAILIAN_API_KEY": SECRET, "BAILIAN_OPENAI_API_BASE": SECRET_URL + "/"})
    assert (llm.api_key, llm.base_url, llm.model, llm.dialect) == (SECRET, SECRET_URL, "qwen3.8-flash", "bailian")
    assert SECRET not in repr(llm)
    assert spec_mod.llm_config(c, {"BAILIAN_API_KEY": SECRET}) is None
    ds = spec_mod.llm_config(ConfigSpec(**config()), {"DEEPSEEK_API_KEY": SECRET})
    assert ds.base_url == "https://api.deepseek.com" and ds.dialect == "deepseek"
    runs = asyncio.run(bench.run_bench(spec(config()), [BY_NAME["direct_quote"]], 1, fake=False, env={},
                                       max_tokens=None, max_cost=None))
    assert runs[0].status == "no_credentials" and "DEEPSEEK_API_KEY" in runs[0].reason and not runs[0].results


def test_environment_reads_the_env_file_by_name(tmp_path):
    env_file = tmp_path / "x.env"
    env_file.write_text(f"BAILIAN_API_KEY={SECRET}\nexport OTHER='1'\n")
    env = spec_mod.environment({"OCEAN_ENV_FILE": str(env_file), "PATH": "/bin"})
    assert env["BAILIAN_API_KEY"] == SECRET and env["OTHER"] == "1" and env["PATH"] == "/bin"


# ------------------------------------------------------------------ pricing ------------------
def test_the_registry_prices_deepseek_and_marks_token_plan_models_unknown():
    reg = pricing.load()
    ds = reg.price("deepseek", "deepseek-flash")
    assert (ds.input_cache_hit, ds.input_cache_miss, ds.output) == (0.006, 0.3, 1.2)
    # the same model at another provider is another price: unknown, not DeepSeek's
    assert reg.price("bailian", "deepseek-v4.1-flash-1017") is None
    assert reg.price("bailian", "qwen3.8-flash") is None
    assert reg.price("bailian", "deepseek-flash") is None and reg.price("nobody", "x") is None
    assert "token plan" in reg.basis("bailian", "qwen3.8-flash")
    usage = {"input": 1_000_000, "cached": 400_000, "output": 100_000, "reasoning": 60_000}
    assert ds.cost(usage) == pytest.approx(0.4 * 0.006 + 0.6 * 0.3 + 0.1 * 1.2)
    assert pricing.cost(None, usage) is None


def test_the_registry_refuses_duplicates_and_bad_numbers(tmp_path):
    p = tmp_path / "p.json"
    entry = {"provider": "x", "model": "m", "input_cache_hit": 1, "input_cache_miss": 1, "output": 1}
    p.write_text(json.dumps({"version": "v", "prices": [entry, entry]}))
    with pytest.raises(ValueError, match="duplicate"):
        pricing.load(p)
    p.write_text(json.dumps({"version": "v", "prices": [{**entry, "output": -1}]}))
    with pytest.raises(ValueError, match="output"):
        pricing.load(p)


@pytest.mark.parametrize("raw, expected", [
    # DeepSeek official: cache hit at the top level (and also in prompt_tokens_details)
    ({"prompt_tokens": 333, "completion_tokens": 321, "prompt_cache_hit_tokens": 128, "prompt_cache_miss_tokens": 205,
      "prompt_tokens_details": {"cached_tokens": 128}, "completion_tokens_details": {"reasoning_tokens": 294}},
     {"input": 333, "cached": 128, "output": 321, "reasoning": 294}),
    # Bailian (probed 2026-11-24): OpenAI-style details only
    ({"prompt_tokens": 294, "completion_tokens": 66, "prompt_tokens_details": {"cached_tokens": 256, "text_tokens": 294},
      "completion_tokens_details": {"text_tokens": 66}}, {"input": 294, "cached": 256, "output": 66, "reasoning": 0}),
    ({"prompt_tokens": 100, "completion_tokens": 10}, {"input": 100, "cached": 0, "output": 10, "reasoning": 0}),
    (None, {"input": 0, "cached": 0, "output": 0, "reasoning": 0}),
    ({"prompt_tokens": "x", "completion_tokens": True}, {"input": 0, "cached": 0, "output": 0, "reasoning": 0}),
])
def test_usage_is_normalised_across_providers(raw, expected):
    assert pricing.normalize_usage(raw) == expected


def test_estimates_are_tokens_always_and_dollars_only_with_a_price():
    cases = [BY_NAME["book_flow"]]
    assert estimate_tokens(cases, 2) == 2 * estimate_tokens(cases, 1) > 0
    assert estimate_cost(cases, 1, None) is None and estimate_cost(cases, 1) > 0


# ------------------------------------------------------------------ aggregation --------------
def _run(case: str, passed: bool, usage: dict[str, int]) -> RunResult:
    from evals.checks import TurnRun
    r = RunResult(case, 0, usage=dict(usage), llm_calls=2)
    n = sum(1 for s in BY_NAME[case].steps if type(s).__name__ == "Turn")
    r.turns = [TurnRun("x", latency_s=1.0, first_token_s=0.5) for _ in range(n)]
    r.turn_failures = [[] for _ in range(n)]
    if not passed:
        r.turn_failures[0] = ["tools"]
    return r


def test_tokens_and_cost_per_successful_task_and_unknown_cost():
    case = BY_NAME["direct_quote"]
    u = {"input": 1000, "cached": 200, "output": 100, "reasoning": 40}
    results = [_run("direct_quote", True, u), _run("direct_quote", False, u), _run("direct_quote", True, u)]
    s = summarize([case], results, {"mode": "real"}, price=None)
    assert s["passed_runs"] == 2 and s["tokens"] == 3300 and s["tokens_per_success"] == 1650
    assert s["usage"] == {"input": 3000, "cached": 600, "output": 300, "reasoning": 120}
    assert s["usage_per_success"]["reasoning"] == 60
    assert s["cost_usd"] is None and s["cost_per_success"] is None and s["per_case"]["direct_quote"]["cost"] is None
    priced = summarize([case], results, {"mode": "real"}, price=pricing.Price(0.01, 0.1, 1.0))
    assert priced["cost_usd"] == pytest.approx(3 * (200 * 0.01 + 800 * 0.1 + 100 * 1.0) / 1e6)
    assert priced["cost_per_success"] == pytest.approx(priced["cost_usd"] / 2)
    none = summarize([case], [_run("direct_quote", False, u)], {"mode": "real"}, price=None)
    assert none["tokens_per_success"] is None and none["usage_per_success"] is None


def test_unrequested_request_booking_and_retries_are_counted():
    from evals.checks import ToolRecord
    case = BY_NAME["quote_details"]  # one quote turn: a confirmation card was not asked for
    r = _run("quote_details", False, {"input": 1, "cached": 0, "output": 1, "reasoning": 0})
    r.turns[0].tools = [ToolRecord("quote_route", {"origin": "Boston", "destination": "Berlin"}, "ok", "q_1"),
                        ToolRecord("request_booking", {"quote_id": "q_1"}, "ok")]
    r.retries, r.llm_calls = 1, 4
    s = summarize([case], [r], {"mode": "real"})
    assert s["unrequested_request_booking"] == 1 and s["retries"] == 1 and s["retry_rate"] == 0.25
    book = BY_NAME["book_flow"]  # asked for: not counted
    rb = _run("book_flow", True, {"input": 1, "cached": 0, "output": 1, "reasoning": 0})
    rb.turns[1].tools = [ToolRecord("request_booking", {"quote_id": "q_1"}, "ok")]
    assert summarize([book], [rb], {"mode": "real"})["unrequested_request_booking"] == 0


def test_the_wilson_interval_is_wide_for_small_n():
    lo, hi = bench.wilson(3, 3)
    assert hi == 1.0 and 0.4 < lo < 0.5
    lo, hi = bench.wilson(90, 93)
    assert 0.9 < lo < hi < 1.0
    assert bench.wilson(0, 0) is None


# ------------------------------------------------------------------ order and guards ---------
def test_the_order_is_interleaved_with_a_rotating_first_configuration():
    cases = [BY_NAME["direct_quote"], BY_NAME["transfer_quote"]]
    out = bench.order(cases, 2, ["a", "b", "c"], True)
    assert len(out) == 12 and sorted(out, key=str) == sorted(bench.order(cases, 2, ["a", "b", "c"], False), key=str)
    firsts = [out[i][0] for i in range(0, 12, 3)]
    assert firsts == ["a", "b", "c", "a"]  # rotates
    assert [x[2] for x in out[:6]] == [0] * 6  # every case and configuration plays run 0 before run 1
    for i in range(0, 12, 3):
        assert len({(x[1].name, x[2]) for x in out[i:i + 3]}) == 1  # the same case, back to back
    flat = bench.order(cases, 2, ["a", "b"], False)
    assert [x[0] for x in flat] == ["a"] * 4 + ["b"] * 4


def test_identical_cases_for_every_configuration_and_the_token_guard_stops_one():
    s = spec(config(id="a"), config(id="b", model="other"))
    cases = [BY_NAME["direct_quote"], BY_NAME["book_flow"]]
    runs = asyncio.run(bench.run_bench(s, cases, 2, fake=True, env={}, max_tokens=None, max_cost=None))
    for r in runs:
        assert sorted((x.case, x.index) for x in r.results) == sorted((c.name, i) for c in cases for i in range(2))
        assert all(x.config == r.spec.id and x.passed for x in r.results)
    # a guard of 1 token: each configuration plays the runs already admitted, then starts no more
    limited = spec(config(id="a", concurrency=1))
    runs = asyncio.run(bench.run_bench(limited, cases, 3, fake=True, env={}, max_tokens=1, max_cost=None))
    assert len(runs[0].results) == 1 and runs[0].skipped == 5


def test_the_cost_guard_applies_to_priced_configurations_only():
    s = spec(config(id="priced", concurrency=1),
             config(id="plan", provider="bailian", model="qwen3.8-flash", api_key_env="BAILIAN_API_KEY",
                    base_url_env="BAILIAN_OPENAI_API_BASE", concurrency=1))
    runs = asyncio.run(bench.run_bench(s, [BY_NAME["direct_quote"]], 3, fake=True, env={}, max_tokens=None,
                                       max_cost=1e-9))
    by = {r.spec.id: r for r in runs}
    assert (len(by["priced"].results), by["priced"].skipped) == (1, 2)
    assert (len(by["plan"].results), by["plan"].skipped) == (3, 0)  # unknown price: the token guard applies


# ------------------------------------------------------------------ CLI and report -----------
def test_cli_fake_bench_writes_a_comparison_without_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BAILIAN_API_KEY", SECRET)
    monkeypatch.setenv("BAILIAN_OPENAI_API_BASE", SECRET_URL)
    out = tmp_path / "out"
    code = asyncio.run(bench.main(["--spec", "12-bench.json", "--fake", "--n", "2",
                                   "--cases", "decline,book_flow,no_service_austin", "--out", str(out)]))
    assert code == 0
    md = (out / "bench.md").read_text()
    for needle in ("## Configurations", "## Comparison matrix", "Strict safety violations", "Tokens per successful task",
                   "Unrequested `request_booking`", "First streamed text", "## Per case", "## Failed cases",
                   "Small N", "deepseek-official", "deepseek-bailian", "qwen-bailian", "| Cost | $"):
        assert needle in md, needle
    assert md.count("| unknown") >= 2  # Bailian: cost unknown, tokens compared
    data = json.loads((out / "bench.json").read_text())
    assert {c["id"]: c["status"] for c in data["configs"]} == dict.fromkeys(
        ("deepseek-official", "deepseek-bailian", "qwen-bailian"), "ready")
    assert all(len(v) == 6 for v in data["runs"].values())
    assert data["summaries"]["qwen-bailian"]["cost_usd"] is None
    assert data["meta"]["dataset_version"] and data["meta"]["prompt_version"] and data["meta"]["pricing_version"]
    for f in out.iterdir():
        text = f.read_text()
        assert SECRET not in text and SECRET_URL not in text and "secret-gateway" not in text, f.name
    assert sorted(p.name for p in out.iterdir()) == [
        "bench.json", "bench.md", "report-deepseek-bailian.md", "report-deepseek-official.md", "report-qwen-bailian.md"]
    assert SECRET not in capsys.readouterr().out


def test_cli_smoke_estimate_and_bad_input(tmp_path, capsys):
    assert asyncio.run(bench.main(["--spec", "12-bench.json", "--smoke", "--estimate"])) == 0
    out = capsys.readouterr().out
    assert "3 cases x 1 run(s)" in out and "cost unknown" in out and "deepseek-official" in out
    assert asyncio.run(bench.main(["--spec", "no-such-spec.json"])) == 2
    assert asyncio.run(bench.main(["--spec", "12-bench.json", "--cases", "nope"])) == 2
    assert asyncio.run(bench.main(["--spec", "12-bench.json", "--configs", "nope"])) == 2
    code = asyncio.run(bench.main(["--spec", "12-bench.json", "--fake", "--configs", "qwen-bailian",
                                   "--cases", "direct_quote", "--out", str(tmp_path)]))
    assert code == 0 and "deepseek-official" not in (tmp_path / "bench.md").read_text()


def test_a_safety_violation_fails_that_configuration():
    """Any invariant violation of a configuration is shown as a FAIL of that configuration, with
    the invariant named; the other configurations are unaffected."""
    s = spec(config(id="good"), config(id="bad", model="other"))
    cases = [BY_NAME["false_booked_prompt"]]
    runs = asyncio.run(bench.run_bench(s, cases, 1, fake=True, env={}, max_tokens=None, max_cost=None))
    bad = next(r for r in runs if r.spec.id == "bad")
    bad.results[0].invariants = {"no_false_booked": ["reply says a booking is done"]}
    meta = {"mode": "fake", "n": 1, "date": "d", "commit": "c", "dataset_version": "x", "prompt_version": "y",
            "pricing_version": "z"}
    sums = bench.summaries(cases, runs, meta)
    md = bench.comparison_markdown(s, cases, runs, sums, meta)
    row = next(line for line in md.splitlines() if line.startswith("| **Strict safety violations**"))
    assert row.endswith("| 0 (PASS) | **1 (FAIL: no_false_booked: 1)** |")
    assert "invariant `no_false_booked`" in md
