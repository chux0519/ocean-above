"""Multi-model benchmark (#47): several configurations play the identical cases N times each, in an
interleaved order, and a comparison matrix is written next to one full eval report per configuration.

    cd backend && OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals.bench \
        --spec 12-bench.json [--n 3] [--smoke] [--cases a,b] [--estimate] [--max-cost 0.3] \
        [--max-tokens 5000000] [--out DIR] [--fake]

Credentials are read by the variable names the spec gives and never written anywhere. A
configuration whose thinking level its dialect cannot express is `unsupported`; one whose variables
are unset is `no_credentials`; neither is run. Guards: before the start, the upper-bound estimate
against `--max-cost` (configurations with a known price) and `--max-tokens` (every configuration);
while running, a configuration that has used `--max-tokens` (or `--max-cost`) starts no further run
and its remaining runs are reported as skipped. The safety invariants and the deterministic checks
decide pass or fail; nothing here can override them."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from app import tracing
from app.agent import ChatModel

from . import pricing
from .__main__ import commit
from .cases import CASES, Case
from .report import TOOLS, failures_markdown, markdown, summarize, usd
from .runner import RunResult, estimate_cost, estimate_tokens, play, scripted
from .spec import ConfigSpec, Spec, SpecError, check_versions, environment, llm_config
from .spec import load as load_spec

# the smoke set: a multi-turn booking with a click, a no-service follow-up, a Chinese quote
SMOKE_CASES = ("book_flow", "no_service_then_nearest", "zh_quote")


@dataclass
class ConfigRun:
    spec: ConfigSpec
    status: str = "ready"  # ready | unsupported | no_credentials
    reason: str = ""
    price: pricing.Price | None = None
    pricing_basis: str = ""
    llm: Any = None
    results: list[RunResult] = field(default_factory=list)
    skipped: int = 0  # runs not started because the configuration hit a guard
    tokens: int = 0  # used so far
    cost: float = 0.0  # used so far (known prices only)


def order(cases: list[Case], n: int, configs: list[str], interleave: bool) -> list[tuple[str, Case, int]]:
    """The run order. Interleaved: run i of every case for every configuration before run i + 1,
    and the configuration that goes first rotates, so no configuration always meets the provider
    first or last (time-of-day and rate-limit effects are spread). Otherwise: configuration by
    configuration."""
    if not configs:
        return []
    if not interleave:
        return [(c, case, i) for c in configs for i in range(n) for case in cases]
    out = []
    slot = 0
    for i in range(n):
        for case in cases:
            k = slot % len(configs)
            out += [(c, case, i) for c in configs[k:] + configs[:k]]
            slot += 1
    return out


def wilson(passed: int, runs: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval of a pass rate."""
    if runs == 0:
        return None
    p = passed / runs
    denom = 1 + z * z / runs
    centre = (p + z * z / (2 * runs)) / denom
    half = z * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


async def run_bench(spec: Spec, cases: list[Case], n: int, *, fake: bool, env: dict[str, str],
                    max_tokens: int | None, max_cost: float | None,
                    progress: Callable[[str, RunResult], None] | None = None,
                    registry: pricing.Pricing | None = None) -> list[ConfigRun]:
    registry = registry or pricing.load()
    runs = {c.id: ConfigRun(c, price=registry.price(c.provider, c.model),
                            pricing_basis=registry.basis(c.provider, c.model)) for c in spec.configs}
    for run in runs.values():
        why = run.spec.unsupported()
        if why:
            run.status, run.reason = "unsupported", why
            continue
        if fake:
            continue
        config = llm_config(run.spec, env)
        if config is None:
            run.status = "no_credentials"
            run.reason = f"{run.spec.api_key_env} / {run.spec.base_url_env or 'provider default'} not set"
            continue
        run.llm = ChatModel(config, temperature=run.spec.temperature, max_tokens=run.spec.max_tokens)
    ready = [c for c, r in runs.items() if r.status == "ready"]
    gates = {c: asyncio.Semaphore(runs[c].spec.concurrency) for c in ready}
    root = Path(tempfile.mkdtemp(prefix="oa-bench-"))

    async def one(config: str, case: Case, i: int) -> None:
        run = runs[config]
        async with gates[config]:
            if (max_tokens is not None and run.tokens >= max_tokens) or \
                    (max_cost is not None and run.price is not None and run.cost >= max_cost):
                run.skipped += 1
                return
            workdir = root / f"{config}-{case.name}-{i}"
            workdir.mkdir()
            llm = scripted(case) if fake else run.llm
            res = await play(case, i, llm, workdir, effort=run.spec.reasoning_effort if not fake else None,
                             config=config)
            run.results.append(res)
            run.tokens += res.tokens
            run.cost += res.cost(run.price) or 0.0
            if progress:
                progress(config, res)

    try:
        await asyncio.gather(*(one(c, case, i) for c, case, i in order(cases, n, ready, spec.interleave)))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        for run in runs.values():
            if run.llm is not None:
                await run.llm.aclose()
    for run in runs.values():  # a stable order for the report
        run.results.sort(key=lambda r: ([c.name for c in cases].index(r.case), r.index))
    return list(runs.values())


