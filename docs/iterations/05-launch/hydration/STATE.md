# Hydration #418 (issue #37) — STATE

## Done
- **Root cause**: not a React/theme bug. The deployed site sits behind Cloudflare, whose *Email Address Obfuscation*
  rewrites the literal address in `/privacy` and `/terms` HTML (`<a class="__cf_email__">[email protected]</a>` + a decode
  script). The rewritten DOM no longer matches the RSC payload → React error #418. Found by diffing local prod
  `curl /privacy` against the live response (only the two address spots + the injected script differ).
- **Fix** (`frontend/src/components/legal-page.tsx`): e-mail addresses are rendered as `user<span>@</span>domain`, so
  no literal address exists in the HTML and the proxy has nothing to rewrite. Text reads the same.
- **Test**: `tests/legal-page.test.tsx` — rendered HTML of both legal pages has no literal address.
- Live OS-scheme switch: with the page open (prod build, CDP `prefers-color-scheme` light→dark), computed
  colour/background/border of every element equals a fresh load in dark (login 81 elements, privacy 63: 0 diffs).
  Not reproducible → no change. Theme is CSS variables only; THEME_BOOT sets data-theme pre-paint.
- `text-wrap: pretty` on `p` already in globals.css.
- Scripted ego-browser pass over login, privacy, terms, chat, tickets, history on the prod build (port 3611, mock
  API): zero console errors apart from expected 401 on `/api/me` when logged out.

## Verify
`cd frontend && npm test && npx next build && PORT=3611 npx next start`, then `curl localhost:3611/privacy | grep -c 'chuxdesign@'` → 0.
After deploy: `npx impeccable detect https://ocean-above.potafree.net/privacy` should have no #418.
Optional belt-and-braces (coordinator): turn off Cloudflare Scrape Shield → Email Address Obfuscation.
