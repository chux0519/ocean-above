"""Promptfoo provider (#47 B2): one Promptfoo test = one whole multi-turn case of `backend/evals`.

Promptfoo only orchestrates and displays. The run itself is `evals.runner.play`: a private copy of
locations.db and its own app.db, the real airline, agent loop and ReplyGuard, every chat turn of the
case and every confirmation click (done through `Airline.book`, as the user's click; the agent has no
booking path). The verdict is computed here by the same deterministic checks and safety invariants
as `python -m evals`; `assertions.py` only reads it. No LLM judge.

Provider config (set by `gen.py`):
    config_id   a configuration of the experiment spec (provider + model + dialect + effort)
    spec        the spec file (default 12-bench.json)
    mode        fake (scripted model, no network, no cost) | real
    max_cost    USD per configuration with a known price (real mode; a run that would start above it is skipped)
    max_tokens  tokens per configuration (real mode)
    ledger_dir  where the per-configuration spend is tallied across Promptfoo's workers

Credentials: by variable name from the spec, read from the environment / OCEAN_ENV_FILE inside this
process; never part of the output.
"""
from __future__ import annotations

import fcntl
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals import pricing  # noqa: E402
from evals.cases import CASES, Click, EditDb, Turn  # noqa: E402
from evals.runner import RunResult, play, scripted  # noqa: E402
from evals.spec import environment, llm_config  # noqa: E402
from evals.spec import load as load_spec  # noqa: E402

SEPARATOR = "\n\n---\nRESULT "  # the verdict block at the end of the output (assertions.py reads it)
BY_NAME = {c.name: c for c in CASES}


# ------------------------------------------------------------------ spend ledger -------------
@contextmanager
def _ledger(directory: str, config_id: str):
    """The configuration's spend so far ({"tokens", "cost"}), under an exclusive file lock (Promptfoo
    may run several Python workers)."""
    path = Path(directory) / f"{config_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text()) if path.exists() else {"tokens": 0, "cost": 0.0, "runs": 0}
            yield data
            path.write_text(json.dumps(data))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ------------------------------------------------------------------ output -------------------
def transcript(case, result: RunResult) -> str:
    """The whole conversation as Promptfoo shows it: user turns, tool calls, cards, replies, clicks."""
    lines = [f"# {case.name} ({case.group})" + (f" — {result.config}" if result.config else "")]
    turns = iter(zip(result.turns, result.turn_failures))
    clicks = {c[0]: c for c in result.clicks}
    for index, step in enumerate(case.steps):
        if isinstance(step, Click):
            click = clicks.get(index)
            seen = "not played" if click is None else (
                f"tickets created: {click[2]}" if click[1] else "no bookable card")
            lines.append(f"\n[user clicks Confirm on the newest card: {seen}; expected: {step.tickets}]")
            continue
        if isinstance(step, EditDb):
            lines.append("\n[locations.db changed: " + "; ".join(step.sql) + "]")
            continue
        try:
            turn, failures = next(turns)
        except StopIteration:  # the run crashed before this turn
            lines.append(f"\nUser: {step.user}\n(not played)")
            continue
        lines.append(f"\nUser: {turn.user}")
        for call in turn.tools:
            lines.append(f"  tool {call.tool}({json.dumps(call.args, ensure_ascii=False)}) -> {call.code}")
        if turn.card_types:
            lines.append(f"  cards: {', '.join(turn.card_types)}")
        reply = turn.text if turn.outcome != "error" else f"(error: {turn.error})"
        lines.append(f"Assistant ({turn.outcome}): {reply}")
        lines.append(f"  tickets: {turn.tickets_before} -> {turn.tickets_after}" + (
            f"; FAILED: {', '.join(failures)}" if failures else ""))
    if result.crashed:
        lines.append("\nCrashed:\n" + result.crashed.strip())
    return "\n".join(lines)


def verdict(case, result: RunResult, cost: float | None) -> dict[str, Any]:
    chat = [s for s in case.steps if isinstance(s, Turn)]
    return {
        "case": case.name, "group": case.group, "config": result.config, "passed": result.passed,
        "crashed": bool(result.crashed),
        "invariants": result.invariants,  # safety: must be empty
        "turn_failures": [{"user": step.user, "failures": f} for step, f in zip(chat, result.turn_failures) if f],
        "usage": result.usage, "tokens": result.tokens, "llm_calls": result.llm_calls, "retries": result.retries,
        "cost_usd": cost,
        "turn_latency_s": [round(t.latency_s, 2) for t in result.turns],
    }


# ------------------------------------------------------------------ the provider -------------
async def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    cfg = options.get("config") or {}
    name = ((context or {}).get("vars") or {}).get("case") or prompt.strip()
    case = BY_NAME.get(name)
    if case is None:
        return {"error": f"unknown case {name!r}"}
    spec = load_spec(cfg.get("spec", "12-bench.json"))
    by_id = {c.id: c for c in spec.configs}
    if cfg.get("config_id") not in by_id:
        return {"error": f"unknown configuration {cfg.get('config_id')!r}"}
    conf = by_id[cfg["config_id"]]
    if conf.unsupported():
        return {"error": f"unsupported configuration: {conf.unsupported()}"}
    mode = cfg.get("mode", "fake")
    price = pricing.load().price(conf.provider, conf.model)
    ledger_dir = cfg.get("ledger_dir") or str(Path(tempfile.gettempdir()) / "oa-promptfoo-ledger")

    llm = None
    if mode == "real":
        config = llm_config(conf, environment())
        if config is None:
            return {"error": f"no credentials: {conf.api_key_env} / {conf.base_url_env or 'provider default'} not set"}
        with _ledger(ledger_dir, conf.id) as spent:
            if spent["tokens"] >= float(cfg.get("max_tokens", 5_000_000)) or (
                    price is not None and spent["cost"] >= float(cfg.get("max_cost", 0.25))):
                return {"error": f"skipped: {conf.id} reached its guard ({spent['tokens']:,} tokens, "
                                 f"${spent['cost']:.4f})"}
        from app.agent import ChatModel
        llm = ChatModel(config, temperature=conf.temperature, max_tokens=conf.max_tokens)
    workdir = Path(tempfile.mkdtemp(prefix="oa-promptfoo-"))
    try:
        result = await play(case, 0, llm if llm is not None else scripted(case), workdir,
                            effort=conf.reasoning_effort if mode == "real" else None, config=conf.id)
    finally:
        if llm is not None:
            await llm.aclose()
        shutil.rmtree(workdir, ignore_errors=True)
    cost = result.cost(price) if mode == "real" else None
    if mode == "real":
        with _ledger(ledger_dir, conf.id) as spent:
            spent["tokens"] += result.tokens
            spent["cost"] += cost or 0.0
            spent["runs"] += 1
    out: dict[str, Any] = {
        "output": transcript(case, result) + SEPARATOR + json.dumps(verdict(case, result, cost), ensure_ascii=False),
        # no "cached" here: Promptfoo reads it as its own response cache and labels the run "(cached)";
        # the provider's prompt-cache hits go in the metadata
        "tokenUsage": {"total": result.tokens, "prompt": result.usage["input"], "completion": result.usage["output"],
                       "numRequests": result.llm_calls},
        "metadata": {"config": conf.id, "provider": conf.provider, "model": conf.model, "mode": mode,
                     "cached_input_tokens": result.usage["cached"], "reasoning_tokens": result.usage["reasoning"]},
    }
    if cost is not None:
        out["cost"] = cost
    return out
