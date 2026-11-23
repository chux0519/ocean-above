# 07-ui round 01 — STATE

The source tested is branch `it07-ui`, at the commit that adds this file.

## Done (stage 1: proposal)

The full proposal is `docs/iterations/07-ui/proposal/proposal.md`; the screenshots are in
`docs/iterations/07-ui/proposal/screens/` (14 files, desktop and mobile, light and dark).

- New tokens for the light theme (warm neutral) and the dark theme (charcoal/zinc).
- The Geist font, loaded locally.
- Rules for radius and depth.
- A redesigned shell (sidebar and drawer).
- New message styling.
- A redesigned itinerary card.
- A floating composer.

## In progress
Nothing. Waiting for the user to confirm decisions D1–D7 in the proposal, §5.

## Next (stage 2, after confirmation)
- Apply the system to the remaining cards and pages.
- Handle `server_busy` (contract v1.4).
- Interaction polish (issue #39, P2).

## How to verify

Start the mock API, the dev server and the proposal scenario:

```
cd frontend
MOCK_PORT=8017 MOCK_OPEN=1 node mock/server.mjs &
API_ORIGIN=http://127.0.0.1:8017 PORT=3117 npm run dev &
curl -XPOST localhost:8017/__mock -d '{"scene":"proposal"}'
```

Then open `http://localhost:3117/chat`.

Run the checks with `npx tsc --noEmit && npx vitest run && npx next build`, and `python3 tool/review_lint.py` from the
repository root.
