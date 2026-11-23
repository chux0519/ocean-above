"""ReplyGuard: numbers, dates, ids and in-db city names in LLM text are traced by value."""
from __future__ import annotations

import random
from datetime import date

import pytest

from app.agent.guard import CityIndex, ReplyGuard, SentenceBuffer, fold
from app.agent.templates import NO_ANSWER, fallback_reply
from app.agent.tools import Toolbox
from app.core import places

from .airline_helpers import World

QUOTE = {
    "code": "ok", "quote_id": "q_7f3a9c0d1e2b4a56", "origin": "Guangzhou", "destination": "Lima",
    "legs": [{"from": "Guangzhou", "to": "Frankfurt", "distance_display": "1,362.1 km"},
             {"from": "Frankfurt", "to": "Singapore", "distance_display": "1,872.4 km"},
             {"from": "Singapore", "to": "Amsterdam", "distance_display": "4,097.1 km"},
             {"from": "Amsterdam", "to": "Lima", "distance_display": "1,597.0 km"}],
    "transfers": ["Frankfurt", "Singapore", "Amsterdam"], "direct": False,
    "distance_display": "8,928.6 km", "price_display": "$1,160.71",
    "expires_at": "2026-11-23T08:15:00Z", "status": "quoted", "leg_count": 4, "transfer_count": 3,
}
NO_SERVICE = {"code": "no_service", "city": "Abu Dhabi", "served": False, "nearest": [
    {"city": "Tirana", "distance_display": "512.3 km"}, {"city": "Odesa", "distance_display": "640.0 km"}]}


@pytest.fixture(scope="module")
def index(net):
    return CityIndex(net.cities)


def guard(index, *records, user=""):
    return ReplyGuard(index, records=records, user_text=user, today=date(2026, 11, 23))


def kinds(problems):
    return [(p["kind"], p["value"]) for p in problems]


# --- numbers --------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "The route is 8,928.6 km and costs $1,160.71.",
    "It costs **$1,160.71** (8928.6 km).",  # same values, other formatting
    "There are 4 legs and 3 transfers.",  # counted by code: leg_count / list lengths
    "The first leg is 1,362.1 km, the last one 1,597.0 km.",
    "The last leg is 1,597 km.",  # 1,597.0 == 1597
    "1. Guangzhou to Frankfurt\n2. Frankfurt to Singapore\n3) Amsterdam to Lima",  # list markers
    "Your quote q_7f3a9c0d1e2b4a56 is ready.",  # record id
    "No numbers at all, just words about Lima.",
])
def test_traced_numbers_pass(index, text):
    assert guard(index, QUOTE).check(text) == []


@pytest.mark.parametrize("text, bad", [
    ("It costs $1,160.70.", "1,160.70"),  # one cent off
    ("It costs about $1,161.", "1,161"),  # rounded by the LLM
    ("That is roughly 8,929 km.", "8,929"),
    ("That is 5,548 miles.", "5,548"),  # converted
    ("The first two legs add up to 3,234.5 km.", "3,234.5"),  # summed
    ("There are 5 legs.", "5"),  # miscounted
    ("The rate is $0.13 per km.", "0.13"),  # not in any record
    ("Valid for 15 minutes.", "15"),
    ("It costs 1160,71 dollars.", "1160"),  # mangled format
    ("Prices went up 12%.", "12"),
    ("Quote q_0000000000000000 is ready.", "q_0000000000000000"),  # an id no record has
])
def test_untraced_numbers_fail(index, text, bad):
    problems = guard(index, QUOTE).check(text)
    assert bad in [p["value"] for p in problems], problems


def test_a_number_the_user_wrote_is_allowed_only_in_that_turn(index):
    assert guard(index, QUOTE, user="my budget is 1,200 dollars").check("It is under your 1200 budget.") == []
    assert kinds(guard(index, QUOTE).check("It is under your 1200 budget.")) == [("number", "1200")]


def test_no_records_means_no_numbers(index):
    assert kinds(guard(index).check("A flight costs $500.")) == [("number", "500")]
    assert guard(index).check("Where would you like to fly from?") == []


def test_high_precision_values_are_not_in_what_the_llm_sees_but_would_trace(index):
    record = {"count": 2, "tickets": [{"price_display": "$366.55"}, {"price_display": "$1,144.79"}]}
    g = guard(index, record)
    assert g.check("You have 2 tickets: $366.55 and $1,144.79.") == []
    assert kinds(g.check("In total you spent $1,511.34.")) == [("number", "1,511.34")]


