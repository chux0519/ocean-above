# Agent notes for ocean-above

Practices: pfstack (potafree stack). Read this file first; details live in the linked docs.

## Project
- **ocean-above**: natural-language flight booking web app for the airline Ocean Above (take-home; brief in
  `Quotr Agent Dev Engineer take-home.md`).
- `locations.db` (SQLite) is the **single source of truth** for cities, hubs and flights. Never hard-code or
  copy its data elsewhere.
- Stack: frontend Next.js + TypeScript; backend FastAPI (Python). LLM: an OpenAI-compatible API (DeepSeek
  flash for the demo), configured by env vars (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), never committed.
- **The LLM never computes.** Routes, distances, prices, nearest-served-city and bookings are deterministic
  backend code; the LLM only parses intent, calls tools, and phrases results the algorithms produced.
  Every number shown to the user comes from a tool result.
- **Nothing is booked without explicit user confirmation** of the full route (every leg and transfer city),
  distance and price. Booking is triggered **only by the user clicking** a confirm card (one-time nonce);
  the LLM can only make the confirm card appear (`request_booking`), never book.
- Reply text is checked by value: every number and every in-db city name in LLM text must appear in this
  turn's records (or the user's message); otherwise the reply is replaced by a code template.
- Dev: servers bind `0.0.0.0`, any port. Auth in dev: one fixed account from env (no Google).
- Delivery and deploy: `docker compose up` (web + api + Caddy proxy, one exposed port). Demo:
  `ocean-above.potafree.net` now runs on a VPS (since 2026-11-23; images built elsewhere and shipped, see deploy.md).
  Google login and multi-user flow are finished before the demo goes public. Homelab deploy questions go to
  the hc agent named "potafree.net" on the homelab machine. Details: [docs/deploy.md](docs/deploy.md).
- Deliverables include `prompt history/` with the **raw, unedited** agent sessions (only secrets removed),
  so it is excluded from the forbidden-names lint. Export with `tool/export_sessions.py` at the end of every
  iteration (main session, workers, reviewers, the homelab deploy agent). README and the design doc are updated
  every iteration, not at the end.

## Roles
- **Coordinator** (main session): talks with the user, writes plans and acceptance criteria, reviews every diff
  and screenshot, merges, releases. Owns shared resources: the dev servers, `locations.db`, any deploy.
- **Implementers** (workers in hc): one task = one branch = one worktree = one writer. They code, run tier A,
  write implementation notes. They never launch other agents, touch shared resources, merge or release.
- Launch: `tool/spawn_worker.sh <name> <worktree> [sonnet]`. Model by difficulty: mechanical work → a fast
  model; design, concurrency, protocol, security, native → the strongest model. State the choice in the plan.
- Machine load: at most 4 workers (user, 2026-11-23: the machine is idle enough) and 1 browser-automation session at a time; heavy commands run with `nice -n 10`.

## Never lose work
- Every change goes through a branch and a PR; the coordinator merges and pushes main right away.
- Implementers: each small verifiable unit ends in check → commit → push. Nothing older than a few minutes
  lives only on this machine.
- Every report and every pause writes `docs/iterations/<id>/round-NN/STATE.md` (done, in progress, next,
  open decisions, how to verify).

## Plan first, bounded rounds
- Plan + acceptance criteria → user confirms → implement. Scope changes need a new confirmation.
- **Before every iteration**, check the plan against `Quotr Agent Dev Engineer take-home.md` requirement by
  requirement; where they conflict the take-home wins — adjust the plan and record the check in the plan's
  "Take-home check" section.
- At most 3 implement → accept → review rounds per iteration; then stop and ask the user.
- An agent saying "done" is not evidence. Record the exact source state that was tested.

## Evidence tiers
- **A (default):** host-rendered screenshots + analyze + lint + test diff. Web UI screenshots and UI tests are
  driven with ego-browser (ego lite), at a desktop and a mobile viewport (e.g. 1440×900 and 390×844).
- **B:** one device / one theme / relevant scenes, only when A cannot prove it (keyboard, timing, webview,
  gestures, native). Coordinator only. Timing issues: record once, cut frames at PHASE marks.
- **C:** full matrix once before a store submission or external release.
- Web only, no dedicated devices. Tier A screenshots come from a local browser against the dev server.

## Design
- [frontend/DESIGN.md](frontend/DESIGN.md) (the UI design system) is the single source of truth for visual and
  interaction rules; only the coordinator edits it. The root [DESIGN.md](DESIGN.md) is the take-home design doc.
- UI reviews use **Impeccable** (https://github.com/pbakaus/impeccable): `/impeccable critique <target>` (UX,
  scored) and `/impeccable audit <target>` (a11y, performance, theming, responsive; P0–P3) on narrow targets,
  plus `npx impeccable detect` on `frontend/` sources and on the running dev-server URL (rendered-only rules
  such as contrast need the URL). P0–P1 are fixed in the same round. Install once the frontend exists
  (`npx impeccable install`, project scope, accept the design hook).
