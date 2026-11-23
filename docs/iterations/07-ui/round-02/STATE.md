# 07-ui round 02 — STATE (stages 2–3)

The source tested is branch `it07-ui`, at the commit that adds this file (with `origin/main` merged; contract v1.5).

## Done
- **The design system is applied to every page and card:**
  - login, chat, My tickets, History and the history detail, Privacy and Terms;
  - quote, confirm, booked, ticket and ticket list;
  - no-service, ambiguous (candidates are now buttons that send "I mean X."), unknown city, notice;
  - expired and stale;
  - loading, empty and error.
- **Messages that were not sent keep the input and offer Retry:** `conversation_busy` (in this chat or another one),
  `server_busy`, and `internal_error` before `user_message`.
- **Scrolling:**
  - The chat follows new content only near the bottom, so the page is never forced to the bottom while you read.
  - A "Scroll to latest" button appears when you scroll away.
  - The composer is in the normal flow, so the last card always scrolls fully above it on phones (checked at 390 and
    360).
- **Accessibility:**
  - the status badge is `aria-live`;
  - the page behind the drawer is `inert`;
  - account-menu focus returns on Escape;
  - every app control is ≥ 44 × 44;
  - reduced motion is respected, including "Scroll to latest".
- **Reviews:**
  - `critique.md`: 29/40 (Good), no P0 or P1.
  - `audit.md`: 18/20 (Excellent), no P0 or P1.
  - Both ran single-context (no sub-agents allowed for implementers).
- **Detector:** `detect` on the sources returns `[]`. On the running production pages, the only remaining finding is
  `overused-font` (Geist, the user's choice D1), plus two straggler advisories on `/privacy`.
- **Screenshots:** 161 in `screens/`, named `<width>-<theme>-<state>.png`.
  - Widths: 1440, 768, 390, 360. Themes: light and dark.
  - States (21):

    | Area | States |
    |---|---|
    | Login and legal | login, privacy |
    | Chat | chat-empty, chat-quote, chat-confirm, chat-calc-open, chat-booked |
    | Cards | chat-no-service-ambiguous, chat-unknown-notice |
    | Errors | chat-stale, chat-expired, chat-server-busy, chat-error |
    | Navigation | account-menu (desktop and 768), drawer (390 and 360) |
    | My tickets | tickets, tickets-empty, tickets-loading |
    | History | history, history-error, history-detail |

  - One extra shot: `390-dark-chat-reading-up-while-streaming.png`.
  - Every shot has `scrollWidth == clientWidth`.
- **DESIGN.md:** the §2–6 rewrite draft is `../DESIGN-draft.md`, for the coordinator to merge.

## Open
- **P2: stopping a streaming reply needs a backend cancel.** Out of scope; carried over from D1.
- **P2: the Google button is 40 px tall.** GIS draws it at its maximum size.
- **The ticket card could look more like a document (P3).**

## How to verify

Run the checks from `frontend`:

```
npx tsc --noEmit && npx vitest run && npx next build
```

Then run `bash tool/test_diff.sh && python3 tool/review_lint.py` from the repository root.

To view the screens:

```
MOCK_PORT=8017 MOCK_OPEN=1 node mock/server.mjs &
API_ORIGIN=http://127.0.0.1:8017 npx next build && API_ORIGIN=http://127.0.0.1:8017 npx next start -p 3117
```

Mock scenarios: `POST /__mock` with `{"scene":"proposal"}`, `{"book":"stale"}`, `{"message":"server_busy"}`,
`{"slow":"/api/tickets"}` and the others listed at the top of `mock/server.mjs`.

## Results (this commit)
- tsc is clean and vitest passes 89 (new: `server_busy` / `internal_error` keep the input and offer Retry; Enter /
  Shift+Enter / IME; ambiguous-city buttons; drawer focus in the real `Shell`).
- `next build` is OK.
- `test_diff`: no new failures.
- `review_lint`: ok.
