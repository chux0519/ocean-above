"""Airline tools and read views: results, cards exactly as the contract, ownership."""
import json

import pytest

from app.airline import Airline, NotFound
from app.core.network import NetworkDataError
from app.core.pricing import FORMULA
from app.core.routing import ALGORITHM

from .airline_helpers import FIXTURES, World, assert_card, assert_message, assert_shape, fixture_schemas

Z = "0" * 30
SCHEMAS = fixture_schemas()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return World(tmp_path_factory.mktemp("real"), real=True)


def no_nonce_anywhere(result):
    text = json.dumps([result.data, result.cards])
    assert '"nonce": "' not in text and "n_" not in text.replace("in_", "")


# --- the fixtures themselves -------------------------------------------------------------------
def test_every_card_type_of_the_contract_has_a_fixture_example():
    assert {"quote", "booking_request", "ticket", "ticket_list", "no_service", "ambiguous_city",
            "unknown_city", "notice", "message", "trace", "booking_ok", "error"} <= set(SCHEMAS)


def test_shape_checker_rejects_missing_extra_and_mistyped_fields():
    card = json.loads((FIXTURES / "tickets.json").read_text(encoding="utf-8"))["tickets"][0]
    assert_card(card, SCHEMAS)
    for broken in (
        {k: v for k, v in card.items() if k != "booked_at"},
        {**card, "extra": 1},
        {**card, "price_display": 1160.71},
        {**card, "legs": [{**card["legs"][0], "distance": 1362.1}]},
        {**card, "transfers": [1]},
    ):
        with pytest.raises(AssertionError):
            assert_card(broken, SCHEMAS)


# --- find_city ---------------------------------------------------------------------------------
def test_find_city_served_city_has_no_card(world):
    r = world.airline.find_city("  c ")
    assert r.ok and r.cards == ()
    assert r.data == {"city": "C", "served": True, "is_hub": False, "connected_hubs": ["H3", "H4"],
                      "similar_names": []}
    hub = world.airline.find_city("H1")
    assert hub.data["is_hub"] is True and hub.data["connected_hubs"] == ["H2", "H3"]


def test_find_city_without_service_lists_the_nearest_served_cities(world):
    r = world.airline.find_city("U")
    assert r.code == "no_service" and not r.ok
    assert r.cards == ({"type": "no_service", "city": "U", "nearest": [
        {"city": "H1", "distance_display": "7.1 km"},
        {"city": "A", "distance_display": "15.8 km"},
        {"city": "B", "distance_display": "15.8 km"},
    ]},)
    assert r.data["nearest"] == r.cards[0]["nearest"] and r.data["served"] is False
    assert_card(r.cards[0], SCHEMAS)


def test_find_city_unknown(world):
    r = world.airline.find_city("Atlantis")
    assert r.code == "unknown_city"
    assert r.cards == ({"type": "unknown_city", "query": "Atlantis", "suggestions": []},)
    assert_card(r.cards[0], SCHEMAS)


def test_find_city_on_the_real_db_matches_the_contract_examples(real):
    abu = real.airline.find_city("Abu Dhabi")
    fixture = json.loads((FIXTURES / "conversation.json").read_text(encoding="utf-8"))
    cards = {c["type"]: c for m in fixture["messages"] for c in m["cards"]}
    assert abu.cards[0] == cards["no_service"]  # Tirana, Odesa, Whitehorse with the same distances

    ambiguous = real.airline.find_city("san jose")  # folded: two cities, never auto-picked
    assert ambiguous.code == "ambiguous_city"
    assert ambiguous.cards[0] == {**cards["ambiguous_city"], "query": "san jose"}
    exact = real.airline.find_city("San José")
    assert exact.ok and exact.data["similar_names"] == ["San Jose"]

    unknown = real.airline.find_city("Atlantis")
    assert unknown.code == "unknown_city" and unknown.cards[0]["suggestions"][0] == "Atlanta"
    for r in (abu, ambiguous, unknown):
        assert_card(r.cards[0], SCHEMAS)