- Component library / CSS framework allowed (e.g. shadcn/ui + Tailwind CSS); UI built with them is also
  reviewed with the matching skill or guidelines for that library, in addition to Impeccable.
- Mobile first-class: every screen works at phone width (≥ 360 px), touch targets ≥ 44×44, no horizontal
  scroll; each UI acceptance includes mobile screenshots.
- Every screenshot gets a review; obvious defects are fixed in the same round; advice that conflicts with
  frontend/DESIGN.md goes to the user as a "design conflicts" table.

## Rules, invariants, numbers
- New pitfall → a rule in [docs/review_rules.md](docs/review_rules.md), and in `tool/review_lint.py` if it can
  be checked automatically. Every review runs `python3 tool/review_lint.py`.
- Timing / state bugs → a numbered rule in [docs/design/state_invariants.md](docs/design/state_invariants.md)
  first, then a model-based test (`tool/model_check.py` pattern), then a named regression case.
- Only new failures count (`tool/test_diff.sh`). A wall-clock benchmark that fails in the full run is rerun
  alone before it counts.

## Testing
- **Deterministic core** (DB loading, distance, graph search, pricing, nearest served city, booking rules):
  unit tests plus property tests against a brute-force reference on `locations.db` and on generated graphs
  (pytest + hypothesis). Expected numbers in tests come from the reference, never from an LLM.
- **LLM layer** has its own eval suite (`backend/evals/`), separate from unit tests because it costs money and
  is non-deterministic:
  - *tool-call accuracy*: per scenario, the right tool with valid, correct arguments; report the success rate
    over N runs per case, with a threshold per tool;
  - *trajectory tests*: whole multi-turn conversations checked against the expected tool-call sequence and
    invariants — e.g. `book` never happens without a preceding user confirmation of that exact quote; every
    number in the reply appears in a tool result; unserved cities lead to the nearest-served suggestion;
  - deterministic replay: the agent loop runs against a scripted fake LLM in the normal test suite, so loop,
    tool dispatch and confirmation gating are tested for free; real-model evals run on demand and record
    model, date, pass rate and cost in the iteration's acceptance notes.
- **Numbers:** one canonical pricing function (Decimal, prec 50, explicit context, stored to 30 dp); round
  only when displaying. Never feed a rounded value back into a calculation. Call them "high-precision", not
  "exact".
- **Freshness before booking:** a booking re-reads `locations.db` (fingerprint check), recomputes the quoted
  route and compares it with the stored quote and the business DB; any difference → no booking, re-quote and
  ask the user to confirm again.
- Booking/confirmation state rules get numbered invariants in `docs/design/state_invariants.md` and a
  model-based test.

## Dependencies and prior art
- Minimal dependencies; no big frameworks (UI component libraries such as shadcn/ui + Tailwind are allowed).
- Agent runtime (user, 2026-11-24, issue #49): a mainstream agent SDK may run the generic loop (model streaming, tool
  dispatch, run lifecycle, cancellation, tracing spans). Why: user Stop must cancel the backend run and the upstream
  model stream, and tracing should feed a standard system (OpenTelemetry → Phoenix) instead of hand-written code.
  Which SDK is decided by evidence in `docs/design/adr-001-agent-runtime.md`. Always our own code, never the SDK's:
  booking (user click + nonce, B1–B7, no `book` tool), ReplyGuard before any text is streamed, app.db history and
  compaction, the SSE contract, per-turn budgets and concurrency limits. No remote trace export by default; no
  LangChain. See [docs/roadmap.md](docs/roadmap.md).
- For well-defined deterministic algorithms (shortest path etc.), prefer a stable, fast, well-known library
  if it fits (e.g. `networkx` / `scipy.sparse.csgraph`) over a hand-rolled one; it must still be covered by
  our reference tests. State the choice and why in the plan.
- Before building something non-trivial, search GitHub for a mature solution first and note the result
  (adopted / rejected and why) in the plan.
- Every new dependency is named in the plan with its reason.

## Authorizations
- Standing (no need to ask): install dependencies locally, run tests / lint / dev servers, commit on branches; deploy
  releases of `ocean-above.potafree.net` to the production VPS (user, 2026-11-24): only merged `main` commits whose
  tests pass; build images off the VPS, ship, `up -d --no-build --pull never`, verify, and log commit + checks in
  docs/deploy.md.
- Standing since 2026-11-23: push branches and `main` to the private repo `github.com/chux0519/ocean-above`.
- Always ask: force-push or history rewrites, making the repo public, opening PRs to other repos, any other deploy
  (other hosts/domains/services), deleting data, anything public or
  hard to undo.
- An authorization never extends to a different context. If the tool refuses an action, ask; don't work around.

## Hard rules
- Never print tokens, passwords or review credentials; read them inside scripts only.
- Fixtures use `example-*` hosts only; no real host or user names in the repo or public exports.
- `rm -rf "${D:?}"` form only; look before deleting or overwriting.
- Don't change global toolchains or shared SDKs; ask the user to apply patches.
- Temporary LAN previews serve a copied folder only and are stopped after use; generated HTML has
  `<meta charset="utf-8">`.
- Replies to the user: short, action items first, in the user's language.
