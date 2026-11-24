# Roadmap

Owner decisions and the order of work after the take-home MVP (07-ui, released 2026-11-24). Details live in the issues
and in each iteration's plan under `docs/iterations/`.

## Decision: move the generic agent loop to an agent SDK (2026-11-24)
The MVP runs a hand-written agent loop (`backend/app/agent/loop.py`, `llm.py`). The owner decided to move the generic
part of it to a mainstream agent SDK, for two reasons:
1. **Cancelling streamed output.** A user's Stop must cancel the backend run and close the upstream model stream, not
   just the browser's SSE reader. Native run cancellation in an SDK is preferred over a second hand-written
   cancellation layer.
2. **Systematic tracing.** Agent / LLM / tool spans from the SDK, exported through OpenTelemetry to a local Phoenix,
   instead of instrumenting a custom loop by hand.

Decided: **Pydantic AI Slim** (owner, 2026-11-24, after both POCs; evidence and boundaries in
`docs/design/adr-001-agent-runtime.md`). The SDK never gets booking authority: booking stays the user's click on a
confirm card (nonce, B1–B7), ReplyGuard still checks text before it is streamed, and app.db history, budgets and
concurrency limits stay our own code.

## Order
| # | Issue | Iteration | Depends on | Status |
|---|---|---|---|---|
| 1 | #49 SDK POC, ADR, runtime migration | 08-runtime | — | ADR-001: Pydantic AI Slim (2026-11-24); migration in progress |
| 2 | #48 backend run cancellation + Stop UI | 09-stop | #49 accepted | queued |
| 3 | #35 OpenTelemetry + local Phoenix | 10-otel | #49, #48 accepted | queued |
| 4 | #47 multi-model benchmark, optional Promptfoo (deepseek-flash official vs deepseek-v4.1-flash-1017 and qwen3.8-flash on Bailian; tokens when price unknown) | 11-bench | #49, #48 (traces from #35) | queued |
| — | #41 SQLite limits + benchmark, PostgreSQL criteria | 08-runtime (parallel) | — | done (PR #51, docs/design/sqlite-concurrency.md) |
| — | #11 / #12 thinking level, BYOK, thinking bench | bonus | — | not scheduled |

Each item starts only after the one before it is accepted.
