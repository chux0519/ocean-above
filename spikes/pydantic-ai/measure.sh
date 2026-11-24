#!/bin/bash
# Footprint of adding pydantic-ai-slim[openai] to the backend's runtime deps (issue #49 POC).
# Two throwaway venvs under $TMPDIR: the backend's runtime deps alone, and with pydantic-ai-slim[openai].
set -euo pipefail
BASE="fastapi>=0.115 uvicorn>=0.30 networkx>=3.3 httpx>=0.27 pyjwt[crypto]>=2.10"
T="$(mktemp -d)"
for v in base pa; do
  uv venv -q --python 3.12 "$T/$v"
done
nice -n 10 uv pip install -q --python "$T/base/bin/python" $BASE
nice -n 10 uv pip install -q --python "$T/pa/bin/python" $BASE "pydantic-ai-slim[openai]"
for v in base pa; do
  sp=$("$T/$v/bin/python" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
  n=$(uv pip list -q --python "$T/$v/bin/python" 2>/dev/null | tail -n +3 | wc -l | tr -d ' ')
  kb=$(du -sk "$sp" | cut -f1)
  echo "$v packages=$n site_packages_kb=$kb"
done
echo "added packages:"
comm -13 <(uv pip list -q --python "$T/base/bin/python" | tail -n +3 | awk '{print $1}' | sort) \
         <(uv pip list -q --python "$T/pa/bin/python" | tail -n +3 | awk '{print $1}' | sort) | tr '\n' ' '; echo
echo "largest added (kb):"
sp=$("$T/pa/bin/python" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
for p in pydantic_ai openai tiktoken genai_prices httpx2 httpcore2 regex opentelemetry pydantic_graph griffe jiter requests charset_normalizer urllib3 truststore logfire_api; do
  [ -d "$sp/$p" ] && echo "  $p $(du -sk "$sp/$p" | cut -f1)"
done
echo "cold import (median of 7, fresh interpreter, seconds):"
imp() { "$T/pa/bin/python" - "$1" <<'PY'
import subprocess, sys, time, statistics, os
code = sys.argv[1]
ts = []
for _ in range(7):
    t = time.perf_counter(); subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1"}); ts.append(time.perf_counter() - t)
print(f"{statistics.median(ts):.3f}")
PY
}
echo "  python -c pass: $(imp 'pass')"
echo "  fastapi+httpx (today's agent imports): $(imp 'import fastapi, httpx, networkx')"
echo "  + pydantic_ai Agent/OpenAIChatModel/DeepSeekProvider: $(imp 'import fastapi, httpx, networkx; from pydantic_ai import Agent; from pydantic_ai.models.openai import OpenAIChatModel; from pydantic_ai.providers.deepseek import DeepSeekProvider')"
rm -rf "${T:?}"
