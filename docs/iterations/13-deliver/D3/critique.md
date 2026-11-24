# D3 poster — Impeccable critique

⚠️ DEGRADED: single-context (the implementer brief forbids starting other agents, so Assessment A and B ran in one
context; A was written before reading detector output). Snapshot not persisted to `.impeccable/` (outside the task's
files). Questions skipped: implementer run; open points go to the coordinator in STATE.md.

Target: `docs/infographic/poster.html`, rendered `poster.png` (2400×1410). Design context: `frontend/DESIGN.md`.
Mode: Read (a reviewer should understand the system in about a minute).

## Assessment A — design review (draft 2, before fixes)

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | n/a | static poster |
| 2 | Match real world | 4 | uses the product's words (quote card, Confirm booking, Stop) |
| 3 | User control | n/a | static poster |
| 4 | Consistency | 3 | one accent, one meaning per colour (blue = structure / production, red = never books, green = ticket); header logo was an invented mark |
| 5 | Error prevention | 3 | the "never books" lane shows the failure paths explicitly |
| 6 | Recognition over recall | 3 | numbered sections, figure captions, sources next to each claim |
| 7 | Flexibility | n/a | static poster |
| 8 | Aesthetic and minimalist | 3 | Results column ended ~100 px early (unbalanced); one KPI tile repeated a caption |
| 9 | Error recovery | n/a | static poster |
| 10 | Help and documentation | 4 | footer reading order, source under every block |
| **Total** | | **20/24** | good |

Design specificity: grounded in the product — the booking-gate diagram with a "who acts" lane and a "never books"
lane is this system's argument and could not be reused unchanged by another product. Palette and type are the
product's own tokens.

Priority issues found and fixed in the same round:
1. Invented logo mark in the header (the product has no logo) → removed; title stands alone.
2. Results column ended early → benchmark rows taller, three larger test tiles.
3. "135 real-model runs" tile duplicated the Fig. 3 caption → removed.
4. Tile padding applied to the tile's inner text blocks (selector bug) → `.tests > div`.
Earlier render fixes (draft 1): chart script failed (`const top` collides with `window.top`; wrapped in an IIFE);
columns 1–2 and the footer overflowed; four SVG labels spilled out of their boxes or cylinders. All fixed.

Persona walk (take-home reviewer, from PRODUCT.md): reads title → three KPIs → booking gate in under a minute; every
number has a source to check. Red flag none remaining; the architecture diagram is the densest panel but follows
the order of the design doc's §1.1.

## Assessment B — detector

`impeccable detect --json docs/infographic/poster.html` (source scan, 1 file; no live-server overlay because the poster
is a fixed-size file rendered by headless Chrome):

| Rule | Count | Verdict |
|---|---|---|
| tiny-text (11 / 11.5 px) | 31 | left standing: the brief sets 11 px as the poster minimum; source notes and SVG labels; the PNG is 1.5× (16.5 px) |
| overused-font (Geist) | 1 | false positive for this project: Geist is pinned by frontend/DESIGN.md |
| tight-leading (1.19×) | 1 | single-line title and KPI numbers; no multi-line text at that leading |

No P0–P1 findings remain.
