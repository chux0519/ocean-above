# STATE — T4 round 01
- Done: eval harness, 27 cases, CI tests, three real runs (final = `../report.md`, $0.039), notes in `../implementation.md`.
- Next: coordinator review; bench (#12) reuses the runner. Open: ReplyGuard gap "it has been booked" (see notes §发现 1).
- Verify: `cd backend && uv run pytest -q tests/test_evals.py`; `bash tool/test_diff.sh`; `python3 tool/review_lint.py`; real: see implementation.md.