# --- dates and times ------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "The quote is valid until 2026-11-23T08:15:00Z.",
    "It is valid until 08:15 UTC on 2026-11-23.",
    "It is valid until 8:15 on November 23, 2026.",
    "Valid until 23 November 2026, 08:15.",
    "Valid until Nov 23rd at 08:15.",
    "Today is 2026-11-23.",
])
def test_dates_and_times_of_the_records_pass(index, text):
    assert guard(index, QUOTE).check(text) == []


@pytest.mark.parametrize("text, kind", [
    ("It is valid until 08:30.", "time"),
    ("It is valid until 2026-11-24.", "date"),
    ("Valid until November 24, 2026.", "date"),
    ("Valid until 23 November 2025.", "date"),
    ("You booked it on 3 March.", "date"),
])
def test_other_dates_and_times_fail(index, text, kind):
    assert [p["kind"] for p in guard(index, QUOTE).check(text)] == [kind]


def test_a_date_from_the_user_message_is_allowed(index):
    g = guard(index, {"count": 0, "tickets": []}, user="tickets booked after 2026-03-01, or on 5 May")
    assert g.check("You have 0 tickets booked after 2026-03-01 or on May 5.") == []
    assert [p["kind"] for p in g.check("Nothing after 2026-03-02.")] == ["date"]


# --- cities ---------------------------------------------------------------------------------
def test_cities_of_the_records_pass(index):
    g = guard(index, QUOTE)
    assert g.check("From Guangzhou via Frankfurt, Singapore and Amsterdam to Lima.") == []
    assert g.check("**Guangzhou** → **Lima**, Lima's airport, (Singapore).") == []


def test_a_db_city_from_general_knowledge_fails(index):
    """Design §4.5: the LLM suggests the Dubai it knows for Abu Dhabi; the records say Tirana."""
    g = guard(index, NO_SERVICE, user="I need a flight from Abu Dhabi to London")
    assert g.check("Abu Dhabi has no flights. The nearest served cities are Tirana and Odesa.") == []
    assert g.check("I can quote Tirana to London instead.") == []  # London: the user said it
    assert kinds(g.check("You could fly from Dubai, which is close to Abu Dhabi.")) == [("city", "Dubai")]


def test_cities_from_earlier_user_messages_are_allowed_but_their_numbers_are_not(index):
    g = ReplyGuard(index, records=[NO_SERVICE], user_text="which big cities are near Abu Dhabi?",
                   earlier_user_texts=["Abu Dhabi to London for under 900 dollars"])
    assert g.check("I can quote Tirana to London if you like.") == []
    assert kinds(g.check("That stays under 900 dollars from Dubai.")) == [("number", "900"), ("city", "Dubai")]


def test_city_names_are_found_whatever_the_case_accents_or_spacing(index):
    g = guard(index)
    for text in ("Try SÃO PAULO.", "Try Sao Paulo.", "Try São\nPaulo.", "Try sao  paulo.", "Try new york."):
        assert [p["kind"] for p in g.check(text)] == ["city"], text
    assert ReplyGuard(index, records=[{"city": "São Paulo"}]).check("Try Sao Paulo or SAO PAULO.") == []
    assert kinds(g.check("Go to Xi’an or Port‑au‑Prince.")) == [("city", "Xi’an"), ("city", "Port‑au‑Prince")]


def test_one_word_city_names_count_only_when_capitalised(index, net):
    assert {"Nice", "Split", "Cork"} <= set(net.cities)
    g = guard(index)
    assert g.check("That is a nice route, we can split it, no cork needed.") == []
    assert kinds(g.check("How about Nice instead?")) == [("city", "Nice")]


def test_san_jose_and_san_jose_with_accent_are_different_cities(index, net):
    assert {"San Jose", "San José"} <= set(net.cities)
    g = ReplyGuard(index, records=[{"city": "San José"}])
    assert g.check("The quote is for San José.") == []
    assert kinds(g.check("The quote is for San Jose.")) == [("city", "San Jose")]
    assert g.check("The quote is for SAN JOSE.") == []  # no exact name: either may be meant
    both = ReplyGuard(index, user_text="a ticket from san jose to Lima")
    assert both.check("Did you mean San Jose or San José? And Lima is the destination?") == []


def test_longest_name_wins_and_parts_of_words_do_not_match():
    index = CityIndex(["York", "New York", "Lima", "Paris"])
    g = ReplyGuard(index, records=[{"city": "New York"}])
    assert g.check("Fly to New York.") == []
    assert kinds(g.check("Fly to York.")) == [("city", "York")]
    assert ReplyGuard(index).check("Limassol and Yorkshire are elsewhere, as is Parisian flair.") == []


