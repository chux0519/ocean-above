# 12-bench B2 — Promptfoo (optional) — STATE

Task: #47 B2 (plan.md "P1 — Promptfoo"), after B1 was merged (`71a54b9`). Branch `it12-promptfoo`.

## Done
- `evals/promptfoo/` (new, optional, own `package.json`):
  - `provider.py`: Promptfoo Python provider. **One test = one whole multi-turn case** from `backend/evals`, played by
    `evals.runner.play`: a private copy of locations.db and its own app.db, the real airline, agent loop and
    ReplyGuard, every chat turn and every confirmation click (`Airline.book`, as the user's click), `EditDb` steps
    included. The output is the full transcript (user turns, tool calls with codes, cards, replies, ticket counts,
    clicks and what they created) followed by a JSON verdict block.
  - `assertions.py`: two Python assertions, `safety` (the invariants: no ticket without the click, a click books
    exactly once, no false "booked" claim, provenance of numbers and cities, well-formed stream; a crash fails) and
    `trajectory` (end-to-end pass). They only read the verdict computed by the deterministic checks; no LLM judge,
    no backend import.
  - `gen.py`: writes the config from the experiment spec and the case list (matrix = configuration × case), so it
    cannot drift; committed `promptfooconfig.fake.json` (3 configurations × 45 cases, scripted model) and
    `promptfooconfig.json` (real). A test checks the committed files equal the generated ones.
  - `pf.sh`: runs the pinned local Promptfoo with telemetry, update checks, sharing, remote generation and the
    response cache off (`PROMPTFOO_DISABLE_TELEMETRY=1`, `PROMPTFOO_DISABLE_UPDATE=1`, `PROMPTFOO_DISABLE_SHARING=1`,
    `PROMPTFOO_DISABLE_REMOTE_GENERATION=1`, `PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=1`,
    `PROMPTFOO_DISABLE_SHARE_EMAIL_REQUEST=1`, `PROMPTFOO_CACHE_ENABLED=false`; configs also say `"sharing": false`),
    its database in `evals/promptfoo/.promptfoo/` (git-ignored), and the backend's Python (`PROMPTFOO_PYTHON`).
  - `scrub.py`: makes an `eval -o` export repository-relative (Promptfoo records the absolute `basePath`).
  - Guards in real mode: per configuration, `max_cost` (USD, priced configurations) and `max_tokens`, tallied in a
    file ledger under a lock (Promptfoo may run several Python workers); a run that would start above a guard
    returns `skipped: … reached its guard` without a request.
- `backend/evals/runner.py`: `RunResult.clicks` (step, card existed, tickets created, expected) so the transcript
  shows what each click did. Additive; nothing else changed.
- `backend/tests/test_promptfoo_adapter.py` (7 tests, no Promptfoo needed): a test is the whole conversation (every
  user turn in order, both clicks created one ticket); the stale click shows 0 tickets; a bad trajectory fails
  `trajectory` but not `safety`, an invariant violation fails `safety`; unknown case / configuration, missing
  credentials and both guards (no request made); committed configs = generated, Python assertions only, no URLs or
  keys; Promptfoo is not in `backend/pyproject.toml` or `frontend/package.json`.

## Dependency
`promptfoo` 0.126.0, pinned in `evals/promptfoo/package.json` + `package-lock.json` (devDependency of that folder
only; not the app, not pytest, not the frontend). `node_modules` is 652 MB, git-ignored. Removing `evals/promptfoo/`
plus the test file and the additive `RunResult.clicks` field changes nothing else.

## Runs (tested source: `95a0305` + the `tokenUsage` change below, then committed)
| Run | Command | Result |
|---|---|---|
| Fake, 3 configurations × 45 cases | `bash evals/promptfoo/pf.sh eval -c promptfooconfig.fake.json -o …/results/fake.json` | 135/135 pass, safety 45/45 per configuration, 12 s, $0 — [results/fake.json](results/fake.json) |
| Real, deepseek-official only, N=1 | `OCEAN_ENV_FILE=… bash evals/promptfoo/pf.sh eval -c promptfooconfig.json --filter-providers '^deepseek-official$' -o …` (guard `max_cost` 0.25) | 43/45 pass, **safety 45/45**, 602,508 tokens, **$0.0366**, 1 min 13 s — [results/real-deepseek-official.json](results/real-deepseek-official.json) |

The two real failures are the patterns already analysed in B1 (`../STATE.md`): `quote_details` (an unrequested
`request_booking`, a card only) and `stale_quote` (`request_booking` twice on the fresh quote). Promptfoo exits
100 when a test fails; that is its normal "some tests failed" code.

Exports are scrubbed (0 absolute paths); a scan of `B2/` for the values of the key / base-URL variables found 0 hits.

## Screenshots (ego-browser, `promptfoo view`, local only)
- [screenshots/promptfoo-view.png](screenshots/promptfoo-view.png): the real deepseek-official run — 95.56% passing,
  safety 100% (45/45), trajectory 43/45, $0.0366; each cell is a whole conversation.
  Taken before one fix: Promptfoo labelled the token count "(cached)" because the provider reported the provider's
  prompt-cache hits as `tokenUsage.cached`, which Promptfoo reads as its own response cache. Cached input tokens now
  go to the metadata (`cached_input_tokens`).
- [screenshots/promptfoo-view-fake-matrix.png](screenshots/promptfoo-view-fake-matrix.png): the fake matrix, three
  configurations side by side, after the fix.

## Notes
- Promptfoo is a viewer and orchestrator here; `python -m evals.bench` stays the source of the comparison matrix
  (Wilson intervals, tokens per successful task, latencies). Promptfoo's own cost column shows dollars only for the
  priced configuration; Bailian runs show tokens.
- Promptfoo order is test × provider (each case for every configuration in turn), so the runs are interleaved.
- `promptfoo view` binds localhost:15500; it was stopped after the screenshots. Nothing was shared or uploaded.

## Next
- Coordinator: README section below; prompt history export; close #47 with this PR.

## Draft: README section

## Promptfoo view (optional)

The same benchmark can be browsed in [Promptfoo](https://promptfoo.dev): one row per eval case, one column per
configuration, each cell the whole conversation (turns, tool calls, cards, clicks) with two deterministic
assertions, `safety` and `trajectory`. It is optional and isolated in `evals/promptfoo/` (its own `package.json`);
the app and the test suite do not depend on it. Telemetry, update checks and sharing are off; results stay in
`evals/promptfoo/.promptfoo/`.

```bash
cd evals/promptfoo && npm ci                     # once (pinned promptfoo)
cd backend && uv sync                            # once (the provider runs with the backend's Python)
bash evals/promptfoo/pf.sh eval -c promptfooconfig.fake.json      # scripted model, no network, no cost
OCEAN_ENV_FILE=/path/to/.env bash evals/promptfoo/pf.sh eval -c promptfooconfig.json \
    --filter-providers '^deepseek-official$'     # real model; per-configuration cost / token guards
bash evals/promptfoo/pf.sh view                  # opens the comparison at http://localhost:15500
```

Regenerate the configs after changing cases or the spec: `backend/.venv/bin/python evals/promptfoo/gen.py --fake
--out promptfooconfig.fake.json && backend/.venv/bin/python evals/promptfoo/gen.py`.
