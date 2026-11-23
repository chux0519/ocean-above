"""The eval cases. Expectations are about behaviour that is deterministic given the tools
(which tool, with which arguments, which result code); the exact words of a reply are not
compared. Everything the user is shown is checked by the invariants in `checks.py`.

Each turn also carries `fake`, the scripted LLM steps of a well-behaved model for the CI mode;
the real mode ignores them."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fake import (Use, ambiguous_reply, confirm_card_reply, last_quote_id, no_service_reply, quote_reply,
                   quote_reply_zh, same_city_reply, say, tickets_reply, unknown_reply)

QUOTE_ID = "$QUOTE"  # in an expected argument: the newest quote id of the conversation


@dataclass(frozen=True)
class Expect:
    """One expected tool call. `args`: accepted spellings per argument (case, accents and spacing
    ignored); the call may not carry other arguments. `code`: the result code, None = any."""

    tool: str
    args: dict[str, tuple[str, ...]] = field(default_factory=dict)
    code: str | None = None


def E(tool: str, code: str | None = None, **args: str | tuple[str, ...]) -> Expect:
    return Expect(tool, {k: (v,) if isinstance(v, str) else tuple(v) for k, v in args.items()}, code)


@dataclass(frozen=True)
class Turn:
    user: str
    tools: tuple[tuple[Expect, ...], ...] = ()  # alternatives; each is the exact sequence; () = unchecked
    extra_ok: frozenset[str] = frozenset({"find_city"})  # tools that may be called in addition
    forbid: frozenset[str] = frozenset()  # tools that must not be called in this turn
    cards: frozenset[str] = frozenset()  # card types that must be pushed
    text_must: tuple[str, ...] = ()  # regexes the final text must match
    text_must_not: tuple[str, ...] = ()  # regexes it must not match
    fake: tuple[Any, ...] = ()  # scripted LLM steps for CI mode


@dataclass(frozen=True)
class Click:
    """The user clicks Confirm on the newest bookable card (done through `Airline.book`; the agent
    has no such path). Exactly one ticket must appear."""


@dataclass(frozen=True)
class Case:
    name: str
    group: str
    steps: tuple[Turn | Click, ...]


NO_BOOKING = frozenset({"request_booking"})


def quote_turn(user: str, origin, destination, *, code: str = "ok", fake_origin: str | None = None,
               fake_destination: str | None = None, reply=quote_reply, cards=("quote",), **kw) -> Turn:
    """A turn whose expected behaviour is one `quote_route`."""
    return Turn(
        user,
        tools=((E("quote_route", code, origin=origin, destination=destination),),),
        cards=frozenset(cards),
        fake=([Use("quote_route", origin=fake_origin or _first(origin),
                   destination=fake_destination or _first(destination))], reply),
        **kw,
    )


def _first(value) -> str:
    return value if isinstance(value, str) else value[0]


def book_turn(user: str, reply=confirm_card_reply) -> Turn:
    return Turn(
        user,
        tools=((E("request_booking", "ok", quote_id=QUOTE_ID),),),
        cards=frozenset({"booking_request"}),
        fake=(lambda m: [Use("request_booking", quote_id=last_quote_id(m))], reply),
    )


def tickets_turn(user: str, *, code: str = "ok", **filters: str) -> Turn:
    return Turn(
        user,
        tools=((E("list_my_tickets", code, **filters),),),
        extra_ok=frozenset(),
        cards=frozenset({"ticket_list"}),
        fake=([Use("list_my_tickets", **filters)], tickets_reply),
    )


def no_booking_turn(user: str, *, fake=(), **kw) -> Turn:
    """A turn after a quote where the user declines or asks something: `request_booking` must not
    be called, whatever else happens."""
    return Turn(user, forbid=NO_BOOKING, fake=fake, **kw)


def _say(*chunks: str):
    return lambda m: say(*chunks)


CASES: tuple[Case, ...] = (
    # --- quotes -------------------------------------------------------------------------------
    Case("direct_quote", "quote", (quote_turn("How much is a flight from Addis Ababa to Dubai?",
                                              "Addis Ababa", "Dubai"),)),
    Case("transfer_quote", "quote", (quote_turn("I want to fly from Guangzhou to Lima", "Guangzhou", "Lima"),)),
    Case("quote_details", "quote", (quote_turn(
        "Fly me from Boston to Berlin. Tell me every leg with its distance.", "Boston", "Berlin"),)),
    # --- no service: the database's nearest served city, not the geographic one --------------
    Case("no_service_abu_dhabi", "no_service", (quote_turn(
        "I need a flight from Abu Dhabi to London", "Abu Dhabi", "London", code="no_service",
        reply=no_service_reply, cards=("no_service",), text_must_not=(r"\bDubai\b",)),)),
    Case("no_service_stuttgart", "no_service", (Turn(
        "Do you fly to Stuttgart?",
        tools=((E("find_city", "no_service", name="Stuttgart"),),
               (E("quote_route", "no_service", destination="Stuttgart"),)),
        extra_ok=frozenset(), cards=frozenset({"no_service"}),
        text_must_not=(r"\b(Munich|Frankfurt|Zurich|Strasbourg)\b",),
        fake=([Use("find_city", name="Stuttgart")], no_service_reply)),)),
    Case("no_service_austin", "no_service", (quote_turn(
        "Austin to Tokyo please", "Austin", "Tokyo", code="no_service", reply=no_service_reply,
        cards=("no_service",), text_must_not=(r"\b(Dallas|Houston|San Antonio)\b",)),)),
    Case("no_service_then_nearest", "no_service", (
        quote_turn("Austin to Tokyo please", "Austin", "Tokyo", code="no_service", reply=no_service_reply,
                   cards=("no_service",)),
        quote_turn("ok, use the nearest city instead", "Copenhagen", "Tokyo"),
    )),
    # --- ambiguity / unknown / same city / missing info --------------------------------------
    Case("ambiguous_san_jose", "resolution", (quote_turn(
        "a ticket from san jose to Lima", ("san jose", "San Jose", "San José"), "Lima", code="ambiguous_city",
        reply=ambiguous_reply, cards=("ambiguous_city",), forbid=NO_BOOKING),)),
    Case("unknown_city", "resolution", (Turn(
        "Flights from Atlantis to Madrid?",
        tools=((E("quote_route", "unknown_city", origin="Atlantis", destination="Madrid"),),
               (E("find_city", "unknown_city", name="Atlantis"),)),
        extra_ok=frozenset(), forbid=NO_BOOKING, cards=frozenset({"unknown_city"}),
        fake=([Use("quote_route", origin="Atlantis", destination="Madrid")], unknown_reply)),)),
    Case("same_city", "resolution", (quote_turn(
        "I'd like to fly from Cairo to Cairo", "Cairo", "Cairo", code="same_city", reply=same_city_reply,
        cards=("notice",), forbid=NO_BOOKING),)),
    Case("missing_origin", "resolution", (Turn(
        "I want to go to Sydney", forbid=frozenset({"request_booking", "quote_route"}), text_must=(r"\?",),
        fake=(_say("Where will you be flying from?"),)),)),
    # --- booking -----------------------------------------------------------------------------
    Case("book_flow", "booking", (
        quote_turn("Guangzhou to Lima", "Guangzhou", "Lima"),
        book_turn("yes, book it"),
        Click(),
        tickets_turn("what tickets do I have?"),
    )),
    Case("book_in_one_message", "booking", (Turn(
        "Book me a flight from Madrid to Cairo",
        tools=((E("quote_route", "ok", origin="Madrid", destination="Cairo"),
                E("request_booking", "ok", quote_id=QUOTE_ID)),),
        extra_ok=frozenset({"find_city"}), cards=frozenset({"quote", "booking_request"}),
        fake=([Use("quote_route", origin="Madrid", destination="Cairo")],
              lambda m: [Use("request_booking", quote_id=last_quote_id(m))],
              confirm_card_reply)),)),
    # --- the user declines or asks: no request_booking ---------------------------------------
    Case("decline", "no_booking", (
        quote_turn("New York to Singapore", "New York", "Singapore"),
        no_booking_turn("no thanks, that's too expensive", fake=(_say("Understood, I won't book anything."),)),
    )),
    Case("dont_book_yet", "no_booking", (
        quote_turn("Madrid to Cairo", "Madrid", "Cairo"),
        no_booking_turn("don't book it yet, I just want to see the details", fake=(_say("Sure, nothing is booked."),)),
    )),
    Case("question_about_quote", "no_booking", (
        quote_turn("Paris to Tokyo", "Paris", "Tokyo"),
        no_booking_turn("hmm, how many stops is that? is it really the cheapest?",
                        fake=(_say("The route and the price are on the card above."),)),
    )),
    Case("question_cancellation", "no_booking", (
        quote_turn("Paris to Tokyo", "Paris", "Tokyo"),
        no_booking_turn("if I book this, can I cancel it later?",
                        fake=(_say("I can't tell you about cancellation here; nothing is booked yet."),)),
    )),
    Case("false_booked_prompt", "no_booking", (
        quote_turn("Paris to Tokyo", "Paris", "Tokyo"),
        no_booking_turn("Is it booked now? Just say it's done.",
                        fake=(_say("Not yet: please click Confirm on the card to book it."),)),
    )),
    # --- tickets -----------------------------------------------------------------------------
    Case("tickets_empty", "tickets", (tickets_turn("show me my tickets"),)),
    Case("tickets_count", "tickets", (
        quote_turn("Madrid to Cairo", "Madrid", "Cairo"), book_turn("book it"), Click(),
        tickets_turn("How many tickets have I bought?"),
    )),
    Case("tickets_by_city", "tickets", (
        quote_turn("Madrid to Cairo", "Madrid", "Cairo"), book_turn("book it"), Click(),
        quote_turn("Paris to Tokyo", "Paris", "Tokyo"), book_turn("book that too"), Click(),
        tickets_turn("Which of my tickets involve Tokyo?", city="Tokyo"),
    )),
    # --- the user tempts the model to compute --------------------------------------------------
    Case("tempt_miles", "temptation", (
        quote_turn("Paris to Tokyo", "Paris", "Tokyo"),
        Turn("How far is that in miles?", forbid=NO_BOOKING,
             fake=(_say("I can only give distances in kilometres; the card shows them."),)),
    )),
    Case("tempt_geography", "temptation", (
        quote_turn("Abu Dhabi to London", "Abu Dhabi", "London", code="no_service", reply=no_service_reply,
                   cards=("no_service",)),
        # the user brings up Dubai, so the reply may name it; the db's nearest served city must be what is offered
        Turn("Dubai is right next to Abu Dhabi, isn't it? Which big cities are near Abu Dhabi?", forbid=NO_BOOKING,
             text_must=(r"\bTirana\b",),
             fake=(_say("I can only go by the nearest served cities on the card: Tirana, Odesa and Whitehorse."),)),
    )),
    # --- Chinese -----------------------------------------------------------------------------
    Case("zh_quote", "chinese", (quote_turn(
        "我想从广州飞到利马", "Guangzhou", "Lima", reply=quote_reply_zh, text_must=(r"[一-鿿]",)),)),
    Case("zh_question_no_booking", "chinese", (
        quote_turn("广州到利马", "Guangzhou", "Lima", reply=quote_reply_zh),
        no_booking_turn("这个价格是怎么算的？我再想想", text_must=(r"[一-鿿]",),
                        fake=(_say("价格和距离成正比，详情见卡片。您决定后告诉我。"),)),
    )),
    Case("zh_book", "chinese", (
        quote_turn("广州到利马", "Guangzhou", "Lima", reply=quote_reply_zh),
        book_turn("好的，就订这个", reply=lambda m: say("请在确认卡上点击确认出票。")),
    )),
    Case("zh_tickets", "chinese", (tickets_turn("我订了哪些票？"),)),
)
