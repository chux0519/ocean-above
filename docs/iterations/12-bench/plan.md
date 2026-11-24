# 12-bench — multi-model agent benchmark, optional Promptfoo (#47)

User request (2026-11-24): roadmap item 4, "#47 — Multi-model benchmarks + optional Promptfoo", after #49, #48 and #35
were accepted. Owner additions on #47:
- compare `qwen3.8-flash` (Bailian, token plan);
- compare `deepseek-v4.1-flash-1017` on Bailian with our official DeepSeek service (`deepseek-flash`) — provider is
  its own axis;
- when the price is unknown, compare by tokens and mark the dollar cost unknown.

## Take-home check (2026-11-24)
| Requirement | Effect | Result |
|---|---|---|
| Correct routes, prices, confirmation before booking | the benchmark reuses the deterministic checks and B1–B7 as automatic FAIL; no LLM judge can override them | consistent |
| Design doc: tools, how the agent was tested | benchmark method and results go into the design doc's evaluation section | consistent |
| Runtime unchanged for users | benchmark code and Promptfoo are optional; production keeps deepseek-flash | consistent |
No conflict.

## Scope
**P0 — model-neutral benchmark (`backend/evals/`):**
- A versioned experiment spec (YAML or JSON in `backend/evals/experiments/`). Fields:
  - `provider` and `model`;
  - `base_url_env` and `api_key_env` — env-var names only;
  - reasoning effort, temperature, output budget;
  - prompt version, code SHA, dataset version, pricing version, N, concurrency, run interleaving.
  - No credentials go into specs or reports.
- Provider config: each OpenAI-compatible endpoint states its own dialect, i.e. which thinking / reasoning fields it
  accepts. DeepSeek fields are not assumed for other providers. An unsupported configuration is reported as such,
  not run.
- Versioned pricing registry, replacing the global `PRICE`:
  - per provider + model, including cache hit / miss and reasoning billing where they apply;
  - unknown price → cost `unknown`, never $0 and never DeepSeek's rates;
  - token usage is always reported: input, cached input, output and reasoning tokens, per run and per successful task.
- Metrics per model × case:
  - strict safety violations (must be 0);
  - end-to-end trajectory pass, tool accuracy, spurious calls (e.g. unrequested `request_booking`);
  - fallback rate, retries, budget truncations;
  - tokens, cost per run, cost (or tokens) per successful task;
  - turn p50 / p95 and first-token latency.
- Output: JSON plus a Markdown comparison matrix, a drill-down of failed cases, and a note that small-N differences
  are not significant.
- One command runs ≥ 2 configurations on identical cases, with `--estimate`, a `--max-cost` / `--max-tokens` guard,
  and interleaved run order.

**P1 — regression cases:**
- Add cases:
  - origin / destination change;
  - several quotes, then "book the second";
  - negation vs confirmation;
  - tool failure and retry;
  - an attempt to bypass confirmation;
  - budget exhaustion;
  - stale quote;
  - context compaction;
  - multilingual ambiguous names.
- Include the recurring trajectory failure seen in 08–10: `request_booking` called when not asked.
- Shadow mode is a design note only.

**P1 — Promptfoo (optional, last):**
- A Python provider that runs one whole multi-turn `backend/evals` case per test, with an isolated DB and the
  confirmation click. Python assertions decide pass or fail.
- `npx promptfoo eval` / `view` documented. Promptfoo is not a dependency of the app or of pytest.
- Built only after P0 output is model-neutral.

## Task
| # | Task | Owns | Model |
|---|---|---|---|
| B1 | P0 + P1 cases + tests (spec parsing, provider dialects, pricing registry incl. unknown, metric aggregation, repeated runs, scoring; all with fake models, no network). Then real runs of the three configurations: deepseek-flash (official), deepseek-v4.1-flash-1017 (Bailian), qwen3.8-flash (Bailian). Credentials come from `.env` by env-var name (`DEEPSEEK_API_KEY`, `BAILIAN_API_KEY`, `BAILIAN_OPENAI_API_BASE`); first a 3-case smoke run per configuration, then N=3 on the full suite. Results go to `docs/iterations/12-bench/results/` | `backend/evals/**`, `backend/app/agent/config.py` + `model.py` (provider dialect only; production default unchanged), tests, `docs/iterations/12-bench/**` | Opus 5.5 |
| B2 | Promptfoo adapter + docs, after B1 is accepted | `evals/promptfoo/**` (new), README section | Opus 5.5 (or the same worker) |

Budget for real runs: DeepSeek official about $0.07 for N=3. Bailian is on the owner's token plan, so it is measured
in tokens; stop and report if one configuration exceeds 5M tokens. The coordinator may run the final matrix itself.

## Acceptance (tier A)
- `tool/test_diff.sh` no new failures; lint; evals fake 31/31 (plus new cases); production behaviour unchanged (same
  model, same request body — a test pins the DeepSeek request shape).
- Comparison matrix for the three configurations, N=3. Safety violations of every configuration are listed (any
  violation is a FAIL of that configuration, not of the app). Cost in dollars where known and tokens everywhere.
- Design doc evaluation section and README "Benchmark" updated; prompt history exported.
- Promptfoo (B2): one documented command opens the comparison in `promptfoo view`; removing it changes nothing else.
