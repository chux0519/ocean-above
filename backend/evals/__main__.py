"""CLI: `python -m evals --mode fake|real [--n N] [--effort none|low|high|max] [--case SUBSTR] [--out DIR]`."""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import date
from pathlib import Path

from app.agent import LLMConfig, LLMNotConfigured, OpenAIChatClient
from app.agent.config import EFFORTS

from .cases import CASES
from .report import markdown, results_json, summarize
from .runner import REPO_ROOT, estimate_cost, run_all


def commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "backend"], cwd=REPO_ROOT, capture_output=True,
                               text=True).stdout.strip()
        return sha + (" (+uncommitted changes)" if dirty else "")
    except OSError:
        return "unknown"


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals")
    ap.add_argument("--mode", choices=("fake", "real"), default="fake")
    ap.add_argument("--n", type=int, default=1, help="runs per case")
    ap.add_argument("--effort", choices=EFFORTS, help="reasoning effort per request (real mode; default: the configured one)")
    ap.add_argument("--case", default="", help="only cases whose name or group contains this")
    ap.add_argument("--parallel", type=int, default=4, help="concurrent runs (real mode)")
    ap.add_argument("--out", type=Path, help="write report.md and results.json here")
    ap.add_argument("--estimate", action="store_true", help="print the cost estimate and stop")
    ap.add_argument("--max-cost", type=float, default=1.0, help="refuse to start above this estimate (USD)")
    args = ap.parse_args(argv)

    cases = [c for c in CASES if args.case in c.name or args.case in c.group]
    if not cases:
        print("no case matches", file=sys.stderr)
        return 2
    estimate = estimate_cost(cases, args.n)
    llm, model, effort = None, "scripted-fake", "n/a (fake)"
    if args.mode == "real":
        print(f"estimated cost (upper bound): ${estimate:.3f} for {len(cases)} cases x {args.n} run(s)")
        if args.estimate:
            return 0
        if estimate > args.max_cost:
            print(f"above --max-cost {args.max_cost}; lower --n or raise the limit", file=sys.stderr)
            return 2
        try:
            config = LLMConfig.from_env()
        except LLMNotConfigured:
            print("no LLM key: set LLM_API_KEY / DEEPSEEK_API_KEY or OCEAN_ENV_FILE", file=sys.stderr)
            return 2
        llm, model, effort = OpenAIChatClient(config), config.model, args.effort or config.reasoning_effort
    elif args.estimate:
        print("fake mode costs nothing")
        return 0

    done = 0

    def progress(res):
        nonlocal done
        done += 1
        print(f"[{done}/{len(cases) * args.n}] {res.case}#{res.index} {'ok' if res.passed else 'FAILED'}", flush=True)

    try:
        results = await run_all(cases, args.n, mode=args.mode, llm=llm, effort=args.effort,
                                parallel=args.parallel if args.mode == "real" else 1, progress=progress)
    finally:
        if llm is not None:
            await llm.aclose()
    meta = {"mode": args.mode, "model": model, "effort": effort, "n": args.n, "date": date.today().isoformat(),
            "commit": commit()}
    summary = summarize(cases, results, meta)
    text = markdown(summary, cases, results)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.md").write_text(text, encoding="utf-8")
        (args.out / "results.json").write_text(results_json(summary, results), encoding="utf-8")
    return 0 if all(summary["verdict"].values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