def test_a_city_split_over_two_released_pieces_is_still_one_mention(index, net):
    """The loop checks the whole text so far, so `St. | Louis` cannot slip through."""
    assert "St. Louis" in net.cities
    pieces = SentenceBuffer().push("Why not fly to St. Louis instead? ")
    assert pieces == ["Why not fly to St. ", "Louis instead? "]
    g = guard(index)
    assert g.check(pieces[0]) == []
    assert kinds(g.check(pieces[0] + pieces[1])) == [("city", "St. Louis")]


def test_fold_equals_the_core_fold_on_every_city(net):
    """guard.fold is a copy (the agent does not import core); they must not drift apart."""
    for name in net.cities:
        assert fold(name) == places.fold(name), name
    index = CityIndex(net.cities)
    assert index.by_key == {k: tuple(v) for k, v in net.fold_index.items()}
    for name in net.cities:  # every city finds itself, exactly
        assert [m.cities for m in index.mentions(f"to {name}.")] == [(name,)], name


def test_city_index_of_nothing_finds_nothing():
    assert CityIndex([]).mentions("Lima") == []


# --- sentence buffer ------------------------------------------------------------------------
def test_sentences_are_cut_after_punctuation_and_never_inside_a_number():
    buf = SentenceBuffer()
    assert buf.push("The price is $1,160.") == []  # could still become 1,160.71
    assert buf.push("71 for 8,928.6 km. Shall") == ["The price is $1,160.71 for 8,928.6 km. "]
    assert buf.push(" I book it?\nYes") == ["Shall I book it?\n"]
    assert buf.flush() == "Yes" and buf.flush() == ""


def test_sentence_pieces_concatenate_to_the_original_text():
    text = ("First. Second!  Third?\n\n- a list item\n- 1,234.5 km, $9.99.\nTrailing 中文句子。还有一句！"
            "St. Louis e.g. this... and the end")
    rng = random.Random(7)
    for _ in range(200):
        buf, out, pos = SentenceBuffer(), [], 0
        while pos < len(text):
            step = rng.randint(1, 9)
            out += buf.push(text[pos:pos + step])
            pos += step
        out.append(buf.flush())
        assert "".join(out) == text
        assert "Trailing 中文句子。" in out and "还有一句！" in out and "- a list item\n" in out


# --- templates always pass the guard --------------------------------------------------------
REAL_CALLS = [
    ("quote_route", {"origin": "Guangzhou", "destination": "Lima"}),
    ("quote_route", {"origin": "Addis Ababa", "destination": "Dubai"}),
    ("quote_route", {"origin": "Abu Dhabi", "destination": "London"}),
    ("quote_route", {"origin": "Abu Dhabi", "destination": "Stuttgart"}),
    ("quote_route", {"origin": "san jose", "destination": "Atlantis 2000"}),
    ("quote_route", {"origin": "Cairo", "destination": "Cairo"}),
    ("quote_route", {"origin": "new york", "destination": "St. Louis"}),
    ("find_city", {"name": "Stuttgart"}),
    ("find_city", {"name": "Lima"}),
    ("find_city", {"name": "san jose"}),
    ("find_city", {"name": "Xyzzy 42"}),
    ("list_my_tickets", {}),
    ("list_my_tickets", {"city": "Lima", "booked_after": "2026-01-01"}),
    ("list_my_tickets", {"booked_after": "last tuesday"}),
    ("request_booking", {"quote_id": "q_0000000000000000"}),
    ("no_such_tool", {}),
]


def test_every_template_reply_passes_the_guard_built_from_the_same_results(tmp_path, index):
    import json

    world = World(tmp_path, real=True)
    tools = Toolbox(world.airline)
    outcomes = [tools.run(world.alice, world.conv, name, json.dumps(args)) for name, args in REAL_CALLS]
    # a booked ticket, the confirm card of a fresh quote, and the already-booked answer
    quote_id, nonce = world.presented("Madrid", "Cairo")
    world.airline.book(world.alice, quote_id, nonce)
    fresh = tools.run(world.alice, world.conv, "quote_route", '{"origin": "Paris", "destination": "Tokyo"}')
    for name, args in [("request_booking", {"quote_id": fresh.payload["quote_id"]}),
                       ("request_booking", {"quote_id": quote_id}),
                       ("list_my_tickets", {}), ("list_my_tickets", {"city": "cairo"})]:
        outcomes.append(tools.run(world.alice, world.conv, name, json.dumps(args)))
    world.clock.advance(minutes=16)
    outcomes.append(tools.run(world.alice, world.conv, "request_booking",
                              json.dumps({"quote_id": fresh.payload["quote_id"]})))
    assert {o.code for o in outcomes} >= {
        "ok", "no_service", "ambiguous_city", "unknown_city", "same_city", "bad_request", "not_found",
        "already_booked", "quote_expired", "unknown_tool"}

    texts = set()
    rng = random.Random(3)
    groups = [[o] for o in outcomes] + [rng.sample(outcomes, rng.randint(2, 4)) for _ in range(60)]
    for group in groups:
        text = fallback_reply(group)
        g = ReplyGuard(index, records=[o.payload for o in group], today=date(2026, 11, 23))
        assert g.check(text) == [], text
        texts.add(text)
    assert fallback_reply([]) == NO_ANSWER and guard(index).check(NO_ANSWER) == []
    assert len(texts) > 40
    # the texts carry the facts: every quoted price and every nearest city is in its template
    for o in outcomes:
        text = fallback_reply([o])
        if o.code == "ok" and "price_display" in o.payload:
            assert o.payload["price_display"] in text and o.payload["distance_display"] in text
            assert all(city in text for city in o.payload["transfers"])
        for issue in o.payload.get("issues", []):
            assert all(n["city"] in text for n in issue.get("nearest", []))