# --- quote_route -------------------------------------------------------------------------------
def test_quote_route_stores_a_quoted_quote_and_returns_its_card(world):
    r = world.airline.quote_route(world.alice, world.conv, "a", "D")
    assert r.ok
    card = r.cards[0]
    assert_card(card, SCHEMAS)
    assert card == {
        "type": "quote", "id": r.data["quote_id"], "origin": "A", "destination": "D", "similar_names": [],
        "legs": [
            {"from": "A", "to": "H1", "distance_display": "10.0 km", "distance": "10." + Z},
            {"from": "H1", "to": "H3", "distance_display": "50.0 km", "distance": "50." + Z},
            {"from": "H3", "to": "H4", "distance_display": "60.0 km", "distance": "60." + Z},
            {"from": "H4", "to": "D", "distance_display": "10.0 km", "distance": "10." + Z},
        ],
        "transfers": ["H1", "H3", "H4"],
        "distance_display": "130.0 km", "price_display": "$16.90",
        "distance": "130." + Z, "price": "16.9" + "0" * 29,
        "expires_at": "2026-11-23T08:15:00Z",
        "state": {"status": "quoted", "bookable": False, "nonce": None, "ticket_id": None},
    }
    assert r.data == {
        "quote_id": card["id"], "origin": "A", "destination": "D",
        "legs": [{"from": "A", "to": "H1", "distance_display": "10.0 km"},
                 {"from": "H1", "to": "H3", "distance_display": "50.0 km"},
                 {"from": "H3", "to": "H4", "distance_display": "60.0 km"},
                 {"from": "H4", "to": "D", "distance_display": "10.0 km"}],
        "transfers": ["H1", "H3", "H4"], "direct": False,
        "distance_display": "130.0 km", "price_display": "$16.90",
        "expires_at": "2026-11-23T08:15:00Z", "status": "quoted",
    }
    no_nonce_anywhere(r)
    row = world.sql("select user_id, conversation_id, status, network_fingerprint, created_at from quotes")
    assert row == [(world.alice, world.conv, "quoted", world.networks.current().fingerprint.id,
                    "2026-11-23T08:00:00Z")]


def test_quote_validity_starts_at_the_floored_creation_second(world):
    world.clock.advance(seconds=0.9)
    r = world.quote()
    assert r.cards[0]["expires_at"] == "2026-11-23T08:15:00Z"


def test_direct_flight_quote(world):
    r = world.quote("H1", "H3")
    assert r.data["direct"] is True and r.data["transfers"] == [] and r.cards[0]["transfers"] == []
    assert_card(r.cards[0], SCHEMAS)


@pytest.mark.parametrize(
    "origin, destination, code, card_types",
    [
        ("Nowhere", "D", "unknown_city", ["unknown_city"]),
        ("Nowhere", "Elsewhere", "unknown_city", ["unknown_city", "unknown_city"]),
        ("A", "a", "same_city", ["notice"]),
        ("U", "D", "no_service", ["no_service"]),
        ("A", "E", "unreachable", ["notice"]),
    ],
)
def test_quote_route_errors_store_nothing_and_have_cards(world, origin, destination, code, card_types):
    r = world.airline.quote_route(world.alice, world.conv, origin, destination)
    assert r.code == code and not r.ok
    assert [c["type"] for c in r.cards] == card_types
    for card in r.cards:
        assert_card(card, SCHEMAS)
    assert world.sql("select count(*) from quotes") == [(0,)]


def test_quote_route_error_details(world):
    same = world.airline.quote_route(world.alice, world.conv, "A", "A")
    assert same.cards[0] == {"type": "notice", "code": "same_city",
                             "message": "Origin and destination are the same city."}
    island = world.airline.quote_route(world.alice, world.conv, "A", "E")
    assert island.cards[0] == {"type": "notice", "code": "unreachable",
                               "message": "There is no route between A and E."}
    unserved = world.airline.quote_route(world.alice, world.conv, "U", "H1")
    # the other endpoint (H1, the nearest of all) is not suggested
    assert [n["city"] for n in unserved.cards[0]["nearest"]] == ["A", "B", "H2"]
    assert unserved.data["issues"][0] == {"role": "origin", "code": "no_service", "query": "U", "city": "U",
                                          "nearest": unserved.cards[0]["nearest"]}


def test_ambiguous_city_in_a_quote_on_the_real_db(real):
    r = real.airline.quote_route(real.alice, real.conv, "san jose", "Lima")
    assert r.code == "ambiguous_city" and r.cards[0]["type"] == "ambiguous_city"
    assert [c["city"] for c in r.cards[0]["candidates"]] == ["San Jose", "San José"]
    exact = real.airline.quote_route(real.alice, real.conv, "San Jose", "Lima")
    assert exact.ok and exact.data["similar_names"] == {"San Jose": ["San José"]}


