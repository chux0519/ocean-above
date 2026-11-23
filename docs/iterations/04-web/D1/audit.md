# /impeccable audit: frontend (chat, cards, My tickets, history, login; light and dark)

Single-context run (no sub-agents, per the coordinator). Source state: branch `it04-design`, the commit that adds
this file.

## Audit Health Score
| # | Dimension | Score | Key finding |
|---|---|---|---|
| 1 | Accessibility | 3 | Drawer focus and heading levels were fixed in this round. The theme buttons in the mobile menu are icon-only, though each has an accessible name. |
| 2 | Performance | 3 | Production JS is 214,992 B gzip in total; lucide adds about 2.0 KB over the hand-written SVGs. Self-hosting the font through next/font means no runtime request to Google. |
| 3 | Theming | 3 | Every colour comes from a CSS variable and there are two complete palettes. A few translucent white or black values remain on the navy shell and the popover shadow. |
| 4 | Responsive design | 4 | No horizontal scroll at 1440, 390 or 360. All controls are at least 44×44 (measured at 360). The drawer and settings menu are used below `md` and `sm`. |
| 5 | Implementation integrity | 3 | One coherent token and component system; the detector is clean on source and on the running pages. |
| **Total** | | **16/20** | **Good** |

## Implementation integrity verdict
**Pass.** The UI is built from one set of primitives:
- `ui/button`, `ui/card` (Card and Badge), `ui/input`, `ui/alert` and `ui/skeleton`;
- one icon module (`components/icons.tsx`, lucide named imports);
- tokens in `globals.css`.

Results of `impeccable detect`:

| Scan | Result |
|---|---|
| `frontend/src` | `[]` |
| Running pages `/login`, `/chat`, `/tickets`, `/history/c_01` at 1440×900 and 390×844, after the fixes | `[]` |
| Round 1 (before the fixes) | 1 warning (skipped heading on `/tickets`) and 1 advisory (straggler on `/login`); both fixed |

## Executive summary
- Score 16/20 (Good).
- Issues: P0 0, P1 2 (both fixed), P2 3 (2 fixed, 1 open), P3 3.
- Next steps: the P3s, and a stop control for streaming replies (a behaviour change, not D1).

## Findings
| Pri | Issue | Location | Category | Standard | Status |
|---|---|---|---|---|---|
| P1 | Drawer `role=dialog aria-modal`: focus not moved inside, no Escape, focus not returned | `app/chat/page.tsx` | A11y | WCAG 2.4.3 focus order, 2.1.1 keyboard | **Fixed** and tested |
| P1 | Theme switch used `role=radiogroup` / `role=radio` without arrow-key handling or a roving tabindex | `components/theme-toggle.tsx` | A11y | WAI-ARIA radio group pattern | **Fixed**: now `role=group` with `aria-pressed` buttons; tests updated |
| P2 | Skipped heading level (h1 → h3) on My tickets; no h1 on chat; no `<main>` landmark | cards, `shell.tsx`, chat | A11y | WCAG 1.3.1 | **Fixed** |
| P2 | Mixed locales: history dates followed the browser locale | `app/history/page.tsx` | Implementation | — | **Fixed** (English) |
| P2 | Focus ring on the navy header used the accent colour (low contrast against navy) | `globals.css` | A11y | WCAG 1.4.11 non-text contrast | **Fixed**: `[data-shell] :focus-visible` uses white (13.8:1 light, 16.2:1 dark) |
| P3 | Theme buttons in the mobile settings menu are icon-only | `shell.tsx` | A11y | — | Open (each has `aria-label` and `title`) |
| P3 | Translucent white or black values not tokenised: `bg-white/10–15` on the shell, the popover shadow | `shell.tsx`, `theme-toggle.tsx` | Theming | — | Open (correct in both themes, because the shell is navy in both) |
| P3 | Reduced motion is a global 0 ms rule | `globals.css` | A11y | — | Accepted: no state is carried by motion alone |

## Contrast (computed, WCAG 2.x)
- **Light theme:**

  | Pair | Ratio |
  |---|---|
  | ink / surface | 16.5:1 |
  | ink-2 / surface | 7.3:1 |
  | placeholder | 5.5:1 |
  | white on accent | 7.1:1 |
  | ok, warn, danger on their tints | 5.8, 6.7, 5.8:1 |
  | field border (non-text) | 3.1:1 |

- **Dark theme:**

  | Pair | Ratio |
  |---|---|
  | ink / surface | 13.5:1 |
  | ink-2 / surface | 7.5:1 |
  | placeholder | 5.6:1 |
  | text on accent | 6.8:1 |
  | ok, warn, danger on their tints | 7.6, 7.7, 6.9:1 |
  | field border (non-text) | 3.3:1 |

- All body-text pairs are ≥ 4.5:1 and all non-text pairs ≥ 3:1. The full table is in `design-proposal.md` §2 and §2b.

## Positive findings
- **No flash on first paint:**
  - the stored theme is applied by an inline script in `<head>` before the body is parsed;
  - when nothing is stored, the "system" setting is pure CSS (`prefers-color-scheme`);
  - `color-scheme` is set for native controls;
  - `theme-color` follows the system theme.
- **Tabular figures everywhere numbers line up** (`.figures`), so the km column and the totals align.
- **The no-service and similar-names cards** use text plus an icon, never colour alone.

## Recommended actions
1. [P3] `/impeccable polish`: add text labels to the theme buttons in the mobile menu, and tokenise the shell overlay
   tints.
2. [P2, outside D1] `/impeccable harden`: stop control for streaming replies (needs an API abort path).
