# 03-slice end-to-end check (2026-11-23, coordinator)

Source: `main@bb4488e`, real DeepSeek flash (effort high).

1. **Local API through the Next dev proxy** (`curl`, SSE): quote Guangzhou → Lima (cheapest via Frankfurt, Singapore,
   Amsterdam; card $1,160.71 / 8,928.6 km) → "yes, book it" → booking-request card with nonce → click (POST book) →
   ticket; replaying the same nonce → `already_booked`; "which tickets do I have?" → ticket list (1); Abu Dhabi →
   no-service card with Tirana, Odesa, Whitehorse; "不，太贵了，别订" → no booking request, Chinese reply.
2. **Demo site** `https://ocean-above.potafree.net` (homelab compose behind Cloudflare Tunnel), ego-browser:
   sign in (dev account) → new chat → quote → "yes, book it" → Confirm booking → ticket card; My tickets on a
   390×844 viewport, no horizontal scroll. Screenshots in this folder.
3. SSE through Cloudflare + Caddy: first ping 1.34 s after the request (incl. TLS), then every 2 s.

Findings for 04: My tickets shows an empty page while loading (no loading state); the ticket id badge wraps on
mobile; the card's "Valid until" stays visible after the quote is booked.
