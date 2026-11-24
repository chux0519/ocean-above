# 08-runtime — acceptance (2026-11-24)

Scope: #49 (SDK POC, ADR, migration) and #41 (SQLite limits + benchmark). Plan: [plan.md](plan.md).

## #49
- **POC (phase 1):** OpenAI Agents SDK ([report](poc-openai-agents.md), PR #50, $0.0046) and Pydantic AI Slim
  ([report](poc-pydantic-ai.md), PR #52, about $0.03), same 15 checks on real DeepSeek plus a TCP-level fake server.
  Both passed the functional checks; neither met the "clearly less code" rule (about −5% each).
- **Decision:** owner chose Pydantic AI Slim (2026-11-24) over the coordinator's recommendation to keep the own loop;
  [ADR-001](../../design/adr-001-agent-runtime.md), amended after the migration with the measured boundary.
- **Migration (phase 2–3, PR #53):** `llm.py` replaced by `model.py` (`OpenAIChatModel`, DeepSeek dialect); `loop.py`
  drives `Agent.iter`; retries and request context stay ours; `Agent.cancel` closes the upstream stream, dispatches no
  further tools, drains started threads before releasing locks; zero outbound tracing by default, `tracer_provider`
  hook for #35. Pinned `pydantic-ai-slim==2.71.0`, +11 packages (no tiktoken / requests), about +33 MB unpacked.
  `backend/app/agent` 2237 → 2665 lines.
- **Independent review (Fable):** [review-runtime-sdk.md](review-runtime-sdk.md) — P0 none, P1 1 (retry count carried
  across steps), P2 9; all fixed with tests except P2-7 (cancel semantics), handed to #48. Mutation check: making the
  stream close a no-op turns the cancel and guard tests red.
- **Tests (source `d49afb2`, merged as `440bcb6`, verified by the coordinator):** 830 passed; `tool/test_diff.sh` no new
  failures; lint clean; evals fake 31/31; frontend vitest 89 passed; contract fixtures pass.
- **Real model (deepseek-flash, high):** 92/93 before review fixes ([migration/report.md](migration/report.md)) and
  30/31 after ([evals/real-after-review](migration/evals/real-after-review/report.md)); **0 safety violations**; latency
  in line with 06 (call p50 1.1–1.2 s, turn p50 2.2–2.4 s). The two failures were model behaviour (an extra confirmation
  card that booked nothing; a Chinese city name passed once before retrying in English). Cost about $0.09 in total.
- **B1–B7:** `core/`, `airline/`, `store/` business logic unchanged; booking model tests pass; no `book` tool.
- **Released** to the VPS at `963c9be`, public API e2e passed (see docs/deploy.md).
- **Docs:** design.md §2, §4.3, new §4.10; README; AGENTS.md dependency rule; docs/roadmap.md.

## #41 (PR #51, closed)
- Timing hook (off by default) in `store/db.py`; `backend/bench/`; measured run: 0 integrity failures, 0 BUSY / 5xx;
  single-worker CPU saturates first (~200 req/s); a `locations.db` rebuild inside a booking raises other writers'
  wait p99 to 2.8 s. Decision: keep SQLite + one worker for the demo; criteria in
  [docs/design/sqlite-concurrency.md](../../design/sqlite-concurrency.md).

## Not done here
- No browser e2e this round (frontend unchanged; API-level e2e on production instead).
- Docker image size measured by estimate (no docker on the dev machine); the homelab build succeeded.
