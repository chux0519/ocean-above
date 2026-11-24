# 10-otel — acceptance (2026-11-24)

Scope: #35 (and the last open part of #31). Plan: [plan.md](plan.md); details: [STATE.md](STATE.md).

- **Built (PR #56):** `app/tracing.py`, off by default (the OTel SDK is not even imported). When on: one trace per
  turn, rooted at `agent.turn`, with Pydantic AI's own `invoke_agent` / `chat` / `execute_tool` spans
  (`include_content=False`) plus business spans `airline.quote_route` → `core.routing`, `reply_guard`, `run.stop`.
  A booking click has its own trace: `airline.book` → `booking.recheck` → `core.routing`. OTLP/HTTP batch export.
  Phoenix 22.0.1 sits behind compose profile `tracing`, bound to loopback, with its own telemetry off.
  `locations.db` reads now have a 5 s deadline (#35 comment).
- **Privacy:** spans carry codes, counts, durations and opaque ids only. The conversation id appears only as an HMAC
  keyed with a server secret, and invented tool names become `unknown_tool`. A redacting layer sits in front of the
  exporter. Scans of the Phoenix data found 0 hits for message text, city names, emails, nonces, cookies or keys
  (`evals/privacy-scan-*.json`). Local endpoints bypass HTTP proxies.
- **Independent review (Fable):** [review-otel.md](review-otel.md). No P0. P1: spans to a local endpoint went
  through an HTTP proxy. 9 P2s, including `_trace_end` running before the run was retired. All fixed with tests.
  Mutation testing killed 15 of 16 mutants; the survivor is equivalent on CPython, with evidence in STATE.
- **Tests (merged `a82cc91`, verified by the coordinator):** backend 955 passed, `tool/test_diff.sh` no new
  failures, lint clean, evals fake 31/31 (also with tracing on).
- **Real model with tracing on (N=1):** 30/31, then 28/31 after the review fixes, both with 0 safety violations
  ($0.0223 / $0.0228). All failures are the known trajectory pattern: the model calls `request_booking` when it was
  not asked to (`quote_details`, `false_booked_prompt`), or passes a Chinese city name before retrying in English.
  These show a confirmation card at most and never book. Tracked for the #47 benchmark and prompt work.
- **Screenshots:** Phoenix trace of a quoted itinerary and of a stopped turn (`screenshots/`).
- **Released** `73ba50e` with tracing off; production e2e passed (docs/deploy.md).
- **Docs:** design §4.11, README "Tracing", `.env.example`.
