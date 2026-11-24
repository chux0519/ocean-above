"""Does real DeepSeek accept a request whose history holds a malformed tool call (a name outside
`^[A-Za-z0-9_-]{1,64}$`, arguments that are not JSON) the way the SDK echoes it back? And does a
`call_model_input_filter` that applies our `wire_tool_call` rules fix it?
Run: cd spikes/openai-agents/poc && ../.venv/bin/python r3_wire.py
"""
from __future__ import annotations

import asyncio

from agents.run_config import CallModelData, ModelInputData

from common import (Cost, StepHooks, Toolbox, TurnCtx, World, deepseek_settings, make_agent, make_model, p,
                    quiet_tracing, real_config, record_cost, run_config)
from turn import run_turn

from app.agent.guard import CityIndex
from app.agent.llm import wire_tool_call

HISTORY = [
    {"role": "user", "content": "Find Paris for me."},
    {"type": "function_call", "call_id": "call_bad_1", "name": "bad name!", "arguments": '{"name": "Par'},
    {"type": "function_call_output", "call_id": "call_bad_1", "output": '{"code":"unknown_tool"}'},
    {"role": "user", "content": "Sorry, just say hello."},
]


def sanitize(data: CallModelData) -> ModelInputData:
    """The narrow adapter: every function_call item goes to the model in its wire-safe form."""
    items = []
    for item in data.model_data.input:
        if isinstance(item, dict) and item.get("type") == "function_call":
            wire = wire_tool_call(item["call_id"], item["name"], item["arguments"])["function"]
            item = {**item, "name": wire["name"], "arguments": wire["arguments"]}
        items.append(item)
    return ModelInputData(input=items, instructions=data.model_data.instructions)


async def main() -> None:
    quiet_tracing()
    cfg = real_config()
    world = World("r3")
    cost = Cost()
    try:
        for label, filt in (("SDK as-is", None), ("with call_model_input_filter(wire_tool_call)", sanitize)):
            model, client = make_model(cfg.base_url, cfg.api_key, cfg.model)
            agent = make_agent(model, deepseek_settings("none"))
            ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline))
            hooks = StepHooks()
            kw = {"call_model_input_filter": filt} if filt else {}
            log = await run_turn(agent, ctx, list(HISTORY), index=CityIndex(world.networks.current().cities),
                                 user_text="Sorry, just say hello.", run_config=run_config(**kw), hooks=hooks)
            for raw in log.raw_usage:
                cost.add_raw(raw)
            p(f"[{label}] error={log.error!r}; reply={log.released[:80]!r}")
            await client.close()
    finally:
        world.close()
        record_cost("r3_wire", cost)
        p(f"cost this run: ${cost.usd:.5f}")


if __name__ == "__main__":
    asyncio.run(main())
