# Review rules

Pitfalls we have hit. `R*` rules are checked by `tool/review_lint.py` (run one with
`python3 tool/review_lint.py R01`); `M*` rules are checked by reading.

| Id | Rule | Why (the incident) |
|---|---|---|
| R01 | No real host or user names in tracked files (except raw `prompt history/`); use `example-*`. | A public export leaked a machine name. |
| R02 | `rm -rf` only as `rm -rf "${VAR:?}"`. | An empty variable must never delete `/`. |
| M01 | Timing claims need a recording cut at stable frames, not one screenshot after the action. | A transient state was reported as a persistent failure. |
| M02 | Build-number bumps go through `tool/next_build.py`, never a blind `sed`. | A sed rewrote a hex id that contained the number. |
| M03 | Every number shown to the user (distance, price, leg list) comes from a tool result, never from LLM text. | Project rule: the LLM never computes. |
| M04 | Booking only via the user's click with the card's one-time nonce; the LLM has no booking path. | Requirement 7; design review P0-2. |
| M05 | UI changes ship with an Impeccable `critique`/`audit` result and a `detect` run; P0–P1 fixed. | User decision 2026-11-23. |
| M06 | New dependency named in the plan with a reason; no agent/LLM frameworks. | Minimal-dependency rule. |
| M07 | Each iteration plan has a "Take-home check" against `Quotr Agent Dev Engineer take-home.md`; the take-home wins on conflict. | User decision 2026-11-23. |
| M08 | One canonical pricing function (explicit Decimal context); rounding only at display. | User decision 2026-11-23; design review P1-4 (thread default context is 28 digits). |
| M09 | Booking re-validates against current `locations.db` and the business DB; mismatch → re-quote + re-confirm. | User decision 2026-11-23. |
| M10 | UI acceptance includes ego-browser screenshots at desktop and mobile widths. | User decision 2026-11-23. |
| M11 | City lookup never auto-picks among names equal after accent folding (`San Jose` / `San José`). | Design review P0-1. |
| M12 | Every id-bearing endpoint checks ownership, not only booking. | Design review P2-5. |
| M13 | Sessions exported with `tool/export_sessions.py` and secret-scanned at the end of every iteration. | Design review P1-6. |