# --- round 02: adversarial table (review of PR #14) -----------------------------------------
TICKETS = {"code": "ok", "count": 1, "filter": {}, "tickets": [{"id": "t_0123456789abcdef", "origin": "Madrid",
           "destination": "Cairo", "distance_display": "3,350.4 km", "price_display": "$435.55",
           "booked_at": "2026-11-23T08:05:00Z"}]}
BOOKING_EVENT = {"event": "ticket_booked_by_user_click", "ticket": TICKETS["tickets"][0]}


@pytest.mark.parametrize("text", [
    # numbers glued to letters or markdown (review #1)
    "Flight OA1234 is on the card.", "It is RMB6800.", "About _950 USD_.", "Roughly _8,000 km_.",
    "Booking ref TK99812.", "Gate B12.", "Your ticket t_xyz123 is ready.", "Ticket T_9F3A2B.",
    # cities wrapped in underscores (review #2)
    "Fly via _Dubai_.", "Fly via __Dubai__.",
    # line-start numbers that are not a 1, 2, 3 list (review #5)
    "The number of stops is\n7. The price in dollars is\n99) roughly.",
    # right value, wrong unit or a unit the records never have (review #6)
    "It costs $8,928.6.", "That is 1,160.71 km.", "8,928.6 miles.", "It takes about 4 hours.",
    "Roughly 1 thousand dollars.", "About 1k km.", "It leaves at 3 PM.", "Up 4%.", "大约4小时。",
    # list lengths are no longer numbers (review #6)
    "There are 0 problems and 2 nearest cities.",
    # booking claims without a booking (review #4)
    "Done! Your ticket from Guangzhou to Lima is booked and confirmed.", "I've booked it for you.",
    "Your booking is confirmed.", "You're all booked.", "Your flight has been successfully booked.",
    "已为您出票。", "预订成功！", "机票订好了。", "The Lima ticket has been issued.",
])
def test_adversarial_replies_are_caught(index, text):
    assert guard(index, QUOTE, NO_SERVICE).check(text), text


# Booking claims without a booking in the records, in the shapes the first rule missed (no noun
# next to the verb, "it", contractions, fragments, other tenses and languages).
BOOKING_CLAIMS = [
    "Great, it has been booked and confirmed.", "It has been booked.", "It's been booked.", "It's booked!",
    "That is now confirmed.", "Everything is now confirmed.", "Your trip has been confirmed.",
    "It was booked successfully.", "They have been booked.", "The ticket's booked.", "It got booked.",
    "Good news: it is confirmed.", "IT HAS BEEN BOOKED.", "it has been BOOKED", "It\u2019s been booked.",
    "You're all set, booked and ready to go.", "You are all set!", "You\u2019re all set.",
    "Booked! Have a nice trip.", "Ticket booked!", "Booking confirmed!", "Reservation confirmed.",
    "I booked it.", "I have just booked it for you.", "We've confirmed your seat.", "I've issued the ticket.",
    "Your seat is reserved.", "The ticket has been purchased.", "It is already booked.",
    "The Lima flight is booked. Safe travels!", "Done: booked.",
    "已为您预订。", "已经为您预订成功。", "订好了！", "您的机票已订好。", "机票已出票。", "已成功预订。",
    "预订已确认。", "已确认预订。", "订单已生成，已出票。", "您已订票成功。", "票已订好了，祝旅途愉快。",
]


