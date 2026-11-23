# DESIGN — ocean-above

Single source of truth for visual and interaction rules. Only the coordinator edits this file; user decisions
are written here with their date.

## 1. Character
A calm, trustworthy airline booking assistant: a chat where the user types what they want, and the app
answers with precise, checkable facts — every leg, transfer city, distance and price laid out as a clear
itinerary card before anything is booked. Booked tickets and past conversations are always one click away.
It refuses: decorative gradients and illustrations, numbers that only appear in free text (prices and
distances always also appear in a structured card), a "book" action that is not an explicit confirmation,
and colour that means two things.

## 2. Colour
- Neutral surfaces + one accent. Status colours only with text or an icon.
- Contrast: 4.5:1 body text, 3:1 icons and large text, in every theme.

## 3. Type, spacing, shape
- Type scale: <sizes>. Spacing: 4 / 8 / 12 / 16 / 24. Touch targets ≥ 44×44.
- Corner radii: <values>. Lines: <hairline width>.

## 4. Motion
- <Duration, curve>. Reduce Motion → 0. No loops except <the one allowed indeterminate indicator>.

## 5. Components
- Component layer: shadcn/ui components (copied into `frontend/components/ui`) on Tailwind CSS, wrapped by
  app components (itinerary card, ticket card, chat message, conversation list). New UI uses these.

## 6. Screens
<Per screen: hierarchy, primary action, empty state, errors.>

## 7. Exceptions (dated)
- 2026-11-23: mobile is first-class (≥ 360 px wide, no horizontal scroll); screenshots at desktop + mobile.
- 2026-11-23: numbers are shown rounded (distance 1 decimal km, price to the cent) but always computed and
  stored exact.
- 2026-11-23: UI is reviewed with Impeccable (critique / audit / detect); its findings that conflict with this
  file go to the user as a design-conflicts table.
- <YYYY-MM-DD: decision, why, limits.>