def test_the_contract_example_route_on_the_real_db(real):
    """Guangzhou -> Lima: same route and display values as the contract fixtures."""
    r = real.airline.quote_route(real.alice, real.conv, "Guangzhou", "Lima")
    fixture = json.loads((FIXTURES / "conversation.json").read_text(encoding="utf-8"))["messages"][1]["cards"][0]
    card = r.cards[0]
    for key in ("type", "origin", "destination", "transfers", "distance_display", "price_display"):
        assert card[key] == fixture[key], key
    assert [(l["from"], l["to"], l["distance_display"]) for l in card["legs"]] == \
           [(l["from"], l["to"], l["distance_display"]) for l in fixture["legs"]]

    trace = real.airline.trace(real.alice, card["id"])
    assert_shape(SCHEMAS["trace"], trace, "trace")
    example = json.loads((FIXTURES / "trace.json").read_text(encoding="utf-8"))
    for mine, theirs in zip(trace["legs"], example["legs"], strict=True):
        for key in ("from", "to", "distance_display"):
            assert mine[key] == theirs[key], key
    # sources follow the database: Guangzhou is a spoke of Frankfurt, Lima is itself a hub
    assert [l["source"] for l in trace["legs"]] == [
        "locations.connected_hubs(Guangzhou)", "hub_links(Frankfurt, Singapore)",
        "hub_links(Singapore, Amsterdam)", "hub_links(Amsterdam, Lima)"]
    assert trace["legs"][0]["from_coord"] == [-9278, 3445]
    assert trace["note"] == example["note"]


def test_quote_route_checks_the_conversation_owner(world):
    for origin, destination in (("A", "D"), ("A", "A")):  # with and without a route
        with pytest.raises(NotFound):
            world.airline.quote_route(world.bob, world.conv, origin, destination)
        with pytest.raises(NotFound):
            world.airline.quote_route(world.alice, "c_missing", origin, destination)
    assert world.sql("select count(*) from quotes") == [(0,)]


def test_tools_raise_when_locations_db_is_broken(world):
    world.edit_db("update locations set coordinate = 'oops' where city_name = 'A'")
    with pytest.raises(NetworkDataError):
        world.airline.quote_route(world.alice, world.conv, "A", "D")
    with pytest.raises(NetworkDataError):
        world.airline.find_city("A")


# --- trace -------------------------------------------------------------------------------------
def test_trace_shows_sources_coordinates_and_the_core_formula(world):
    quote_id = world.quote("A", "C").data["quote_id"]
    trace = world.airline.trace(world.alice, quote_id)
    assert_shape(SCHEMAS["trace"], trace, "trace")
    assert trace["formula"] == FORMULA and trace["algorithm"] == ALGORITHM
    assert trace["network_fingerprint"] == world.networks.current().fingerprint.id
    assert [(l["from"], l["to"], l["from_coord"], l["to_coord"], l["source"]) for l in trace["legs"]] == [
        ("A", "H1", [0, -10], [0, 0], "locations.connected_hubs(A)"),
        ("H1", "H3", [0, 0], [30, 40], "hub_links(H1, H3)"),
        ("H3", "C", [30, 40], [60, 40], "locations.connected_hubs(C)"),
    ]
    assert trace["distance"] == "90." + Z and trace["price_display"] == "$11.70"


def test_trace_is_only_for_the_owner(world):
    quote_id = world.quote().data["quote_id"]
    with pytest.raises(NotFound):
        world.airline.trace(world.bob, quote_id)
    with pytest.raises(NotFound):
        world.airline.trace(world.alice, "q_missing")
    assert world.airline.get_quote(world.bob, quote_id) is None
    assert world.airline.get_quote(world.alice, quote_id)["state"]["status"] == "quoted"


def test_fractional_coordinates_are_numbers_in_the_trace(world):
    world.edit_db("update locations set coordinate = '0.5,-10' where city_name = 'A'")
    trace = world.airline.trace(world.alice, world.quote("A", "H1").data["quote_id"])
    assert trace["legs"][0]["from_coord"] == [0.5, -10]


