# T6 round 02 — Next dev indicator issues

Source: dev server log (`[browser]` lines forwarded by Next) + overlay counts seen in round-01 screenshots
(desktop 9, mobile 1). Round-01 flow: login → chat → send → confirm → trace → tickets → history.

## Issues found (all of them)
| # | Text | Cause | Ours? | Action |
|---|---|---|---|---|
| 1 | `A tree hydrated but some attributes of the server rendered HTML didn't match the client properties` — diff: `<html lang="en">` `- className="trancy-zh-CN"` (logged 12×, once per page load/navigation; this is the only issue on mobile's first load) | A browser extension (Trancy, translation) adds `class="trancy-zh-CN"` to `<html>` before hydration. Evidence: the only diff line is that class, which appears nowhere in our code (`grep -r trancy frontend/src` empty); the translate/Trancy floating icons are visible on the right edge of every screenshot. | Extension | `suppressHydrationWarning` on `<html>` (round 01, standard for extension-modified root). |
| 2 | `Encountered two children with the same key, m_1` | `MessageList` keyed by `message.id`; the mock replayed `sse_quote_turn.txt` whose `m_1`/`m_2` already exist in the stored fixture conversation, so the list had duplicate ids. Real API ids are unique. | Mock only (not app code) | Mock now gives every replayed message a unique id, in both the SSE stream and the stored conversation. |
| 3 | `Encountered two children with the same key, m_2` | same | Mock only | same |

Counts: the overlay counted repeats of 1–3 (9 on desktop after several navigations, 1 on mobile). No accessibility
or other warnings were reported in the log.

## Result
Same flow re-run against the fixed mock: no `[browser]` lines in the dev log; the Next indicator shows no issue count
(screenshots `desktop-chat-round02.png`, `mobile-chat-round02.png`). The extension's own icons remain on the right edge.
Note: the Next "N" dev indicator overlaps the input row on mobile; it exists only in `next dev`, not in production.
