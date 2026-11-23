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
  distance and price.
- Dev: servers bind `0.0.0.0`, any port. Auth in dev: one fixed account from env (no Google).
- Delivery and deploy: `docker compose up` (web + api + Caddy proxy, one exposed port). Demo:
  `ocean-above.potafree.net` on the homelab behind Cloudflare Tunnel; may move to a VPS with the same compose.
  Google login and multi-user flow are finished before the demo goes public. Homelab deploy questions go to
  the hc agent named "potafree.net" on the homelab machine. Details: [docs/deploy.md](docs/deploy.md).
- Deliverables include `prompt history/` with the **raw, unedited** agent sessions (only secrets removed),
  so it is excluded from the forbidden-names lint.

## Roles
- **Coordinator** (main session): talks with the user, writes plans and acceptance criteria, reviews every diff
  and screenshot, merges, releases. Owns shared resources: the dev servers, `locations.db`, any deploy.
- **Implementers** (workers in hc): one task = one branch = one worktree = one writer. They code, run tier A,
  write implementation notes. They never launch other agents, touch shared resources, merge or release.
- Launch: `tool/spawn_worker.sh <name> <worktree> [sonnet]`. Model by difficulty: mechanical work → a fast
  model; design, concurrency, protocol, security, native → the strongest model. State the choice in the plan.
- Machine load: at most 2 workers and 1 browser-automation session; heavy commands run with `nice -n 10`.

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
- [DESIGN.md](DESIGN.md) is the single source of truth; only the coordinator edits it.
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
  DESIGN.md goes to the user as a "design conflicts" table.

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
- **Numbers:** compute and store exact values (full-precision decimals from integer coordinates); round only
  when displaying. Never feed a rounded value back into a calculation.
- **Freshness before booking:** a booking re-reads `locations.db` (fingerprint check), recomputes the quoted
  route and compares it with the stored quote and the business DB; any difference → no booking, re-quote and
  ask the user to confirm again.
- Booking/confirmation state rules get numbered invariants in `docs/design/state_invariants.md` and a
  model-based test.

## Dependencies and prior art
- Minimal dependencies; no big frameworks (UI component libraries such as shadcn/ui + Tailwind are allowed). The LLM agent loop (messages, tool schemas, tool dispatch,
  confirmation gate) is our own code over a plain OpenAI-compatible HTTP client — no LangChain, no agent SDKs.
- For well-defined deterministic algorithms (shortest path etc.), prefer a stable, fast, well-known library
  if it fits (e.g. `networkx` / `scipy.sparse.csgraph`) over a hand-rolled one; it must still be covered by
  our reference tests. State the choice and why in the plan.
- Before building something non-trivial, search GitHub for a mature solution first and note the result
  (adopted / rejected and why) in the plan.
- Every new dependency is named in the plan with its reason.

## Authorizations
- Standing (no need to ask): install dependencies locally, run tests / lint / dev servers, commit on branches; deploy
  the test demo `ocean-above.potafree.net` (homelab via Cloudflare Tunnel: tunnel, DNS record, compose up/restart).
- Always ask: `git push`, opening PRs, any other deploy (other hosts/domains, the VPS), deleting data, anything public or
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
