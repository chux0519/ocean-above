"""Dev-only server for the 09-stop screenshots: the real backend (routes, agent loop, run registry,
Stop endpoint, app.db) with a scripted, deliberately slow model instead of a real LLM, so Stop can
be shown while waiting for the first token and mid-reply. No network, no API key.

    cd backend && APP_DB=/tmp/oa-stop/app.db DEV_AUTH_USER=demo DEV_AUTH_PASSWORD=<pw> SESSION_SECRET=<s> \
        uv run python ../docs/iterations/09-stop/slow_server.py   # serves 0.0.0.0:8000

First call of a turn: waits FIRST_TOKEN_S, then quotes Guangzhou -> Lima. Second call: streams
one sentence every SENTENCE_S seconds (no numbers, so the ReplyGuard lets them through)."""
from __future__ import annotations

import asyncio
import os

import uvicorn

from app.agent import Completion, ScriptedLLM, TextDelta
from app.main import create_app

FIRST_TOKEN_S = float(os.environ.get("FIRST_TOKEN_S", "6"))
SENTENCE_S = float(os.environ.get("SENTENCE_S", "1.5"))
SENTENCES = ["I found the cheapest route for you. ", "Every leg and transfer city is on the route card. ",
             "Nothing is booked until you press the confirm button. ", "Tell me if you want to compare another day. ",
             "I can also look for a route from a nearby city. ", "Ask me anything else about this trip. "] * 4
USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}


class SlowLLM(ScriptedLLM):
    model = "slow-scripted"

    async def stream(self, messages, tools, *, reasoning_effort=None):
        last = messages[-1] if messages else {}
        if last.get("role") != "tool":
            await asyncio.sleep(FIRST_TOKEN_S)
            yield Completion("", [{"id": "call_q", "name": "quote_route",
                                   "arguments": '{"origin":"Guangzhou","destination":"Lima"}'}], "tool_calls", USAGE)
            return
        text = ""
        for sentence in SENTENCES:
            await asyncio.sleep(SENTENCE_S)
            text += sentence
            yield TextDelta(sentence)
        yield Completion(text, [], "stop", USAGE)


if __name__ == "__main__":
    uvicorn.run(create_app(llm=SlowLLM()), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
