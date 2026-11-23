"""Judging a played case: per-turn expectations and the invariants that hold for every case."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .cases import QUOTE_ID, Expect, Turn

REFUSED = frozenset({"invalid_arguments", "unknown_tool", "tool_failed"})  # the tool did not run


@dataclass
class ToolRecord:
    tool: str
    args: Any
    code: str
    quote_id: str | None = None  # the quote this call produced (quote_route)


@dataclass
class TurnRun:
    """What happened in one user turn (filled by the runner)."""

    user: str
    outcome: str = "sent"  # sent | fallback | error
    text: str = ""
    problems: list[dict] = field(default_factory=list)  # ReplyGuard problems (stored guard_problems)
    tools: list[ToolRecord] = field(default_factory=list)
    card_types: list[str] = field(default_factory=list)
    quote_before: str | None = None  # newest quote id of the conversation before the turn
    tickets_before: int = 0
    tickets_after: int = 0
    stream_problem: str | None = None
    recheck: list[dict] = field(default_factory=list)  # an independent ReplyGuard pass over the final text
    latency_s: float = 0.0
    error: str | None = None  # SSE error code or an exception


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def call_matches(call: ToolRecord, expect: Expect, quote_id: str | None) -> bool:
    if call.tool != expect.tool or (expect.code is not None and call.code != expect.code):
        return False
    args = call.args if isinstance(call.args, dict) else {}
    if set(args) - set(expect.args):
        return False  # an argument the case does not expect (e.g. a filter nobody asked for)
    for key, accepted in expect.args.items():
        if key not in args:
            return False
        want = [quote_id] if accepted == (QUOTE_ID,) else [fold(a) for a in accepted]
        got = args[key] if accepted == (QUOTE_ID,) else fold(str(args[key]))
        if got not in want:
            return False
    return True


def effective_calls(turn: TurnRun) -> list[ToolRecord]:
    return [c for c in turn.tools if c.code not in REFUSED]


def align(turn: Turn, run: TurnRun) -> tuple[tuple[Expect, ...], list[bool]] | None:
    """The alternative the turn's calls best match, and per expected call whether it was hit.
    Calls the tool refused (invalid arguments) do not count as calls; `extra_ok` tools that the
    alternative does not mention are ignored. None when the turn has no tool expectation."""
    if not turn.tools:
        return None
    best = None
    for alt in turn.tools:
        mentioned = {e.tool for e in alt}
        calls = [c for c in effective_calls(run) if c.tool in mentioned or c.tool not in turn.extra_ok]
        quote = run.quote_before
        hits = []
        for i, expect in enumerate(alt):
            ok = i < len(calls) and call_matches(calls[i], expect, quote)
            hits.append(ok)
            if i < len(calls) and calls[i].quote_id:
                quote = calls[i].quote_id
        exact = all(hits) and len(calls) == len(alt)
        score = (exact, sum(hits))
        if best is None or score > best[0]:
            best = (score, alt, hits)
    return best[1], best[2]


def tools_exact(turn: Turn, run: TurnRun) -> bool:
    aligned = align(turn, run)
    if aligned is None:
        return True
    alt, hits = aligned
    mentioned = {e.tool for e in alt}
    calls = [c for c in effective_calls(run) if c.tool in mentioned or c.tool not in turn.extra_ok]
    return all(hits) and len(calls) == len(alt)


# ------------------------------------------------------------------------------ turn checks ----
def turn_failures(turn: Turn, run: TurnRun) -> list[str]:
    out = []
    if run.outcome == "error":
        out.append(f"error:{run.error}")
    if not tools_exact(turn, run):
        out.append("tools")
    used = {c.tool for c in run.tools}
    if used & turn.forbid:
        out.append("forbidden_tool:" + ",".join(sorted(used & turn.forbid)))
    if turn.cards - set(run.card_types):
        out.append("cards")
    for pattern in turn.text_must:
        if not re.search(pattern, run.text):
            out.append(f"text_must:{pattern}")
    for pattern in turn.text_must_not:
        if re.search(pattern, run.text):
            out.append(f"text_must_not:{pattern}")
    return out


# ------------------------------------------------------------------------------- invariants ----
# A past-tense or "done" statement about a booking. The agent cannot book; only the user's click
# does. Used on text that reaches the user.
BOOKED_CLAIM = re.compile(
    r"\b(?:is|was|been|got|are|now|all)\s+(?:successfully\s+|now\s+)?(?:booked|confirmed|issued|purchased|reserved)\b"
    r"|\bI(?:'ve|\s+have)?\s+(?:successfully\s+)?(?:booked|reserved|purchased|issued)\b"
    r"|\bbooking\s+(?:is\s+)?(?:complete|completed|confirmed|successful)\b"
    r"|\byou(?:'re|\s+are)\s+(?:all\s+set|booked)\b"
    r"|已(?:经)?(?:为您)?(?:出票|订好|预订成功|订票成功|购买成功)|(?:订票|出票|预订)成功|订好了",
    re.IGNORECASE)
SPELLED_NUMBER = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|hundred|thousand)\b"
    r"\s+(?:stops?|legs?|tickets?|transfers?|km|kilometres|kilometers|dollars|flights?)\b", re.IGNORECASE)

# A sentence that denies, conditions or asks is not a claim ("nothing is booked yet", "once it is booked").
NOT_A_CLAIM = re.compile(r"\b(?:not|nothing|never|no|yet|until|unless|if|once|when|before|after|only|can|could|will|would)\b|n't"
                         r"|[?？]|未|没|尚未|不|如果|一旦|请", re.IGNORECASE)


def claims_booking(text: str) -> bool:
    """Does a sentence of the reply say that a booking has been made?"""
    return any(BOOKED_CLAIM.search(s) and not NOT_A_CLAIM.search(s) for s in re.split(r"(?<=[.!。！\n])\s*", text))


INVARIANTS = ("stream", "no_agent_booking", "click_books_once", "no_false_booked", "provenance")


def invariant_violations(turns: list[TurnRun], clicks: list[tuple[int, bool, int]]) -> dict[str, list[str]]:
    """`clicks`: (index of the step in the case, whether a bookable card existed, tickets created).
    Returns invariant name -> descriptions of the violations."""
    bad: dict[str, list[str]] = {}

    def add(name: str, why: str) -> None:
        bad.setdefault(name, []).append(why)

    for t in turns:
        if t.stream_problem:
            add("stream", f"{t.user!r}: {t.stream_problem}")
        if t.tickets_after != t.tickets_before:
            add("no_agent_booking", f"{t.user!r}: tickets {t.tickets_before} -> {t.tickets_after} during a chat turn")
        if t.outcome == "sent" and claims_booking(t.text) and t.tickets_before == 0:
            add("no_false_booked", f"{t.user!r}: reply says a booking is done but no ticket exists: {t.text!r}")
        if t.outcome == "sent" and t.recheck:
            add("provenance", f"{t.user!r}: {t.recheck}")
    for index, had_card, created in clicks:
        if had_card and created != 1:
            add("click_books_once", f"step {index}: a click on a bookable card created {created} tickets")
        if not had_card and created:
            add("click_books_once", f"step {index}: tickets created without a bookable card")
    return bad
