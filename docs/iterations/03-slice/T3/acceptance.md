# Acceptance — T3 agent (2026-11-23)

- Tested source: PR #14 head `f5e4592` (squash-merged as `6fee492`). Coordinator reran the backend suite
  (`HYPOTHESIS_PROFILE=ci`): 491 passed; scope = agent/, tests, pyproject (httpx to runtime deps), T3 docs.
- Independent read-only review (Fable 5.2): no P0 (no booking path in the agent, no nonce to the LLM); 4 P1 guard
  bypasses (letter-prefixed digits, `_City_`, echoed tool arguments as records, false "booked" claims) and 13 P2 —
  all fixed in round 02 with tests asserting the bad text never reaches any event.
- Real model (deepseek-flash, effort high): 50/50 turns sent unchanged, 0 fallbacks, 0 booking claims; task total ≈ $0.09.
- Worker switched from Fable to Opus 5.5 mid-task (persistent 529), session resumed. Design §4.5 synced.
