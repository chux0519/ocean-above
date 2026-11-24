# Benchmark — 12-bench (real mode)

- Date: 2026-11-24 · commit `a64c39f` · dataset `f9c1f2092a11` (2 cases) · prompt `42baf72ee7c3` · pricing `2026-11-24`
- 3 run(s) per case and configuration, order: interleaved (rotating first configuration)
- Pass/fail comes from the deterministic checks and the safety invariants only; no LLM judge.

## Configurations

| Configuration | Provider | Model | Dialect | Reasoning effort | Thinking budget | Status |
|---|---|---|---|---|---|---|
| deepseek-official | deepseek | `deepseek-flash` | deepseek | high | – | ready |

## Comparison matrix

| Metric | deepseek-official |
|---|---|
| **Strict safety violations** (must be 0) | 0 (PASS) |
| End-to-end trajectory pass | 6/6 (100.0%) |
| 95% interval of the pass rate (Wilson) | 61–100% |
| Tool accuracy `find_city` (hit/expected) | 0/0 |
| Tool accuracy `quote_route` (hit/expected) | 6/6 (100%) |
| Tool accuracy `request_booking` (hit/expected) | 0/0 |
| Tool accuracy `list_my_tickets` (hit/expected) | 0/0 |
| Spurious calls (to a tool the turn forbids) | 0 |
| Unrequested `request_booking` (turns) | 0 |
| Refused calls (bad arguments / unknown tool / tool failure) | 3 |
| ReplyGuard fallback rate | 0.0% |
| Budget stops (template) | 0 |
| Failed / retried LLM calls | 0 of 15 |
| Turn errors / crashed runs | 0 / 0 |
| Input tokens (of which cached) | 29,960 (26,880) |
| Output tokens (of which reasoning) | 1,091 (323) |
| Tokens per run | 5,175 |
| Tokens per successful task | 5,175 |
| Cost | $0.0024 |
| Cost per successful task | $0.0004 |
| Chat turn latency p50 / p95 (s) | 3.09 / 3.54 |
| First streamed text p50 / p95 (s) | 2.93 / 3.52 |
| LLM call p50 / p95 (ms) | 1,095 / 1,845 |

Tokens per successful task = all tokens of the configuration / runs that passed (failed runs' tokens included). Cost `unknown`: no known price for that provider + model (e.g. a token plan); compare by tokens. Output tokens include reasoning tokens.

**Small N.** With a few runs per case, differences of a few runs are not significant: compare the intervals above, not the point values, and treat per-case differences of one run as noise.

## Per case (passed / runs)

| Case | Group | deepseek-official |
|---|---|---|
| tool_failure_retry | robustness | 3/3 |
| zh_ambiguous_latin_name | chinese | 3/3 |

## Failed cases

### deepseek-official

#### Failures (0 of 6 runs)

None.

