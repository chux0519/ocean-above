# Acceptance — 02-core (2026-11-23)

- **Tested source:** PR #7 head `52894ba` (squash-merged to `main` as `509c415`).
- **Tier A:** coordinator reran `uv run pytest -q` (105 passed at round 01; 117 at round 02 per worker, `tool/test_diff.sh`
  no new failures, `tool/review_lint.py` all ok) on the worker branch.
- **Cross-check:** all 490,700 ordered served-city pairs, production vs independent Bellman-Ford reference: identical
  price strings, distance strings and city sequences; 27.6 s. Hypothesis random graphs (ties, collinear, disconnected)
  vs brute force; deterministic under `HYPOTHESIS_PROFILE=ci`.
- **Reviewed:** canonical pricing uses an explicit per-call context everywhere (no context-less Decimal arithmetic in
  `app/`); display formatting only in `fmt_km` / `fmt_usd` with thousands separators matching the contract; San Jose /
  San José ambiguity; nearest served (Abu Dhabi → Tirana etc.).
- **Decisions:** Yen + tolerance for hub candidates (design §5 updated); leg-sum vs total ≤ legs × 1e-30; decimal
  coordinates accepted; served = at least one direct flight; `current(strict=True)` for booking re-validation;
  `RouteError.issues`; keep httpx.
- **Bug caught by the cross-check:** context-less `scaleb` used the 28-digit thread context for edge weights (fixed,
  regression test added). Review checkpoint: no Decimal method without an explicit context.
- **Pending C:** none (backend only).
