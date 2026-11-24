"""Check 11: default remote trace export, how to turn it off, and what a custom processor receives.

Each mode runs in a fresh subprocess (tracing state is process-global). The SDK's default exporter
is monkeypatched to *record* instead of POSTing, so nothing is sent even in the "default" mode.
Run: cd spikes/openai-agents/poc && ../.venv/bin/python f_trace.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

MODES = ["default", "replaced", "env_disabled", "runconfig_disabled", "sensitive_on"]


async def child(mode: str) -> None:
    if mode == "env_disabled":
        os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["OPENAI_API_KEY"] = "sk-not-a-real-openai-key"  # worst case: a key is present
    from agents.tracing import custom_span
    from agents.tracing.processors import BackendSpanExporter

    exported: list[str] = []
    endpoint = BackendSpanExporter().endpoint

    def fake_export(self, items):  # never touches the network
        exported.extend(type(i).__name__ for i in items)

    BackendSpanExporter.export = fake_export

    import common
    from common import (Runner, Toolbox, TurnCtx, World, deepseek_settings, make_agent, make_model, quiet_tracing,
                        run_config)
    from fake_server import FakeServer, text, tool_calls
    from agents.tracing import flush_traces

    if mode in ("replaced", "sensitive_on"):
        quiet_tracing()
    world = World("trace")
    try:
        async with FakeServer(scripts=[tool_calls([("quote_route", '{"origin":"Paris","destination":"Tokyo"}')]),
                                       text("Here it is.")]) as server:
            model, client = make_model(server.base_url, "sk-fake", "deepseek-flash", trust_env=False)
            ctx = TurnCtx(world.user, world.conv, Toolbox(world.airline))
            agent = make_agent(model, deepseek_settings("high"))
            kw = {}
            if mode == "runconfig_disabled":
                kw["tracing_disabled"] = True
            if mode == "sensitive_on":
                kw["trace_include_sensitive_data"] = True
            # a business span of ours (#35: route.search, reply_guard...) inside the SDK trace
            with_custom = []

            original = ctx.toolbox.run

            def run_with_span(*a, **k):
                with custom_span("route.search", {"tool": a[2]}):
                    with_custom.append(1)
                    return original(*a, **k)

            ctx.toolbox.run = run_with_span
            result = Runner.run_streamed(agent, "Paris to Tokyo please", context=ctx, run_config=run_config(**kw))
            async for _ in result.stream_events():
                pass
            await client.close()
        flush_traces()
        spans = common.TRACES.spans
        kinds: dict[str, int] = {}
        for s in spans:
            kinds[s["type"]] = kinds.get(s["type"], 0) + 1
        gen = next((s["export"] for s in spans if s["type"] == "generation"), None)
        out = {
            "mode": mode,
            "default_exporter_endpoint": endpoint,
            "default_exporter_would_send": len(exported),
            "custom_processor_spans": kinds,
            "custom_span_parented": [s["parent"] for s in spans if s["type"] == "custom"],
            "generation_input_present": bool(gen and gen.get("input")),
            "generation_input_sample": (json.dumps(gen.get("input"))[:160] if gen and gen.get("input") else None),
            "usage_on_span": gen and gen.get("usage"),
            "outbound_connections": common.hosts(),
        }
        print(json.dumps(out))
    finally:
        world.close()


def main() -> None:
    for mode in MODES:
        r = subprocess.run([sys.executable, __file__, mode], capture_output=True, text=True, timeout=120)
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            data = json.loads(line)
        except ValueError:
            print(mode, "FAILED", r.stderr[-800:])
            continue
        print(f"\n=== {mode}")
        for k, v in data.items():
            if k != "mode":
                print(f"  {k}: {v}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(child(sys.argv[1]))
    else:
        main()