# --- request_booking ---------------------------------------------------------------------------
def test_request_booking_returns_a_confirm_card_without_a_nonce(world):
    quote_id, _ = world.presented()
    r = world.airline.request_booking(world.alice, world.conv, quote_id)
    assert r.ok and r.data["quote_id"] == quote_id and r.data["status"] == "presented"
    card = r.cards[0]
    assert_card(card, SCHEMAS)
    assert card["type"] == "booking_request" and card["id"] == quote_id
    assert card["state"] == {"status": "presented", "bookable": False, "nonce": None, "ticket_id": None}
    quote_card = world.airline.get_quote(world.alice, quote_id)
    assert {k: v for k, v in card.items() if k not in ("type", "state")} == \
           {k: v for k, v in quote_card.items() if k not in ("type", "state")}
    no_nonce_anywhere(r)
    assert world.ticket_count() == 0 and world.status(quote_id) == "presented"


def test_request_booking_in_the_same_turn_as_the_quote(world):
    quote = world.quote()
    r = world.airline.request_booking(world.alice, world.conv, quote.data["quote_id"])
    assert r.ok and r.cards[0]["state"]["status"] == "quoted"
    message = world.done(quote.cards + r.cards)
    states = [c["state"] for c in message["cards"]]
    assert [c["type"] for c in message["cards"]] == ["quote", "booking_request"]
    assert states[0] == states[1] and states[0]["bookable"]  # one message, one nonce
    assert world.sql("select count(*) from quote_cards") == [(1,)]


def test_request_booking_refusals(world):
    quote_id, nonce = world.presented()
    other = world.store.create_conversation(world.alice)["id"]
    for user, conv, qid in ((world.bob, world.bob_conv, quote_id), (world.bob, world.conv, quote_id),
                            (world.alice, other, quote_id), (world.alice, world.conv, "q_missing")):
        r = world.airline.request_booking(user, conv, qid)
        assert r.code == "not_found" and r.cards == () and r.data == {"quote_id": qid}

    world.airline.book(world.alice, quote_id, nonce)
    booked = world.airline.request_booking(world.alice, world.conv, quote_id)
    assert booked.code == "already_booked" and booked.cards[0]["type"] == "ticket"
    assert booked.data["ticket_id"] == booked.cards[0]["id"]
    assert_card(booked.cards[0], SCHEMAS)
    assert world.ticket_count() == 1


def test_request_booking_on_an_expired_quote_gives_a_notice_and_closes_it(world):
    quote_id, _ = world.presented()
    world.clock.advance(minutes=15)
    r = world.airline.request_booking(world.alice, world.conv, quote_id)
    assert r.code == "quote_expired"
    assert r.cards == ({"type": "notice", "code": "quote_expired",
                        "message": "This quote has expired. Please ask for a new quote."},)
    assert_card(r.cards[0], SCHEMAS)
    assert world.status(quote_id) == "expired"


# --- post_assistant_message / messages ---------------------------------------------------------
def test_done_presents_the_quote_and_only_then_gives_a_nonce(world):
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    assert world.status(quote_id) == "quoted"
    world.clock.advance(seconds=4)
    message = world.done(quote.cards, text="The cheapest route is below.")
    assert_message(message, SCHEMAS)
    assert message["role"] == "assistant" and message["created_at"] == "2026-11-23T08:00:04Z"
    card = message["cards"][0]
    assert card["state"]["status"] == "presented" and card["state"]["bookable"] is True
    assert card["state"]["nonce"].startswith("n_") and card["state"]["ticket_id"] is None
    assert {k: v for k, v in card.items() if k != "state"} == \
           {k: v for k, v in quote.cards[0].items() if k != "state"}
    assert world.sql("select status, presented_at from quotes") == [("presented", "2026-11-23T08:00:04Z")]
    # the nonce is not in app.db
    dump = json.dumps(world.sql("select * from messages") + world.sql("select * from quote_cards")
                      + world.sql("select * from quotes"))
    assert card["state"]["nonce"] not in dump


def test_messages_view_reflects_the_current_server_state(world):
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    first = world.done(quote.cards)
    second = world.done(world.airline.request_booking(world.alice, world.conv, quote_id).cards)
    n1, n2 = first["cards"][0]["state"]["nonce"], second["cards"][0]["state"]["nonce"]
    assert n1 != n2  # bound to the message the card is in

    view = world.airline.messages(world.alice, world.conv)
    assert [m["id"] for m in view] == [first["id"], second["id"]]
    assert [m["cards"][0]["state"]["nonce"] for m in view] == [n1, n2]  # stable across reloads
    for m in view:
        assert_message(m, SCHEMAS)

    out = world.airline.book(world.alice, quote_id, n2)
    view = world.airline.messages(world.alice, world.conv)
    booked = {"status": "booked", "bookable": False, "nonce": None, "ticket_id": out["ticket"]["id"]}
    assert [m["cards"][0]["state"] for m in view[:2]] == [booked, booked]
    assert view[2] == out["message"] and view[2]["role"] == "system"
    for m in view:
        assert_message(m, SCHEMAS)


