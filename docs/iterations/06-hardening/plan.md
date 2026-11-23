# 06-hardening — agent runtime budgets, eval regressions, opt-in tracing

User request (2026-11-23, verbatim): 「刚刚我让别的 agent 做了一次review，你可以看看这个大 issue：
https://github.com/chux0519/ocean-above/issues/31 p0 / p1 可以拆分成子 issue 然后整改，p2 排着做」

## Take-home check
- Req. 5–7 (cheapest route, numbers, confirmation): budgets must never book or change quote state; B1–B7, the reference
  routing cross-check and ReplyGuard stay green. Req. 1 (natural language): the two eval regressions improve it.
- Deliverable "design doc: limitations and failure modes": budget semantics and tracing failure modes go into
  design.md. No conflict.

## Sub-issues
| # | Scope | Depends | Model |
|---|---|---|---|
| #32 P0a | per-turn budgets: tool calls per step/turn, turn token cap, config validation | — | Opus 5.5 |
| #33 P0b | global active-turn concurrency, DB/thread timeouts (documented limits) | #32 (same files) | Opus 5.5 |
| #34 P1 | eval regressions: nearest-city follow-up, book-in-one-message; real-model before/after | #32 | Opus 5.5 |
| #35 P2 | opt-in OpenTelemetry + local Phoenix profile (queued) | #33, #34 | Opus 5.5 |

Order: #32 now (parallel with L4 #29, different files) → #33 and #34 (one each, ≤ 2 workers) → #35. Each PR gets the
coordinator's review; #32 and #33 get one Fable read-only review together (runtime safety). The 05 launch (deploy +
two-user e2e) proceeds once L4 is merged; it does not wait for 06.

## Acceptance
As #31 "Acceptance criteria" 1–7.
