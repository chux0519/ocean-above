# T5 STATE (part 2)

- Done: message SSE route wired to T3's agent; 503 without key; surrogate fix; `.env.example`; tests; 427 passed.
- Depends on PR #14 (T3): merge #14 first. In progress: nothing. Next: coordinator review.
- Open decisions: none. Not done (by design): rate limits (design §8.2).
- Verify: `cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q`; `bash tool/test_diff.sh`; `python3 tool/review_lint.py`.