def test_quote_cards_are_rendered_from_the_quote_not_from_what_was_posted(world):
    quote = world.quote()
    forged = {**quote.cards[0], "price_display": "$0.01", "price": "0.01", "destination": "Paris",
              "state": {"status": "presented", "bookable": True, "nonce": "n_forged", "ticket_id": None}}
    message = world.done([forged])
    assert message["cards"][0]["price_display"] == "$16.90" and message["cards"][0]["destination"] == "D"
    assert message["cards"][0]["state"]["nonce"] != "n_forged"
    assert world.sql("select cards from messages") == [(json.dumps(
        [{"type": "quote", "id": quote.data["quote_id"]}], separators=(",", ":")),)]


def test_other_cards_and_guard_problems_are_stored_as_given(world):
    notice = world.airline.quote_route(world.alice, world.conv, "A", "A").cards
    message = world.airline.post_assistant_message(world.alice, world.conv, "Same city.", notice,
                                                   guard_problems=[{"kind": "number", "value": "42"}])
    assert message["cards"] == list(notice) and "guard_problems" not in message
    assert world.store.list_messages(world.alice, world.conv)[0]["guard_problems"] == [{"kind": "number", "value": "42"}]


def test_done_refuses_foreign_conversations_and_foreign_quotes(world):
    quote = world.quote()
    with pytest.raises(NotFound):
        world.airline.post_assistant_message(world.bob, world.conv, "x", quote.cards)
    with pytest.raises(ValueError):  # Alice's quote card in Bob's conversation
        world.airline.post_assistant_message(world.bob, world.bob_conv, "x", quote.cards)
    with pytest.raises(ValueError):
        world.done([{"type": "quote", "id": "q_missing"}])
    assert world.sql("select count(*) from messages") == [(0,)]  # rolled back with the message
    assert world.status(quote.data["quote_id"]) == "quoted"
    with pytest.raises(NotFound):
        world.airline.messages(world.bob, world.conv)


def test_done_after_expiry_does_not_present(world):
    quote = world.quote()
    world.clock.advance(minutes=15)
    message = world.done(quote.cards)
    assert message["cards"][0]["state"] == {"status": "expired", "bookable": False, "nonce": None,
                                            "ticket_id": None}
    assert world.status(quote.data["quote_id"]) == "expired"
    assert world.sql("select count(*) from quote_cards") == [(0,)]


def test_the_clock_is_read_after_the_write_lock_was_obtained(world, monkeypatch):
    """Review round 01, finding 5: waiting for the write lock must not let a quote be presented
    (or confirmed by request_booking) with a time from before the wait."""
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    real_write = world.store.write

    def slow_write():
        world.clock.advance(minutes=15)  # the time spent waiting for BEGIN IMMEDIATE
        return real_write()

    monkeypatch.setattr(world.store, "write", slow_write)
    message = world.done(quote.cards)
    assert message["cards"][0]["state"] == {"status": "expired", "bookable": False, "nonce": None,
                                            "ticket_id": None}
    assert world.status(quote_id) == "expired"
    monkeypatch.undo()

    second = world.quote().data["quote_id"]
    monkeypatch.setattr(world.store, "write", slow_write)
    assert world.airline.request_booking(world.alice, world.conv, second).code == "quote_expired"


def test_nonce_secret_must_be_bytes_of_a_useful_length(world):
    for bad in ("a string from the environment", b"short", b"", 12345):
        with pytest.raises(ValueError, match="nonce_secret"):
            Airline(world.store, world.networks, nonce_secret=bad)
    Airline(world.store, world.networks, nonce_secret=b"0123456789abcdef")


def test_a_presented_quote_shows_as_expired_on_reload_without_being_touched(world):
    quote_id, _ = world.presented()
    world.clock.advance(minutes=14, seconds=59)
    assert world.airline.messages(world.alice, world.conv)[0]["cards"][0]["state"]["bookable"] is True
    world.clock.advance(seconds=1)
    state = world.airline.messages(world.alice, world.conv)[0]["cards"][0]["state"]
    assert state == {"status": "expired", "bookable": False, "nonce": None, "ticket_id": None}
    assert world.airline.get_quote(world.alice, quote_id)["state"]["status"] == "expired"


