# 13-deliver D1 — STATE (2026-11-24)

Task: plan.md D1 (delivery design doc + README), plus the coordinator's two follow-ups (TL;DR / "In short" for human
readers; #47 benchmark merge). Branch `it13-docs`, merged with `origin/main@71a54b9`.

## Done
| Item | Where |
|---|---|
| UI design system moved, content unchanged (`git mv`; it has no relative markdown links, only backticked repo paths) | `frontend/DESIGN.md` |
| Links to it fixed: AGENTS.md (Design rules, 2 places), PRODUCT.md (§1 reference). `tool/`, `.claude/settings.json`: no references. `prompt history/` and old iteration notes untouched (historical). | — |
| New root design doc (English, 642 lines): TL;DR (≈20 lines, the five questions + demo + key numbers incl. benchmark), key-numbers table with sources, doc index, §1–§7 each opening with an *In short* line | `DESIGN.md` |
| §7.3 benchmark table (3 configurations: safety, pass + Wilson 95%, tokens / cost per successful task, p50/p95; N=3, small-sample note), numbers only from 12-bench STATE and `results/n3/bench.md`; benchmark findings added to §5.4 | `DESIGN.md` |
| README for reviewers: 4-line TL;DR, demo + login (invite code given privately, no code written), run locally, tests / evals / app.db bench / multi-model benchmark (12-bench draft merged), repo map (incl. `spikes/` = POC evidence, `prompt history/` = raw sessions), doc index | `README.md` |
| design.md: top line pointing at the English root DESIGN.md; 12-bench draft merged as §10.1 | `docs/design/design.md` |

## Please confirm (coordinator)
1. **AGENTS.md Design rule** now reads: "`frontend/DESIGN.md` (the UI design system) is the single source of truth for
   visual and interaction rules; only the coordinator edits it. The root `DESIGN.md` is the take-home design doc."
   and "advice that conflicts with frontend/DESIGN.md goes to the user…".
2. **Impeccable / pfstack and the root name.** Impeccable's context loader and the pfstack template both look for a
   root `DESIGN.md`. pfstack `check` passes (the new root file exists, no placeholders — mermaid uses `<br>`, not
   `<br/>`, which the placeholder regex would flag). Impeccable would now load the take-home doc as "design system";
   pass `frontend/` as the boundary / `--target` in future UI reviews, or decide otherwise.
3. Number sources I chose: backend test count 1,005 = `pytest --collect-only` on the merged branch (`71a54b9`), not an
   acceptance record; frontend 114 from 11-followup acceptance. Replace if you prefer an acceptance-recorded count.
4. README "Try the demo" lists example prompts (Guangzhou → Lima, Abu Dhabi → Paris, San Jose); checked against
   `locations.db` (Abu Dhabi unserved, Paris a hub, two San Jose entries), not run on production.

## Checks
- `python3 tool/review_lint.py`: all ok.
- `python3 ~/.claude/skills/pfstack/scripts/pfstack.py check .`: `pfstack: all set`.
- Mermaid: all 3 diagrams of DESIGN.md rendered with `@mermaid-js/mermaid-cli` 12.2.0 (`mmdc`, via the LAN proxy)
  with exit code 0 and no "Syntax error" in the SVGs (component diagram, chat-turn sequence, booking-click sequence).
- No code changed (docs and links only). No tests run beyond collection (nothing to test).

## How to verify
`git diff origin/main -- DESIGN.md README.md AGENTS.md PRODUCT.md docs/design/design.md`; `git show --stat -M fdf4651`
shows `DESIGN.md → frontend/DESIGN.md` as a rename. Render check: extract the ```mermaid blocks and run `mmdc -i`.

## Not done / next
- prompt history export for this session (coordinator, D2/D4).
- §7 text review by the coordinator (owner of the final text); infographic (D3).
