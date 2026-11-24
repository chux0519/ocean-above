# ADR-001: Agent runtime — adopt Pydantic AI Slim

- Status: **accepted** (2026-11-24, decided by the owner after both POCs)
- Issue: #49 · Roadmap: [docs/roadmap.md](../roadmap.md) · Evidence:
  [poc-pydantic-ai.md](../iterations/08-runtime/poc-pydantic-ai.md),
  [poc-openai-agents.md](../iterations/08-runtime/poc-openai-agents.md), code in `spikes/`

## Context
The MVP runs a hand-written agent loop (`backend/app/agent/loop.py` + `llm.py`, an httpx client for the
OpenAI-compatible streaming protocol). Two next steps motivated a move to an agent SDK (owner, 2026-11-24):
a user **Stop** that cancels the backend run and the upstream model stream (#48), and **systematic tracing** through
OpenTelemetry to a local Phoenix (#35). Two candidates were tested on the real DeepSeek endpoint plus a fake
OpenAI-compatible server that records TCP close, with the same 15 checks.

## Evidence
| | Own loop (today) | Pydantic AI Slim 2.71 | OpenAI Agents SDK 0.25 |
|---|---|---|---|
| Streaming, sequential + parallel tool calls on DeepSeek | yes | yes (set sequential tools, `strict` off) | yes |
| Cancel → upstream TCP closed (median) | 0.1 ms (`aclose`) | 1.6–1.8 ms fake server, 0.6 ms DeepSeek | 0.4 ms |
| New LLM requests / tools after cancel | 0 (no Stop path yet) | 0 | 0 |
| Waits for started sync work on cancel | our `Threads.drain` | no, keep `Threads` | no, keep `Threads` |
| ReplyGuard before text is streamed | yes | yes (`iter` + `node.stream`) | yes |
| Budgets (steps, tools per step / turn, token soft cap) | ours | timeout and per-call output built in; rest via our `budget.py` | steps and timeout built in; rest via hooks |
| Tracing | none | OpenTelemetry native; **no outbound export by default** | own span model; **remote export to OpenAI on by default**, must be replaced |
| Runtime deps (packages) | 22–23 | +16 | +23 |
| Install / image delta | — | +39 MB unpacked, +7 MB compressed | about +56 MB |
| Cold import | 0.10–0.17 s | +0.32 s | +0.45 s |
| `backend/app/agent` net LOC | 2237 | about −120 (−5%) | about −100 (−5%) |
| Real-model cost of the POC | — | about $0.03 | $0.0046 |

First-token latency was within noise for all three.

## Decision
Adopt **Pydantic AI Slim** (`pydantic-ai-slim[openai]`, pinned) for the generic runtime: the model client (HTTP
stream, chunk parsing, tool-call assembly, provider request fields, per-call retry / timeout), tool dispatch and run
cancellation. Its tracing is OpenTelemetry from the start, with no outbound export unless we configure an exporter.
That fits #35 directly. The dependency cost is the smaller of the two SDKs.

Neither SDK met the plan's "clearly less code to maintain" rule: each removes about 5% net. The owner chose
adoption anyway, for standard cancellation and tracing semantics that a widely used runtime maintains. Without the
SDK, the same features would be hand-written and maintained here. The recommended alternative, keeping the own loop,
was considered and rejected on these grounds.

## Boundaries — what stays our code
- **Booking:** no `book` tool. Tickets only from the user's click on a presented confirm card with a one-time nonce
  (`airline/`, B1–B7). `request_booking` only shows the card.
- **ReplyGuard** runs on buffered sentences before any text reaches the client; the SDK's output validators are not
  a substitute.
- **Tools:** `Tool.from_schema` over our `TOOL_SCHEMAS`; validation, structured errors and `Outcome` stay in
  `tools.py`. `user_id` / `conversation_id` come from trusted run deps, never from model arguments. Tools run
  **sequentially** and in call order, and their sync work goes through our `Threads` (drained before locks are
  released, C2).
- **History:** app.db history and compaction (`context.py`), converted to `message_history`. No SDK session store.
- **SSE contract** (`status`, `card`, `delta`, `replace`, `done`, `error`) is unchanged; SDK events are mapped to it.
- **Budgets and concurrency:** `budget.py`, turn slots, per-user caps, conversation locks, `llm_calls` records and the
  usage estimate when usage is missing (cancelled or cut streams).
- **Error mapping:** SDK exceptions (`ModelAPIError`, `UnexpectedModelBehavior`, timeouts, tool exceptions) map to
  today's fallbacks and templates. A stream that ends without `finish_reason` is not treated as `stop`.

## Consequences
- `llm.py`'s httpx client and most of `_steps` / `_llm_step` are deleted. The test doubles (`FakeLLM`,
  `ScriptedLLM`) move onto `FunctionModel`. The loop and runtime-review tests are kept and must pass unchanged in
  intent.
- There are two HTTP stacks: `httpx` (Google JWKS) and `httpx2` (openai 3.x). Proxy settings come from the
  environment (`trust_env`).
- Fast-moving 2.x API: pin the version. Upgrades run the full test suite plus `spikes/pydantic-ai/poc/checks_fake.py`.
- Settings that must be explicit: sequential tool execution, `strict` off for our schemas, DeepSeek thinking fields
  via `extra_body` (same request as today), `PYDANTIC_AI_NO_BANNER=1`, and `iter` + `run.cancel()` rather than
  `run_stream`.
- #48 builds Stop on this runtime: an app-owned `run_id`, cancelling the run task, and `Threads` drain. #35 adds the
  OTLP exporter, business spans and the Phoenix profile on its OpenTelemetry spans.
- Rollback: the migration is one PR; reverting it restores the own loop.
