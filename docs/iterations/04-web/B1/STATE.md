# B1 similar_names on cards — STATE
- Done: QuoteCard and BookingRequestCard carry `similar_names` (contract v1.1): cities whose accent-folded name equals
  an endpoint (excluding both endpoints), with connected hubs; `[]` otherwise. Computed at render time from the current
  network (`_similar` in service.py), so every read path (quote, request_booking, get_quote, message view) agrees.
  If locations.db is unreadable the hint is `[]` and the card still renders.
- Booking logic: untouched.
- Tests: test_airline_tools.py (Tokyo→San Jose / San José, hubs, shape vs fixtures, plain route []); full suite 600 passed.
- Next: coordinator review and merge. Verify: `cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q`.
