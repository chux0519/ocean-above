# Spike: OpenAI Agents SDK on DeepSeek (#49 phase 1)

Throwaway POC, not used by the app. Report: `docs/iterations/08-runtime/poc-openai-agents.md`.

- `poc/common.py` — imports `backend/app` (tools, airline, store, guard), model factory, tool adapter
  (`FunctionTool` -> `Toolbox.run` in `Threads`), trusted run context, trace processor, cost.
- `poc/turn.py` — one turn: SDK events -> guarded sentences / status / card / replace.
- `poc/fake_server.py` — raw-socket OpenAI-compatible SSE server that observes TCP close.
- `poc/f_*.py` — fake-server checks (free); `poc/r*_*.py`, `poc/m_baseline.py` — real DeepSeek (key via `OCEAN_ENV_FILE`).
- `out/` — the outputs quoted in the report; `out/cost.json` — real-model spend.

```bash
uv sync --frozen && cd poc && ../.venv/bin/python f_cancel.py
```
