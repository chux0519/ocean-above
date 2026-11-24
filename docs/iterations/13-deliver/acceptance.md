# 13-deliver — acceptance (2026-11-24)

| Take-home deliverable | Where | Status |
|---|---|---|
| Hosted app | https://ocean-above.potafree.net. Invite codes go to reviewers privately; Google login also works. Released at `c689e0f`; later commits are docs only. Public e2e after the last release: quote, confirmation, click books, replayed nonce refused, ticket lookup, unserved city → nearest served | done |
| Code + how to run | private repo `chux0519/ocean-above`; README TL;DR, "Run locally", tests / evals / bench / Promptfoo / tracing | done |
| Design doc (5 required topics) | root [DESIGN.md](../../../DESIGN.md) §1–§5, TL;DR on one screen, "In short" per section; §5.5 context management (added after review) | done |
| Prompt history | `prompt history/`: 62 raw Claude Code sessions, secrets redacted ([audit](prompt-history-audit.md)). The homelab Codex deploy session is withheld by owner decision (mixes other private projects) and explained in `prompt history/README.md` | done |
| Optional: the real bot | DESIGN.md §7: runtime choice, Phoenix / OTel, the parts of `core/` a real airline would replace, architecture changes, the model benchmark | done |
| Infographic | [docs/infographic/poster.png](../../infographic/poster.png) (+ HTML, PDF); every number has a source (D3/STATE.md) | done |

Checks at `c982966`: `pfstack check` all set; lint clean; backend 1,014 passed at the #59 merge; vitest 114.

Also delivered in this round:
- #47 benchmark (PR #58) and Promptfoo view (PR #62);
- #59 fixes (PR #61): retry a failed tool once; resolved endpoint on unknown / ambiguous quotes;
- the UI design system moved to `frontend/DESIGN.md`.
