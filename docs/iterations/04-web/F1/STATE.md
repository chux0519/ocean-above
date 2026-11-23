# F1 functional UI states and fixes — STATE

Source tested: branch `it04-ui-states` (see PR, Closes #19). No visual redesign.

## Done
- Loading / empty / error (with Retry) on chat (list + conversation), My tickets, History, history detail (404 message).
- Booking errors per contract: `stale`/`expired`/`invalid_nonce` → explanation, reload, **Get a new quote** (sends a chat
  message); `already_booked` → points to My tickets; `conversation_busy`/`data_unavailable`/network → message, Confirm stays
  available for retry, no reload.
- SSE: `conversation_busy` (HTTP 409 or SSE error before `user_message`) keeps the input text, no reload. Network drop →
  conversation reloaded from the server; if the server never stored the message the input text is kept. Other SSE `error`
  messages now survive the post-stream reload (they were wiped before).
- Cards: `Valid until` only on `quoted`; `presented` shows a live countdown and, at zero, locks Confirm and offers a new
  quote; ticket id badge `whitespace-nowrap`; `similar_names` note (“Also: San José — flies to Sydney, Tokyo”).
- Markdown: streamdown removed; own renderer (`src/lib/markdown.ts`, `components/markdown.tsx`): paragraphs, line breaks,
  bold, italic, inline code, http(s) links (`rel=noopener noreferrer`, `target=_blank`), one-level lists; no raw HTML ever;
  unclosed markers stay plain text; memo per block.
- Mock (`frontend/mock/server.mjs`): `POST /__mock {book, message, tickets, history, conversation, delay}` scenario
  switches; presented quotes get a live expiry.

## Size (prod build, all `.next/static/chunks/*.js`, gzip)
- Before (streamdown): 352,739 B; largest chunk 149,460 B.
- Streamdown with plugins off / controls off / skipHtml: 357,063 B (no gain: cost is unified/remark/rehype/parse5).
- After (own renderer): 206,418 B; largest chunk 71,779 B.

## Verify
- `cd frontend && npm run lint && npm test && npm run build` → tsc clean, 57 vitest tests pass (new: `states.test.tsx`,
  `markdown.test.tsx`), build OK.
- Screenshots (mock API, 1440×900 and 390×844): `docs/iterations/04-web/F1/screenshots/` — tickets loading/empty/error,
  history empty/error, chat similar-names / stale booking / busy. `scrollWidth == clientWidth` in all 16.
  The round dark circle bottom-left is the Next dev indicator; the purple widget at the right edge is a browser extension.

## Round 02
- Merged `origin/main` (B1 merged): `tool/test_diff.sh` → no new failures (the earlier backend failures are gone).
- After `stale`/`expired`/`invalid_nonce`/`already_booked` the card reloads from the server and also locally drops Confirm and
  the countdown; server `stale`/`expired` cards show the badge + "Get a new quote". Mock now flips a refused quote to
  stale/expired/booked. Stale screenshots retaken. 3 new tests (57 total).

## Notes / open
- Impeccable critique/audit not run here (D1 scope).
