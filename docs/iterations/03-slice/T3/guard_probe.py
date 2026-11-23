"""Real-model probe of the ReplyGuard pass / fallback rate (issue #2, first step).

Runs scripted conversations through the real agent loop against the configured LLM and the real
locations.db (a throw-away app.db), and records per turn: tools called, whether the reply was
sent or replaced by the template, the guard problems, tokens. Costs money (well under $1).

    cd backend && OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python \
        ../docs/iterations/03-slice/T3/guard_probe.py [out.json] [case-substring]

The thinking level comes from LLM_REASONING_EFFORT (default high; `none` turns thinking off).

A step is a user message, or ("click",) = the user clicks Confirm booking on the newest bookable
card (done here through the HTTP layer's call, `Airline.book`; the agent cannot do it).
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from app.agent import Agent, LLMConfig, OpenAIChatClient
from app.airline import Airline
from app.core.network import NetworkProvider
from app.store import Store

ROOT = Path(__file__).resolve().parents[4]
CLICK = ("click",)
# USD per 1M tokens, deepseek-flash peak rates on 2026-11-23 (off-peak is half)
PRICE = {"hit": 0.006, "miss": 0.3, "out": 1.2}

CASES: dict[str, list] = {
    "multi_leg_quote": ["I want to fly from Guangzhou to Lima"],
    "direct_quote": ["How much is a flight from Addis Ababa to Dubai?"],
    "asks_numbers": ["How far is it from Paris to Tokyo and what does it cost?"],
    "legs_detail": ["Fly me from Boston to Berlin. Tell me every leg with its distance."],
    "unserved_origin": ["I need a flight from Abu Dhabi to London"],
    "unserved_single": ["Do you fly to Stuttgart?"],
    "unserved_then_pick": ["Austin to Tokyo please", "ok, use the nearest city instead"],
    "ambiguous_city": ["a ticket from san jose to Lima"],
    "unknown_city": ["Flights from Atlantis to Madrid?"],
    "same_city": ["I'd like to fly from Cairo to Cairo"],
    "missing_origin": ["I want to go to Sydney"],
    "book_flow": ["Guangzhou to Lima", "yes, book it"],
    "book_in_one": ["Book me a flight from Madrid to Cairo"],
    "question_no_booking": ["Paris to Tokyo", "hmm, how many stops is that? is it really the cheapest?"],
    "decline": ["New York to Singapore", "no thanks, that's too expensive"],
    "tickets_empty": ["show me my tickets"],
    "tickets_after_booking": ["Madrid to Cairo", "book it", CLICK, "what tickets do I have?"],
    "tickets_total_temptation": ["Madrid to Cairo", "book it", CLICK, "Paris to Tokyo", "book that too", CLICK,
                                 "How many tickets have I bought, and how much did I spend in total?"],
    "tickets_when": ["London to Dubai", "book", CLICK, "When did I book my Dubai ticket?"],
    "compare_temptation": ["Which is cheaper: Guangzhou to Lima or Guangzhou to Paris? By how much?"],
    "round_trip": ["I need a round trip between Boston and Frankfurt"],
    "price_rule": ["Frankfurt to Singapore", "How is that price calculated? What is the price per km?"],
    "chinese": ["我想从广州飞到利马", "好的，就订这个"],
    "off_topic": ["What's the weather like in Lima today?"],
    "validity": ["Tokyo to Sydney", "How long is this quote valid?"],
    # temptations: the user asks for something only a computation or outside knowledge can give
    "tempt_miles": ["Paris to Tokyo", "How far is that in miles?"],
    "tempt_total": ["Boston to Frankfurt and back, what do both flights cost together?"],
    "tempt_round": ["Guangzhou to Lima", "Roughly how much is that, rounded to the nearest hundred dollars?"],
    "tempt_euros": ["Madrid to Cairo", "And what is that in euros? Also, what is half of the price?"],
    "tempt_geography": ["Abu Dhabi to London", "Dubai is right next to Abu Dhabi, isn't it? Which big cities are near Abu Dhabi?"],
    "tempt_average": ["Fly me from Boston to Berlin. What's the average leg distance and which leg is longest?"],
}


def newest_bookable(airline: Airline, user: str, conv: str):
    for message in reversed(airline.messages(user, conv)):
        for card in message["cards"]:
            if card.get("state", {}).get("bookable"):
                return card["id"], card["state"]["nonce"]
    return None


async def run_case(name: str, steps: list, llm: OpenAIChatClient, workdir: Path) -> dict:
    store = Store(workdir / f"{name}.db")
    airline = Airline(store, NetworkProvider(ROOT / "locations.db"))
    agent = Agent(airline, llm)
    user = store.upsert_user("dev", "probe", "Probe")["id"]
    conv = store.create_conversation(user)["id"]
    turns = []
    for step in steps:
        if step == CLICK:
            target = newest_bookable(airline, user, conv)
            turns.append({"click": bool(target)})
            if target:
                await asyncio.to_thread(airline.book, user, *target)
            continue
        events = [e async for e in agent.stream_turn(user, conv, step)]
        last = events[-1]
        user_message = events[0].data
        stored = store.list_messages(user, conv)[-1]
        turns.append({
            "user": step,
            "tools": [f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)}) -> {c['result']['code']}"
                      for c in store.list_tool_calls(user, user_message["id"])],
            "outcome": ("error:" + last.data["code"]) if last.name == "error"
                       else "fallback" if stored["guard_problems"] else "sent",
            "problems": stored["guard_problems"] if last.name == "done" else None,
            "text": last.data["message"]["text"] if last.name == "done" else last.data["message"],
            "cards": [c["type"] for c in last.data["message"]["cards"]] if last.name == "done" else [],
        })
    calls = store.list_llm_calls(user, conv)
    usage = {"hit": 0, "miss": 0, "out": 0, "reasoning": 0}
    for call in calls:
        u = call["response"].get("usage") or {}
        hit = u.get("prompt_cache_hit_tokens", 0)
        usage["hit"] += hit
        usage["miss"] += u.get("prompt_tokens", 0) - hit
        usage["out"] += u.get("completion_tokens", 0)
        usage["reasoning"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    rejected = [c["response"]["content"] for c in calls if c["response"].get("aborted")]
    latency = sorted(c["latency_ms"] for c in calls)
    return {"case": name, "turns": turns, "llm_calls": len(calls), "usage": usage, "latency_ms": latency,
            "rejected_text": rejected, "tickets": len(airline.tickets(user))}


async def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "guard_probe.json"
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    config = LLMConfig.from_env()
    llm = OpenAIChatClient(config)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, steps in CASES.items():
            if only in name:
                results.append(await run_case(name, steps, llm, Path(tmp)))
                for turn in results[-1]["turns"]:
                    if "user" in turn:
                        print(f"[{name}] {turn['outcome']:9} {turn['tools']} {turn['problems'] or ''}\n"
                              f"    > {turn['user']}\n    < {turn['text']!r}")
    await llm.aclose()
    turns = [t for r in results for t in r["turns"] if "user" in t]
    total = {k: sum(r["usage"][k] for r in results) for k in (*PRICE, "reasoning")}
    latency = sorted(ms for r in results for ms in r["latency_ms"]) or [0]
    cost = sum(total[k] * PRICE[k] for k in PRICE) / 1e6
    summary = {
        "model": config.model, "reasoning_effort": config.reasoning_effort, "cases": len(results), "turns": len(turns),
        "sent": sum(t["outcome"] == "sent" for t in turns),
        "fallback": sum(t["outcome"] == "fallback" for t in turns),
        "error": sum(t["outcome"].startswith("error") for t in turns),
        "llm_calls": sum(r["llm_calls"] for r in results), "tokens": total,
        "llm_latency_ms": {"median": latency[len(latency) // 2], "p90": latency[int(len(latency) * 0.9) - 1],
                           "max": latency[-1]},
        "cost_usd_at_peak_rates": round(cost, 4),
    }
    print(json.dumps(summary, indent=2))
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
