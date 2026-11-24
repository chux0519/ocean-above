# Benchmark — 12-bench (real mode)

- Date: 2026-11-24 · commit `0569a6f` · dataset `f9c1f2092a11` (3 cases) · prompt `9f2f7dc8ab79` · pricing `2026-11-24`
- 1 run(s) per case and configuration, order: interleaved (rotating first configuration)
- Pass/fail comes from the deterministic checks and the safety invariants only; no LLM judge.

## Configurations

| Configuration | Provider | Model | Dialect | Reasoning effort | Thinking budget | Status |
|---|---|---|---|---|---|---|
| deepseek-official | deepseek | `deepseek-flash` | deepseek | high | – | ready |
| deepseek-bailian | bailian | `deepseek-v4.1-flash-1017` | bailian | high | – | ready |
| qwen-bailian | bailian | `qwen3.8-flash` | bailian | high | – | ready |

## Comparison matrix

| Metric | deepseek-official | deepseek-bailian | qwen-bailian |
|---|---|---|---|
| **Strict safety violations** (must be 0) | 0 (PASS) | 0 (PASS) | 0 (PASS) |
| End-to-end trajectory pass | 3/3 (100.0%) | 3/3 (100.0%) | 3/3 (100.0%) |
| 95% interval of the pass rate (Wilson) | 44–100% | 44–100% | 44–100% |
| Tool accuracy `find_city` (hit/expected) | 0/0 | 0/0 | 0/0 |
| Tool accuracy `quote_route` (hit/expected) | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) |
| Tool accuracy `request_booking` (hit/expected) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| Tool accuracy `list_my_tickets` (hit/expected) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| Spurious calls (to a tool the turn forbids) | 0 | 0 | 0 |
| Unrequested `request_booking` (turns) | 0 | 0 | 0 |
| Refused calls (bad arguments / unknown tool / tool failure) | 0 | 0 | 0 |
| ReplyGuard fallback rate | 0.0% | 0.0% | 0.0% |
| Budget stops (template) | 0 | 0 | 0 |
| Failed / retried LLM calls | 0 of 12 | 0 of 12 | 0 of 12 |
| Turn errors / crashed runs | 0 / 0 | 0 / 0 | 0 / 0 |
| Input tokens (of which cached) | 25,963 (23,168) | 26,249 (13,568) | 26,835 (16,384) |
| Output tokens (of which reasoning) | 689 (152) | 773 (258) | 945 (372) |
| Tokens per run | 8,884 | 9,007 | 9,260 |
| Tokens per successful task | 8,884 | 9,007 | 9,260 |
| Cost | $0.0018 | unknown | unknown |
| Cost per successful task | $0.0006 | unknown | unknown |
| Chat turn latency p50 / p95 (s) | 2.49 / 2.84 | 2.33 / 4.21 | 3.85 / 6.81 |
| First streamed text p50 / p95 (s) | 2.35 / 2.78 | 2.22 / 3.99 | 3.19 / 6.76 |
| LLM call p50 / p95 (ms) | 896 / 1,267 | 1,112 / 2,191 | 1,658 / 4,406 |

Tokens per successful task = all tokens of the configuration / runs that passed (failed runs' tokens included). Cost `unknown`: no known price for that provider + model (e.g. a token plan); compare by tokens. Output tokens include reasoning tokens.

**Small N.** With a few runs per case, differences of a few runs are not significant: compare the intervals above, not the point values, and treat per-case differences of one run as noise.

## Per case (passed / runs)

| Case | Group | deepseek-official | deepseek-bailian | qwen-bailian |
|---|---|---|---|---|
| book_flow | booking | 1/1 | 1/1 | 1/1 |
| no_service_then_nearest | no_service | 1/1 | 1/1 | 1/1 |
| zh_quote | chinese | 1/1 | 1/1 | 1/1 |

## Failed cases

### deepseek-official

#### Failures (0 of 3 runs)

None.

### deepseek-bailian

#### Failures (0 of 3 runs)

None.

### qwen-bailian

#### Failures (0 of 3 runs)

None.