@pytest.mark.parametrize("text", BOOKING_CLAIMS)
def test_booking_claims_without_a_booking_are_caught(index, text):
    assert "booking_claim" in [p["kind"] for p in guard(index, QUOTE, NO_SERVICE).check(text)], text
    assert "booking_claim" not in [p["kind"] for p in guard(index, QUOTE, BOOKING_EVENT).check(text)], text


NOT_CLAIMS = [
    "Nothing is booked until you click **Confirm booking** on the card.", "Nothing has been booked yet.",
    "Nothing is booked.", "NOTHING IS BOOKED YET.", "It is not booked yet.", "It has not been booked.",
    "It hasn't been booked.", "It isn't booked.", "That isn't confirmed until you click.",
    "Your ticket is not booked.", "No ticket has been booked.", "You haven't booked anything yet.",
    "It is not confirmed.", "Not booked yet: please click Confirm booking.",
    "The ticket is only issued once you click.", "It will be booked once you click Confirm.",
    "It can be booked when you click the button.", "Once it is booked you will see a ticket card.",
    "If it is booked, the ticket appears under your tickets.", "Until you click, it is not booked.",
    "Shall I book it?", "Do you want me to book it for you?", "Please confirm the booking on the card.",
    "Click Confirm booking to book it.", "Is it booked now? No, not yet.", "I can prepare the booking for you.",
    "The route is direct.", "Your quote is ready.", "Tirana is the nearest served city.",
    "尚未出票，请点击确认。", "还没有订好，请先点击确认出票。", "没有订票成功。", "未预订。",
    "点击确认后才会出票。", "请确认预订。", "要我帮您预订吗？",
]


@pytest.mark.parametrize("text", NOT_CLAIMS)
def test_denials_conditions_and_questions_are_not_booking_claims(index, text):
    assert "booking_claim" not in [p["kind"] for p in guard(index, QUOTE).check(text)], text


def test_the_click_hint_the_code_appends_is_never_a_claim(index):
    from app.agent.templates import CLICK_TO_BOOK
    assert guard(index, QUOTE).check(CLICK_TO_BOOK) == []
    assert guard(index, NO_SERVICE).check("Here is the card. " + CLICK_TO_BOOK) == []


@pytest.mark.parametrize("text", [
    "It costs $1,160.71 for 8,928.6 km.", "It costs 1,160.71 USD.", "价格 $1,160.71，距离 8,928.6 公里。",
    "4 legs, 3 transfers.", "1. Frankfurt\n2. Singapore\n3. Amsterdam",
    "Nothing is booked until you click **Confirm booking** on the card.",
    "The ticket is only issued once you click.", "Your ticket is issued only after you click Confirm booking.",
    "Please check the card; you haven't booked anything yet.", "只有你点击后机票才会出票。",
])
def test_honest_replies_still_pass(index, text):
    assert guard(index, QUOTE).check(text) == [], text


def test_booking_claims_pass_with_a_booking_in_the_records(index):
    claim = "Your Madrid to Cairo ticket is confirmed: $435.55, booked 2026-11-23T08:05:00Z."
    assert [p["kind"] for p in guard(index, QUOTE).check(claim)].count("booking_claim") == 1
    assert guard(index, BOOKING_EVENT).check(claim) == []
    assert guard(index, TICKETS).check(claim) == []
    empty = {"code": "ok", "count": 0, "filter": {}, "tickets": []}
    assert kinds(guard(index, empty).check("Your ticket is booked.")) == [("booking_claim", "is booked")]


def test_values_echoed_from_the_llms_own_arguments_are_not_records(index):
    """Review #3: `query`, `filter`, messages and refused calls repeat what the LLM wrote."""
    echoed = [
        {"code": "unknown_city", "query": "roughly 4500 km, $585.00", "suggestions": []},
        {"code": "ok", "count": 0, "filter": {"booked_after": "2026-11-16"}, "tickets": []},
        {"code": "not_found", "quote_id": "q_00000000deadbeef"},
        {"code": "invalid_arguments", "message": "origin 1234 is too long"},
        {"code": "bad_request", "message": "not an ISO date: '2026-11-17'"},
    ]
    g = guard(index, *echoed)
    for text in ("It is roughly 4500 km and costs $585.00.", "Since 2026-11-16 you booked nothing.",
                 "Quote q_00000000deadbeef is gone.", "Origin 1234.", "Since 2026-11-17."):
        assert g.check(text), text
    assert g.check("You have 0 tickets.") == []  # `count` is the code's


def test_user_lowercase_words_do_not_allow_one_word_cities(index):
    g = ReplyGuard(index, user_text="any nice tips for a split fare?")
    assert {p["value"] for p in g.check("Fly via Nice or Split.")} == {"Nice", "Split"}
