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
- Own component layer: <list>. New UI uses it, not raw framework widgets.

## 6. Screens
<Per screen: hierarchy, primary action, empty state, errors.>

## 7. Exceptions (dated)
- <YYYY-MM-DD: decision, why, limits.>
