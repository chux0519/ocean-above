# 07-ui — soft minimalist redesign (issue #39)

User request (2026-11-23): issue #39 (written by the user after choosing the style); 「我们是要替换 DESIGN.md 的描述，以新的为准」;
schedule after runtime fixes, before OTel #35. With 4 workers allowed, stage 1 (proposal) starts while #33/#34 run.

## Take-home check
Req. 7: quote and confirmation cards must still show every leg, every transfer city, total distance and price, and the
explicit Confirm action; never look booked when only quoted. Req. 8–10: no-service, ambiguous, tickets, history keep
their content. Numbers only from `*_display`. Frontend only. No conflict.

## Stages
1. **Proposal (now):** tokens for light (warm neutral) and dark (charcoal/zinc) themes, type (Geist or Inter as local
   files — offline build), radii/elevation rules, then one representative screen: chat with a multi-leg quote card,
   confirmation card and floating composer, desktop 1440 + mobile 390, light + dark, real browser screenshots →
   `docs/iterations/07-ui/proposal/` → **user confirms**.
2. **Apply** to all pages and cards (#39 P1 list), `server_busy` handling (contract v1.4), interaction polish (#39 P2).
3. **Review:** Impeccable critique/audit/detect (source + running page), screenshots at 1440 / 768 / 390 / 360 in both
   themes, e2e booking flow on the demo; coordinator rewrites DESIGN.md §2–6 from the accepted proposal.

Model: Opus 5.5 (design quality). Branch `it07-ui`. Owns `frontend/**`, `docs/iterations/07-ui/**`.
