# 10-otel — opt-in OpenTelemetry tracing + local Phoenix (#35)

User request (2026-11-24): roadmap item 3, "#35 — OpenTelemetry + Phoenix tracing", after #49 and #48 were accepted
(08-runtime and 09-stop acceptance). Order and reasons: docs/roadmap.md. Scope: #35, #31 P2, the #35 comment
(locations.db read deadline).

## Take-home check (2026-11-24)
| Requirement | Effect | Result |
|---|---|---|
| Booking only after confirmation; numbers from code | tracing only observes; exporter failure never changes chat or booking (tests) | consistent |
| Privacy (our legal page: messages go to DeepSeek only) | default off; when on, spans carry no prompts, replies, tool outputs, emails, names, nonces, cookies or keys; export only to a configured local endpoint | consistent; legal text unchanged because production keeps tracing off |
| `docker compose up` for reviewers | Phoenix only behind a compose profile, never started by default | consistent |
No conflict.

## Design
- **Spans:** Pydantic AI's own OpenTelemetry spans (run / chat / execute_tool) through the `tracer_provider` hook from
  08, with `include_content=False`; plus business spans where the SDK has none: `agent.turn` (root; run_id,
  conversation id hash, outcome: completed / cancelled / failed / fallback), `agent.llm_attempt` if retries are not
  visible otherwise, `reply_guard` (pass / fallback category), `airline.quote_route`, `core.routing` (algorithm,
  network fingerprint, path length), `airline.book` (status only), `booking.recheck`, `run.stop` (stop latency, drain
  time). No duplicate spans for the same operation.
- **Switch:** `OTEL_TRACING_ENABLED` (default off) + standard `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP/HTTP). Off → no
  tracer provider, no exporter, no background thread. Batch export in the background; exporter down → spans dropped,
  chat and booking unaffected.
- **Phoenix:** compose profile `tracing` (`docker compose --profile tracing up`), UI at `http://localhost:6006`,
  local only, no auth needed. How spans render in Phoenix (GenAI semantic conventions vs OpenInference) is checked by
  the worker; an OpenInference span processor is allowed if Phoenix needs it.
- **Also (#35 comment):** an operation deadline for `locations.db` reads in `core/network.py`, like app.db's progress
  handler, if cheap.
- **New dependencies (allowed):** `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`; optionally
  `openinference-instrumentation-pydantic-ai` (state why). Listed in the PR with sizes.

## Task
| # | Task | Owns | Model |
|---|---|---|---|
| T1 | Everything above; tests (off by default: no provider/exporter; on with an in-memory exporter: one quote turn gives a root with the expected nested spans and attributes; no sensitive values in any attribute or event (scan for the user's message text, email, nonce, cookie, key); exporter raising or endpoint down → same chat and booking results; Stop shows `run.stop` and a cancelled root); end-to-end with a local Phoenix (screenshot of one quoted itinerary trace and one stopped turn); README "Tracing" section | `backend/app/**` (tracing module + span calls; no business-logic change), `backend/pyproject.toml`, `uv.lock`, `docker-compose.yml` (profile only), `.env.example`, tests, `docs/iterations/10-otel/**` | Opus 5.5 |

No Fable review planned (no booking or concurrency change); the coordinator reviews the diff and the privacy scan.
If T1 has to touch booking, locking or the agent loop beyond adding spans, a Fable review is added.

## Acceptance (tier A)
- `tool/test_diff.sh` no new failures; lint; B1–B7 model tests; evals fake 31/31; one real-model evals run (N=1,
  `--max-cost 0.25`) with tracing **on** against a local collector, 0 safety violations.
- Phoenix screenshots: a quoted itinerary (agent → chat → tool → airline / routing spans) and a stopped turn.
- Production stays tracing-off (no new env on the VPS); release and verify health + one quote.
- design.md (observability section, failure modes), README, `.env.example`, prompt history updated.
