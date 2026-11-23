"""LLM evals for the booking agent (design §10, issue #3).

A *case* is a multi-turn conversation with expectations per turn (tool sequence with checked
arguments, cards, forbidden tools, text patterns) plus invariants that hold for every case
(nothing is booked without the user's click, no false "booked" claims, every number and city in
the reply has a source). The runner plays each case N times against an LLM, either a scripted
fake (CI: free, deterministic, tests the harness) or the real model (on demand, costs money), and
reports tool-call accuracy per tool, trajectory pass rate, ReplyGuard fallback rate, tokens, cost
and latency.

    cd backend && PYTHONPATH=. uv run python -m evals --mode fake
    cd backend && OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals --mode real --n 3 --effort high
"""
