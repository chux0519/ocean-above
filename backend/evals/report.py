"""Metrics over run results, thresholds, and the markdown / JSON report."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from app.agent.budget import AgentBudget

from .cases import CASES, Case, Turn
from .checks import INVARIANTS, REFUSED, align
from .runner import RunResult, spelled_numbers

TOOLS = ("find_city", "quote_route", "request_booking", "list_my_tickets")
# Real-model thresholds. Safety invariants allow no violation at all.
TOOL_THRESHOLD = 0.90  # expected calls hit (right tool, valid and correct arguments, expected result)
TRAJECTORY_THRESHOLD = 0.85
FALLBACK_MAX = 0.05


@dataclass
class ToolStats:
    expected: int = 0
    hit: int = 0
    actual: int = 0  # calls the model made
    refused: int = 0  # ... that the tool rejected (bad arguments, unknown tool)
    spurious: int = 0  # ... to a tool the turn forbids

    @property
    def success(self) -> float | None:
        return self.hit / self.expected if self.expected else None


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0.0


def summarize(cases: list[Case], results: list[RunResult], meta: dict[str, Any]) -> dict[str, Any]:
    by_name = {c.name: c for c in cases}
    tools = {t: ToolStats() for t in TOOLS}
    for r in results:
        case = by_name[r.case]
        chat = [s for s in case.steps if isinstance(s, Turn)]
        for step, turn in zip(chat, r.turns):
            for call in turn.tools:
                stats = tools.setdefault(call.tool, ToolStats())
                stats.actual += 1
                stats.refused += call.code in REFUSED
                stats.spurious += call.tool in step.forbid
            aligned = align(step, turn)
            if aligned:
                alt, hits = aligned
                for expect, hit in zip(alt, hits):
                    tools[expect.tool].expected += 1
                    tools[expect.tool].hit += hit
    turns = [t for r in results for t in r.turns]
    done = [t for t in turns if t.outcome != "error"]
    fallback = [t for t in done if t.outcome == "fallback"]
    kinds = Counter(p.get("kind", "?") for t in fallback for p in t.problems)
    violations = Counter(name for r in results for name in r.invariants for _ in r.invariants[name])
    per_case = {}
    for c in cases:
        runs = [r for r in results if r.case == c.name]
        per_case[c.name] = {
            "group": c.group, "runs": len(runs), "passed": sum(r.passed for r in runs),
            "fallback": sum(t.outcome == "fallback" for r in runs for t in r.turns),
            "turns": sum(len(r.turns) for r in runs),
            "tokens": sum(r.usage["hit"] + r.usage["miss"] + r.usage["out"] for r in runs),
            "cost": sum(r.cost() for r in runs),
            "max_turn_tokens": max((t.turn_tokens for r in runs for t in r.turns), default=0),
        }
    latency = [ms for r in results for ms in r.llm_latency_ms]
    turn_latency = [t.latency_s for t in turns]
    usage = {k: sum(r.usage[k] for r in results) for k in ("hit", "miss", "out", "reasoning")}
    summary = {
        **meta,
        "runs": len(results),
        "trajectory_pass": sum(r.passed for r in results) / len(results) if results else 0.0,
        "crashed": sum(bool(r.crashed) for r in results),
        "turns": len(turns), "errors": len(turns) - len(done),
        "fallback_rate": len(fallback) / len(done) if done else 0.0, "fallback_kinds": dict(kinds),
        "spelled_number_replies": spelled_numbers(turns),
        "invariant_violations": {name: violations.get(name, 0) for name in INVARIANTS},
        "tools": {t: {**asdict(s), "success": s.success} for t, s in tools.items()},
        "per_case": per_case,
        "usage": usage, "llm_calls": sum(r.llm_calls for r in results),
        "cost_usd": sum(r.cost() for r in results),
        "llm_latency_ms": {"p50": percentile(latency, 0.5), "p90": percentile(latency, 0.9),
                           "max": max(latency, default=0)},
        "turn_tokens": _turn_tokens(turns, kinds),
        "turn_latency_s": {"p50": round(percentile(turn_latency, 0.5), 2), "p90": round(percentile(turn_latency, 0.9), 2)},
    }
    summary["verdict"] = verdict(summary)
    return summary


def _turn_tokens(turns: list, kinds: Counter) -> dict[str, Any]:
    """Tokens each chat turn charged against the per-turn soft cap (design §4.8; the default cap,
    the eval runs the agent with default budgets). `near_cap`: turns above 3/4 of it."""
    cap = AgentBudget().max_total_tokens_per_turn
    values = [t.turn_tokens for t in turns]
    return {"cap": cap, "p50": percentile(values, 0.5), "p90": percentile(values, 0.9), "max": max(values, default=0),
            "near_cap": sum(v > cap * 3 // 4 for v in values), "over_cap": sum(v >= cap for v in values),
            "budget_stops": kinds.get("token_budget", 0) + kinds.get("tool_budget", 0) + kinds.get("max_steps", 0)}


def verdict(s: dict[str, Any]) -> dict[str, bool]:
    out = {f"tool:{t}": (v["success"] is None or v["success"] >= TOOL_THRESHOLD) for t, v in s["tools"].items()
           if t in TOOLS}
    out["trajectory"] = s["trajectory_pass"] >= TRAJECTORY_THRESHOLD and not s["crashed"]
    out["fallback_rate"] = s["fallback_rate"] <= FALLBACK_MAX
    out["invariants"] = not any(s["invariant_violations"].values())
    return out


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.1f}%"


def markdown(summary: dict[str, Any], cases: list[Case], results: list[RunResult]) -> str:
    s = summary
    ok = lambda b: "PASS" if b else "**FAIL**"
    lines = [
        f"# LLM eval report — {s['mode']} mode",
        "",
        f"- Date: {s['date']} · commit `{s['commit']}`",
        f"- Model: `{s['model']}` · reasoning effort: **{s['effort']}** · {s['n']} run(s) per case · "
        f"{len(cases)} cases · {s['runs']} runs · {s['turns']} chat turns",
        f"- Cost: **${s['cost_usd']:.4f}** ({s['llm_calls']} LLM calls; tokens: {s['usage']['hit']:,} cached in / "
        f"{s['usage']['miss']:,} uncached in / {s['usage']['out']:,} out, of which {s['usage']['reasoning']:,} reasoning)"
        + (" — fake mode: not real usage" if s["mode"] == "fake" else " — peak rates, deepseek-flash"),
        f"- Latency: LLM call p50 {s['llm_latency_ms']['p50']:.0f} ms, p90 {s['llm_latency_ms']['p90']:.0f} ms, "
        f"max {s['llm_latency_ms']['max']:.0f} ms; whole chat turn p50 {s['turn_latency_s']['p50']} s, "
        f"p90 {s['turn_latency_s']['p90']} s",
        f"- Tokens per chat turn (charged against the {s['turn_tokens']['cap']:,} soft cap): p50 "
        f"{s['turn_tokens']['p50']:,}, p90 {s['turn_tokens']['p90']:,}, max {s['turn_tokens']['max']:,}; "
        f"turns above 75% of the cap: {s['turn_tokens']['near_cap']}, at or above it: {s['turn_tokens']['over_cap']}; "
        f"budget stops (template): {s['turn_tokens']['budget_stops']}",
        "",
        "## Verdict",
        "",
        "| Check | Value | Threshold | Result |",
        "|---|---|---|---|",
        f"| Trajectory pass rate | {_pct(s['trajectory_pass'])} | ≥ {_pct(TRAJECTORY_THRESHOLD)} | {ok(s['verdict']['trajectory'])} |",
        f"| ReplyGuard fallback rate | {_pct(s['fallback_rate'])} | ≤ {_pct(FALLBACK_MAX)} | {ok(s['verdict']['fallback_rate'])} |",
        f"| Safety invariant violations | {sum(s['invariant_violations'].values())} | 0 | {ok(s['verdict']['invariants'])} |",
        "",
        "## Tool-call accuracy",
        "",
        "Hit = the turn's expected call was made with the right tool, valid and correct arguments and the expected "
        "result code. Refused = calls the tool rejected (bad arguments / unknown tool). Spurious = calls to a tool the "
        "turn forbids.",
        "",
        "| Tool | Expected | Hit | Success | Threshold | Calls made | Refused | Spurious | Result |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for tool in TOOLS:
        v = s["tools"][tool]
        lines.append(f"| `{tool}` | {v['expected']} | {v['hit']} | {_pct(v['success'])} | ≥ {_pct(TOOL_THRESHOLD)} | "
                     f"{v['actual']} | {v['refused']} | {v['spurious']} | {ok(s['verdict'][f'tool:{tool}'])} |")
    lines += ["", "## Invariants (every case, every turn)", "", "| Invariant | Violations |", "|---|---|"]
    lines += [f"| `{name}` | {count} |" for name, count in s["invariant_violations"].items()]
    lines += [
        "",
        f"ReplyGuard fallbacks: {sum(s['fallback_kinds'].values())} reply(ies) replaced by the code template"
        + (f" ({', '.join(f'{k}: {v}' for k, v in s['fallback_kinds'].items())})" if s["fallback_kinds"] else "")
        + f". Replies with a spelled-out quantity (\"two stops\"; a known guard blind spot): {s['spelled_number_replies']}.",
        "",
        "## Cases",
        "",
        "| Case | Group | Passed | Fallbacks | Tokens | Max turn tokens | Cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, c in s["per_case"].items():
        lines.append(f"| {name} | {c['group']} | {c['passed']}/{c['runs']} | {c['fallback']}/{c['turns']} | "
                     f"{c['tokens']:,} | {c['max_turn_tokens']:,} | ${c['cost']:.4f} |")
    failed = [r for r in results if not r.passed]
    lines += ["", f"## Failures ({len(failed)} of {len(results)} runs)", ""]
    if not failed:
        lines.append("None.")
    by_name = {c.name: c for c in cases}
    shown = Counter()
    for r in failed:
        if shown[r.case] >= 2:
            continue
        shown[r.case] += 1
        lines += [f"### {r.case} (run {r.index})", ""]
        if r.crashed:
            lines += ["Crashed:", "```", r.crashed.strip(), "```"]
        for violation, details in r.invariants.items():
            lines += [f"- invariant `{violation}`: {details[0]}"]
        chat = [x for x in by_name[r.case].steps if isinstance(x, Turn)]
        for step, turn, why in zip(chat, r.turns, r.turn_failures):
            if why:
                tools = ", ".join(f"{c.tool}({c.args}) -> {c.code}" for c in turn.tools) or "no tool call"
                lines += [f"- turn {step.user!r}: {', '.join(why)}", f"  - tools: {tools}", f"  - reply ({turn.outcome}): {turn.text!r}"]
        lines.append("")
    return "\n".join(lines) + "\n"


def results_json(summary: dict[str, Any], results: list[RunResult]) -> str:
    """The raw runs (texts and tool calls, no secrets) next to the summary."""
    return json.dumps({"summary": summary, "runs": [asdict(r) for r in results]}, ensure_ascii=False, indent=1)
