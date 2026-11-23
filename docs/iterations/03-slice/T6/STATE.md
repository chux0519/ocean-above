# T6 minimal frontend — STATE (round 01)

Source tested: branch `it03-web`, see PR (Closes #5).

## Done
- `frontend/`: Next 16 (App Router) + TS + Tailwind 4 + shadcn-style Button/Card/Badge (cva, clsx, tailwind-merge) + streamdown.
- Pages: `/login` (dev account), `/chat` (conversation list, desktop sidebar / mobile drawer, SSE reader, streamdown markdown,
  all 8 card types, Confirm booking, trace expand), `/tickets`, `/history`, `/history/[id]` (read-only).
- `/api` rewrite to `API_ORIGIN` (default `http://127.0.0.1:8000`); servers bind 0.0.0.0 (dev port 3100).
- Mock API: `node mock/server.mjs` (`MOCK_PORT`, `MOCK_OPEN=1` skips login) serves `contract/fixtures`; a message turn replays
  `sse_quote_turn.txt`; booking flips the card to booked.
- No number formatting: only `*_display` shown; high-precision values only in the trace view.
- Confirm booking: shown only when `state.bookable && nonce`, disabled while a stream runs, hidden in read-only history;
  after done / booking / any stream end the conversation is reloaded (server state wins).

## Verify
- `cd frontend && npm run lint && npm test && npm run build` → tsc clean, 19 vitest tests pass (every card type, SSE parser
  incl. chunk boundaries and fixture `sse_quote_turn.txt`, reducer, Confirm gating, trace), build OK.
- Screenshots (mock API, 1440×900 and 390×844): `docs/iterations/03-slice/T6/screenshots/`. No horizontal scroll
  (scrollWidth == clientWidth on chat and tickets, both viewports).
- Streamdown size (prod build): the chunk containing streamdown is 502 KB raw / 149 KB gzip (includes remark pipeline; mermaid
  is referenced by it); all JS 365 KB gzip. Heavy for a chat view — candidate for trimming in 04 (or react-markdown fallback).

## Notes / open
- `npm install` needs `--proxy/--https-proxy http://192.168.1.13:7890`: the machine's npm config proxy is an unreachable IPv6 address.
- Next 16 has no `next lint`; lint = `tsc --noEmit` (no eslint dependency added).
- Dev overlay "Issues" in screenshots came from a browser extension altering `<html class>` (hydration warning, now suppressed)
  and duplicate ids in the mock (fixed).
- Not done (iteration 04): Impeccable critique/audit, live countdown for quote expiry (only the expiry time is shown).
- Screenshots of dev run may show the Next dev indicator.
