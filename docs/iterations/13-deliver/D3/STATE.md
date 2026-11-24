# D3 — one-page poster (infographic) · STATE

Branch `it13-poster`, implementer session. Date 2026-11-24.

## Done
- `docs/infographic/poster.html` — self-contained (no network): Geist from `../../frontend/src/fonts/Geist-Variable.woff2`
  (relative path), `<meta charset="utf-8">`, inline SVG diagrams, benchmark chart drawn by a small inline script
  from a data array copied from `bench.md`. Layout 1600×940 CSS px.
- `docs/infographic/poster.png` — 2400×1410 (1.5× device scale). `docs/infographic/poster.pdf` — one vector page.
- `docs/infographic/render.sh` — re-renders both with local headless Chrome (`CHROME=` overrides the path).
- README TL;DR: one added line linking the poster (PNG, HTML, PDF). No other README change.
- Colours and type from `frontend/DESIGN.md`: ink `#1C1A17`, ink-2/ink-3, accent `#1F5F99` (section bars, KPI
  numbers, production series), sunken `#F0EEEA`, ok green only on the "Ticket" step, danger red only on the
  "never books" lane. Geist, weights 400/500/600 (no 700). Smallest text 11 CSS px (= 16.5 px in the PNG).
- Panels: title · 3 KPI cards · 1 Problem and goal · 2 Architecture (redrawn §1.1) · 3 Booking gate · 4 Results
  (benchmark small multiples + findings + test layers) · 5 Choices · footer (demo, docs to read, prompt history).
- dataviz skill: highlight encoding (production = accent, other configs = `#8F8A82`, 3.4:1 on white); validator
  run: grey fails the categorical chroma floor by design (it is a de-emphasis, not a category) and its 2.6–3.4
  contrast is relieved by direct value labels on every mark. Pass rate is a dot + Wilson interval on an 80–100 %
  axis (dots, not bars, so a non-zero axis is honest); tokens and latency are bars from 0. Every mark has an SVG
  `<title>` tooltip in the HTML.
- Impeccable critique: see `critique.md` (single-context, because implementers must not start other agents).

## Number → source

| Number on the poster | Source |
|---|---|
| 0 safety violations; 405 benchmark runs | DESIGN.md "Key numbers" table (row "Safety violations"); 405 = 3 configs × 135 runs in bench.md |
| 96.3% (130/135), 98.5% (133/135), 91.1% (123/135) | docs/iterations/12-bench/results/n3/bench.md, comparison matrix; DESIGN.md §7.3 |
| N = 3 runs × 45 cases, reasoning effort high | bench.md header + configurations table |
| 95% intervals 92–98 / 95–100 / 85–95 % | bench.md "95% interval of the pass rate (Wilson)" |
| Tokens per successful task 13,788 / 13,475 / 15,395 | bench.md |
| Chat turn p50 / p95: 2.19/3.40, 2.17/4.23, 3.08/7.85 s | bench.md (3.4 printed as 3.40) |
| $0.0008 per successful task (official API) | bench.md "Cost per successful task" |
| "about 2× the reasoning tokens" (Qwen) | DESIGN.md §7.3 reading (bench.md: 22,853 vs 11,790 reasoning tokens) |
| 490,700 served-city pairs identical, Bellman-Ford reference | docs/iterations/02-core/acceptance.md line 6; DESIGN.md Key numbers |
| 4 tools, no `book` tool | DESIGN.md TL;DR, §2.1 |
| B1–B7, 15 min validity, one ticket per quote, one transaction | DESIGN.md §4.2 |
| B1–B7 model-tested (12 mutants) | DESIGN.md §4.3; docs/iterations/03-slice/acceptance.md line 8 |
| 300 simultaneous clicks → 0 double bookings | DESIGN.md §4.3; docs/design/sqlite-concurrency.md §X.5 |
| Stop: card never presented, no nonce | DESIGN.md §4.4 (R6) |
| Cancel closes upstream stream in 0.6 ms on DeepSeek | docs/design/adr-001-agent-runtime.md table; DESIGN.md §7.1 |
| OTel → Phoenix, no outbound export by default | ADR-001 table and text; DESIGN.md §7.1 |
| PostgreSQL conditions | DESIGN.md §7.4 |
| Real-airline replacements | DESIGN.md §7.2 |
| 1,005 backend tests · 114 frontend tests · 45 eval cases | DESIGN.md "Key numbers" table |
| one uvicorn worker | DESIGN.md §1.1 |

No number was computed for the poster except 405 = 3 × 135 (also stated in DESIGN.md).

## Not done / open
- No ego-browser session was used; rendering is local headless Chrome (allowed by the brief). Only one viewport
  matters (fixed-size poster), so no mobile screenshot.
- Impeccable `detect` warnings left standing (see critique.md): tiny-text 11–11.5 px (poster minimum set by the
  brief; the PNG is 1.5×), overused-font Geist (pinned by frontend/DESIGN.md), tight-leading 1.19 (single-line
  title / KPI numbers). No ignore entries written (files outside this task's scope).
- Decision for the coordinator: the poster has no logo (the product has none; the first draft's invented mark was
  removed).

## How to verify
```sh
docs/infographic/render.sh        # rewrites poster.png (2400×1410) and poster.pdf (1 page)
open docs/infographic/poster.png
.claude/skills/impeccable/scripts/impeccable detect --json docs/infographic/poster.html
```
Compare every number with the table above.
