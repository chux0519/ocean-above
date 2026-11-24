# Product

<!-- impeccable:product-schema 1 -->

<!-- Source of the answers: the coordinator's brief for issue #20 (2026-11-23), plus AGENTS.md, docs/design/design.md
and the take-home brief. No interactive interview took place; nothing below is invented beyond those sources. -->

## Platform

web

## Users
- **Travellers** (primary): ordinary passengers of the airline Ocean Above who want to book a flight by typing what they
  want ("I want to fly from Guangzhou to Lima"), check the route and price, and later look up their tickets. They use
  a desktop browser or, just as often, a phone.
- **Take-home reviewers**: engineers evaluating the demo (`ocean-above.potafree.net`, or `docker compose up` locally).
  They read the design doc, try edge cases (unserved cities, ambiguous names, refusing to confirm) and check that every
  number on screen is right and that nothing is booked without an explicit confirmation.

## Product Purpose
A natural-language flight booking assistant for Ocean Above. Core jobs:
1. Ask for a flight in plain language and get a quote: the cheapest route with every leg, every transfer city, the
   total distance and the price.
2. Check that full route, distance and price, then book by clicking an explicit confirm control.
3. Look up booked tickets (in chat or on My tickets).
4. Re-read any past conversation (History).
Success: the user can verify every fact before paying, and a reviewer finds no number that does not come from the
backend's deterministic computation.

## Positioning
The language model only understands and phrases; routes, distances, prices, nearest served cities and bookings are
computed by deterministic backend code from `locations.db`. Every number shown comes from a tool result, and booking
happens only when the user clicks a one-time confirm card. The interface makes that split visible: facts live in
structured cards, not in chat prose.

## Operating Context
- Chat screen: conversation list (drawer on phones), messages with streamed assistant text, and cards: quote,
  booking request (confirm), ticket, ticket list, no-service (nearest served cities), ambiguous city (candidates with
  distinguishing facts), unknown city, notice.
- My tickets and History (read-only conversations) pages; a simple login (fixed dev account; Google later).
- Quotes expire; a booking can be refused as stale / expired / busy / data unavailable and the UI must say what to do.

## Capabilities and Constraints
- Numbers are shown only from the backend's `*_display` fields; the frontend never formats or recomputes numbers.
- Quote and confirm cards must make the legs, transfer cities, total distance and price prominent (take-home req. 7).
- Unserved city → tell the user and show the nearest served city (req. 8).
- Nothing is booked without the user clicking confirm on that exact quote; the LLM cannot book.
- Mobile is first-class: ≥ 360 px wide, touch targets ≥ 44×44, no horizontal scroll.
- Stack: Next.js + TypeScript + Tailwind CSS; shadcn/ui-style components copied into the repo. Minimal dependencies.
- English UI copy.

## Brand Commitments
- Name: **Ocean Above** (fictional airline from the take-home). No logo or brand assets exist.
- Character (frontend/DESIGN.md §1): calm, trustworthy, precise; no decorative gradients or illustrations; prices and distances
  always also appear in a structured card; colour never means two things.

## Evidence on Hand
- `locations.db` (cities, hubs, links) and the contract fixtures in `contract/fixtures/` (realistic quotes, tickets,
  conversations). No testimonials, customers or marketing claims exist and none may be invented.

## Product Principles
1. Facts before prose: anything the user must verify is in a structured card.
2. Confirmation is explicit and unmistakable; its state (awaiting, booked, expired, out of date) is always legible.
3. Every failure says what happened and what to do next.
4. Phone and desktop are equals.

## Accessibility & Inclusion
WCAG 2.2 AA: 4.5:1 body text, 3:1 large text and icons; keyboard reachable; visible focus; status never by colour
alone; reduced motion respected.
