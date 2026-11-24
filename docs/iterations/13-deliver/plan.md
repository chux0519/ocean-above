# 13-deliver — package the take-home

User request (2026-11-24, summary): the remaining work is optimisation; tidy code and docs and prepare the take-home
deliverables.
1. Deliver the online demo.
2. The design doc goes to the repo root. Today's `DESIGN.md` is the UI design system and moves under `frontend/`.
3. Audit `prompt history/` for leaked tokens.
4. Answer "what would a real booking bot need, and what did we choose". This covers:
   - why we moved from our own agent loop to Pydantic AI Slim;
   - Phoenix for evaluating and observing the LLM;
   - the benchmark results, once #47 finishes;
   - which parts of `core/` a real bot would replace;
   - which architecture parts would change, e.g. the database.
5. After the benchmark, a short infographic in the style of the user's example poster, with our numbers and the
   architecture diagram.

## Take-home check (2026-11-24) — deliverables, item by item
| Take-home | Where | Status |
|---|---|---|
| Hosted app + link + code | https://ocean-above.potafree.net (invite login for reviewers); private GitHub repo → shared as an archive or by invite (ask user) | live |
| README: how to run locally | `README.md` (`docker compose up`, dev login, env) | update |
| Design doc: architecture and data flow | root `DESIGN.md` §1–2 | to write (English) |
| Design doc: the agent's tools and why | root `DESIGN.md` §3 | to write |
| Design doc: routes, distances, prices; correct numbers | root `DESIGN.md` §4 | to write |
| Design doc: nothing booked without confirmation | root `DESIGN.md` §5 | to write |
| Design doc: limitations, failure modes, next steps | root `DESIGN.md` §6 | to write |
| Prompt history: full raw sessions of every coding agent, secrets removed | `prompt history/` (Claude Code sessions; Codex sessions from the homelab deploy and VPS migration agents) | audit + add Codex |
| Optional: what a real bot needs | root `DESIGN.md` §7 | to write after #47 |

## Decisions
- **Names:** root `DESIGN.md` becomes the take-home design doc (English). The UI design system moves to
  `frontend/DESIGN.md`. AGENTS.md and the pfstack rules point at the new path. `docs/design/design.md` (Chinese
  working notes) stays as the detailed record and is linked from the root doc. ADR-001, the SQLite note and the
  state invariants stay in `docs/design/`.
- **Language:** the delivered docs (DESIGN.md, README) are in English. Iteration notes stay as they are.
- **Code tidy:** remove dead code and stale comments only where a reviewer would trip over them; no refactors
  (the system is released and reviewed). `spikes/` stays as POC evidence and is labelled in the README.

## Tasks
| # | Task | Owner |
|---|---|---|
| D1 | Root `DESIGN.md` (English) from `docs/design/design.md`, ADR-001, `sqlite-concurrency.md`, `state_invariants.md`, the acceptance notes. Sections follow the take-home headings. It includes:<br>- mermaid architecture and data-flow diagrams;<br>- a tool table with the reasons;<br>- the number pipeline;<br>- the booking gate with B1–B7 and Stop;<br>- limitations and next steps;<br>- §7 "a real Ocean Above bot" as a skeleton, filled after #47.<br>Move the UI design system to `frontend/DESIGN.md` and fix every link. README restructured for a reviewer: demo link, login, run locally, tests / evals / bench, repo map. | worker (Opus 5.5), coordinator reviews and owns the final text |
| D2 | Prompt-history audit: literal scan against every secret we hold (local `.env`, the VPS `.env` read on the build host, reviewer and test invite codes, the old dev password, `.redact.local`), the token-shape scan, and a manual look at hits in context. Add the Codex sessions of the homelab deploy agent and the VPS-migration agent through `--extra`, redacted the same way. Report counts only. | coordinator |
| D3 | §7 + infographic after #47: one landscape poster (HTML → PNG). Panels:<br>- the problem;<br>- architecture;<br>- the booking gate;<br>- tests and evals numbers;<br>- benchmark chart;<br>- runtime / observability choices.<br>Stored in `docs/infographic/` and linked from the README. | coordinator (+ worker for the HTML if needed) |
| D4 | Final release, final export, `pfstack check`, tag `v1.0` (ask before pushing a tag? — a tag on the private repo is covered by the push authorization) | coordinator |

## Acceptance
- Every take-home deliverable row above is "done", with a link.
- Audit report `docs/iterations/13-deliver/prompt-history-audit.md`: sources, scans, hit counts per kind (0 unredacted
  secrets).
- `pfstack check` all set; lint; tests; the demo answers a quote, a confirmed booking, ticket lookup, history and an
  unserved city.