# --- tickets -----------------------------------------------------------------------------------
def booked(world, origin, destination, user=None, conv=None):
    quote_id, nonce = world.presented(origin, destination, user, conv)
    return world.airline.book(user or world.alice, quote_id, nonce)["ticket"]


def test_tickets_newest_first_and_only_mine(world):
    t1 = booked(world, "A", "D")
    world.clock.advance(minutes=1)
    t2 = booked(world, "B", "C")
    booked(world, "A", "B", world.bob, world.bob_conv)
    mine = world.airline.tickets(world.alice)
    assert [t["id"] for t in mine] == [t2["id"], t1["id"]]
    for card in mine:
        assert_card(card, SCHEMAS)
    assert mine[1] == t1 and t1["booked_at"] == "2026-11-23T08:00:00Z"
    assert len(world.airline.tickets(world.bob)) == 1


def test_list_tickets_counts_and_filters(world):
    t1 = booked(world, "A", "D")  # A H1 H3 H4 D
    world.clock.advance(minutes=60)
    t2 = booked(world, "B", "C")  # B H1 H3 C
    booked(world, "A", "B", world.bob, world.bob_conv)

    everything = world.airline.list_tickets(world.alice)
    assert everything.ok and everything.cards[0]["type"] == "ticket_list"
    assert_card(everything.cards[0], SCHEMAS)
    assert everything.data == {"count": 2, "filter": {}, "tickets": [t2, t1]}

    def ids(**filter_):
        r = world.airline.list_tickets(world.alice, **filter_)
        assert r.ok and r.cards[0]["count"] == len(r.cards[0]["tickets"]) == r.data["count"]
        assert r.cards[0]["filter"] == {k: str(v) for k, v in filter_.items()}
        assert_card(r.cards[0], SCHEMAS)
        return [t["id"] for t in r.cards[0]["tickets"]]

    assert ids(city="d") == [t1["id"]]  # destination, case-insensitive
    assert ids(city="H3") == [t2["id"], t1["id"]]  # a transfer city
    assert ids(city="Paris") == []
    assert ids(ticket_id=t1["id"]) == [t1["id"]]
    assert ids(ticket_id="t_missing") == []
    assert ids(booked_after="2026-11-23T09:00:00Z") == [t2["id"]]  # inclusive
    assert ids(booked_before="2026-11-23T09:00:00Z") == [t1["id"]]  # exclusive
    assert ids(booked_after="2026-11-23", booked_before="2026-11-24") == [t2["id"], t1["id"]]
    assert ids(booked_after="2026-11-24") == []
    assert ids(booked_after="2026-11-23T16:30:00+08:00") == [t2["id"]]
    assert ids(city="h1", booked_before="2026-11-23T08:30:00") == [t1["id"]]


def test_list_tickets_rejects_a_bad_time(world):
    r = world.airline.list_tickets(world.alice, booked_after="last week")
    assert r.code == "bad_request" and r.cards == () and "last week" in r.data["message"]


def test_list_tickets_when_there_are_none(world):
    r = world.airline.list_tickets(world.alice)
    assert r.data == {"count": 0, "filter": {}, "tickets": []} and world.airline.tickets(world.alice) == []


def test_similar_names_on_quote_and_booking_request_cards(real):
    """Tokyo -> San Jose / San José list each other (with hubs); the same on every read; plain routes []."""
    net = real.networks.current()
    for dest, other in (("San Jose", "San José"), ("San José", "San Jose")):
        r = real.airline.quote_route(real.alice, real.conv, "Tokyo", dest)
        assert r.ok
        expected = [{"city": other, "connected_hubs": list(net.cities[other].connected_hubs)}]
        assert expected[0]["connected_hubs"]
        card = r.cards[0]
        assert card["similar_names"] == expected
        assert_card(card, SCHEMAS)
        qid = r.data["quote_id"]
        booking = real.airline.request_booking(real.alice, real.conv, qid)
        assert booking.cards[0]["type"] == "booking_request" and booking.cards[0]["similar_names"] == expected
        assert real.airline.get_quote(real.alice, qid)["similar_names"] == expected
        message = real.done(booking.cards)
        assert message["cards"][0]["similar_names"] == expected
        assert_message(message, SCHEMAS)
    plain = real.airline.quote_route(real.alice, real.conv, "Guangzhou", "Lima")
    assert plain.cards[0]["similar_names"] == []