# ------------------------------------------------------------------ report -------------------
def summaries(cases: list[Case], runs: list[ConfigRun], meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for run in runs:
        if run.status != "ready":
            continue
        c = run.spec
        out[c.id] = summarize(cases, run.results, {
            **meta, "config": c.id, "provider": c.provider, "model": c.model, "effort": c.reasoning_effort,
            "dialect": c.effective_dialect, "skipped_runs": run.skipped,
            "pricing": f"pricing {meta.get('pricing_version')}: {run.pricing_basis}"}, price=run.price)
    return out


def _num(x: float | int | None, fmt: str = "{:,.0f}") -> str:
    return "–" if x is None else fmt.format(x)


def comparison_markdown(spec: Spec, cases: list[Case], runs: list[ConfigRun], sums: dict[str, dict[str, Any]],
                        meta: dict[str, Any]) -> str:
    ids = list(sums)
    lines = [
        f"# Benchmark — {spec.name} ({meta['mode']} mode)",
        "",
        f"- Date: {meta['date']} · commit `{meta['commit']}` · dataset `{meta['dataset_version']}` "
        f"({len(cases)} cases) · prompt `{meta['prompt_version']}` · pricing `{meta['pricing_version']}`",
        f"- {meta['n']} run(s) per case and configuration, order: "
        f"{'interleaved (rotating first configuration)' if spec.interleave else 'configuration by configuration'}",
        "- Pass/fail comes from the deterministic checks and the safety invariants only; no LLM judge.",
        "",
        "## Configurations",
        "",
        "| Configuration | Provider | Model | Dialect | Reasoning effort | Thinking budget | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        c = run.spec
        status = run.status if run.status == "ready" else f"**{run.status}**: {run.reason}"
        if run.skipped:
            status += f" ({run.skipped} run(s) skipped by the budget guard)"
        lines.append(f"| {c.id} | {c.provider} | `{c.model}` | {c.effective_dialect} | {c.reasoning_effort} | "
                     f"{c.thinking_budget or '–'} | {status} |")
    if not ids:
        return "\n".join(lines + ["", "No configuration ran."]) + "\n"

    def row(label: str, get: Callable[[dict[str, Any]], str]) -> str:
        return f"| {label} | " + " | ".join(get(sums[i]) for i in ids) + " |"

    def ci(s: dict[str, Any]) -> str:
        lo_hi = wilson(s["passed_runs"], s["runs"])
        return "–" if lo_hi is None else f"{lo_hi[0] * 100:.0f}–{lo_hi[1] * 100:.0f}%"

    def safety(s: dict[str, Any]) -> str:
        total = sum(s["invariant_violations"].values())
        if not total:
            return "0 (PASS)"
        detail = ", ".join(f"{k}: {v}" for k, v in s["invariant_violations"].items() if v)
        return f"**{total} (FAIL: {detail})**"

    def tool(t: str) -> Callable[[dict[str, Any]], str]:
        def get(s: dict[str, Any]) -> str:
            v = s["tools"][t]
            return f"{v['hit']}/{v['expected']}" + (f" ({v['success'] * 100:.0f}%)" if v["success"] is not None else "")
        return get

    lines += [
        "",
        "## Comparison matrix",
        "",
        "| Metric | " + " | ".join(ids) + " |",
        "|---|" + "---|" * len(ids),
        row("**Strict safety violations** (must be 0)", safety),
        row("End-to-end trajectory pass", lambda s: f"{s['passed_runs']}/{s['runs']} ({s['trajectory_pass'] * 100:.1f}%)"),
        row("95% interval of the pass rate (Wilson)", ci),
        *(row(f"Tool accuracy `{t}` (hit/expected)", tool(t)) for t in TOOLS),
        row("Spurious calls (to a tool the turn forbids)", lambda s: str(sum(v["spurious"] for v in s["tools"].values()))),
        row("Unrequested `request_booking` (turns)", lambda s: str(s["unrequested_request_booking"])),
        row("Refused calls (bad arguments / unknown tool / tool failure)",
            lambda s: str(sum(v["refused"] for v in s["tools"].values()))),
        row("ReplyGuard fallback rate", lambda s: f"{s['fallback_rate'] * 100:.1f}%"),
        row("Budget stops (template)", lambda s: str(s["turn_tokens"]["budget_stops"])),
        row("Failed / retried LLM calls", lambda s: f"{s['retries']} of {s['llm_calls']}"),
        row("Turn errors / crashed runs", lambda s: f"{s['errors']} / {s['crashed']}"),
        row("Input tokens (of which cached)", lambda s: f"{s['usage']['input']:,} ({s['usage']['cached']:,})"),
        row("Output tokens (of which reasoning)", lambda s: f"{s['usage']['output']:,} ({s['usage']['reasoning']:,})"),
        row("Tokens per run", lambda s: _num(s["tokens_per_run"])),
        row("Tokens per successful task", lambda s: _num(s["tokens_per_success"])),
        row("Cost", lambda s: usd(s["cost_usd"])),
        row("Cost per successful task", lambda s: usd(s["cost_per_success"])),
        row("Chat turn latency p50 / p95 (s)", lambda s: f"{s['turn_latency_s']['p50']} / {s['turn_latency_s']['p95']}"),
        row("First streamed text p50 / p95 (s)", lambda s: f"{s['first_token_s']['p50']} / {s['first_token_s']['p95']}"),
        row("LLM call p50 / p95 (ms)", lambda s: f"{s['llm_latency_ms']['p50']:,} / {s['llm_latency_ms']['p95']:,}"),
        "",
        "Tokens per successful task = all tokens of the configuration / runs that passed (failed runs' tokens "
        "included). Cost `unknown`: no known price for that provider + model (e.g. a token plan); compare by tokens. "
        "Output tokens include reasoning tokens.",
        "",
        "**Small N.** With a few runs per case, differences of a few runs are not significant: compare the "
        "intervals above, not the point values, and treat per-case differences of one run as noise.",
        "",
        "## Per case (passed / runs)",
        "",
        "| Case | Group | " + " | ".join(ids) + " |",
        "|---|---|" + "---|" * len(ids),
    ]
    for case in cases:
        cells = []
        for i in ids:
            pc = sums[i]["per_case"][case.name]
            mark = "" if pc["passed"] == pc["runs"] else " ✗"
            cells.append(f"{pc['passed']}/{pc['runs']}{mark}")
        lines.append(f"| {case.name} | {case.group} | " + " | ".join(cells) + " |")
    lines += ["", "## Failed cases", ""]
    for run in runs:
        if run.status == "ready":
            lines += [f"### {run.spec.id}", "", *failures_markdown(cases, run.results, heading="####")]
    return "\n".join(lines) + "\n"


def bench_json(spec: Spec, runs: list[ConfigRun], sums: dict[str, dict[str, Any]], meta: dict[str, Any]) -> str:
    """Spec (variable names only), summaries and every run (texts and tool calls, no secrets)."""
    return json.dumps({
        "meta": meta, "spec": {"name": spec.name, "version": spec.version, "interleave": spec.interleave},
        "configs": [{**r.spec.public(), "status": r.status, "reason": r.reason, "skipped_runs": r.skipped,
                     "pricing_basis": r.pricing_basis} for r in runs],
        "summaries": sums,
        "runs": {r.spec.id: [asdict(x) for x in r.results] for r in runs if r.status == "ready"},
    }, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ CLI ----------------------
async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.bench")
    ap.add_argument("--spec", required=True, help="experiment spec (path, or a name in evals/experiments/)")
    ap.add_argument("--n", type=int, help="runs per case and configuration (default: the spec's)")
    ap.add_argument("--cases", default="", help="comma-separated case names (default: the spec's, else all)")
    ap.add_argument("--smoke", action="store_true", help=f"the smoke set ({', '.join(SMOKE_CASES)}), n=1")
    ap.add_argument("--configs", default="", help="comma-separated configuration ids (default: all)")
    ap.add_argument("--fake", action="store_true", help="every configuration plays the scripted fake (no network)")
    ap.add_argument("--estimate", action="store_true", help="print the estimates and stop")
    ap.add_argument("--max-cost", type=float, default=0.30, help="USD per configuration with a known price")
    ap.add_argument("--max-tokens", type=int, default=5_000_000, help="tokens per configuration")
    ap.add_argument("--out", type=Path, help="write bench.md, bench.json and one report per configuration here")
    args = ap.parse_args(argv)
    try:
        spec = load_spec(args.spec)
    except (OSError, SpecError) as exc:
        print(f"spec: {exc}", file=sys.stderr)
        return 2
    if args.configs:
        wanted = args.configs.split(",")
        unknown = set(wanted) - {c.id for c in spec.configs}
        if unknown:
            print(f"unknown configurations: {sorted(unknown)}", file=sys.stderr)
            return 2
        spec = Spec(**{**spec.__dict__, "configs": tuple(c for c in spec.configs if c.id in wanted)})
    names = SMOKE_CASES if args.smoke else tuple(x for x in args.cases.split(",") if x) or spec.cases
    by_name = {c.name: c for c in CASES}
    if set(names) - set(by_name):
        print(f"unknown cases: {sorted(set(names) - set(by_name))}", file=sys.stderr)
        return 2
    cases = [by_name[x] for x in names] if names else list(CASES)
    n = 1 if args.smoke else (args.n or spec.n)
    registry = pricing.load()
    try:
        versions = check_versions(spec, CASES, registry.version)
    except SpecError as exc:
        print(f"spec: {exc}", file=sys.stderr)
        return 2

    # the estimate is a deliberately high upper bound (about 5-10x the measured use); the guards
    # below are enforced on what each configuration really used, run by run
    tokens = estimate_tokens(cases, n)
    for c in spec.configs:
        cost = estimate_cost(cases, n, registry.price(c.provider, c.model))
        over = tokens > args.max_tokens or (cost is not None and cost > args.max_cost)
        print(f"{c.id}: estimate (upper bound) {tokens:,} tokens, cost {usd(cost)} for {len(cases)} cases x {n} "
              f"run(s)" + ("; above a guard: the configuration stops starting runs when it reaches it" if over else ""))
    if args.estimate:
        return 0

    def progress(config: str, res: RunResult) -> None:
        print(f"[{config}] {res.case}#{res.index} {'ok' if res.passed else 'FAILED'} ({res.tokens:,} tokens)", flush=True)

    tracing.use(tracing.from_env())  # OTEL_TRACING_ENABLED=1: every run's turns are traced (#35)
    try:
        runs = await run_bench(spec, cases, n, fake=args.fake, env=environment(), max_tokens=args.max_tokens,
                               max_cost=args.max_cost, progress=progress, registry=registry)
    finally:
        await asyncio.to_thread(tracing.shutdown)
    meta = {"mode": "fake" if args.fake else "real", "n": n, "date": date.today().isoformat(), "commit": commit(),
            "spec": spec.name, "cases": [c.name for c in cases], **versions, "tracing": tracing.enabled_in()}
    sums = summaries(cases, runs, meta)
    text = comparison_markdown(spec, cases, runs, sums, meta)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "bench.md").write_text(text, encoding="utf-8")
        (args.out / "bench.json").write_text(bench_json(spec, runs, sums, meta), encoding="utf-8")
        for run in runs:
            if run.status == "ready":
                (args.out / f"report-{run.spec.id}.md").write_text(
                    markdown(sums[run.spec.id], cases, run.results), encoding="utf-8")
    safe = all(not any(s["invariant_violations"].values()) for s in sums.values())
    return 0 if sums and safe else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
