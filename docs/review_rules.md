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
| M04 | No booking path that bypasses explicit confirmation of the exact quoted route, distance and price. | Requirement 7. |
| M05 | UI changes ship with an Impeccable `critique`/`audit` result and a `detect` run; P0–P1 fixed. | User decision 2026-11-23. |
| M06 | New dependency named in the plan with a reason; no agent/LLM frameworks. | Minimal-dependency rule. |
