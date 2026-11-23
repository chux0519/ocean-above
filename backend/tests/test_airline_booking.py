"""Airline.book: one test per refusal code, the freshness re-check (B6), atomicity, concurrency.
Random event sequences over the same rules are in test_booking_model.py."""
import asyncio
import json
import os
import sqlite3
import threading

import pytest

from app.airline import Airline, BookingError, ConversationBusy, ConversationLocks  # noqa: F401
from app.core.network import NetworkProvider
from app.store import records

from .airline_helpers import (FIXTURES, SECRET, World, assert_card, assert_message, assert_shape,
                              fixture_schemas)

SCHEMAS = fixture_schemas()
Z = "0" * 30


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def refused(world, user, quote_id, nonce) -> BookingError:
    with pytest.raises(BookingError) as info:
        world.airline.book(user, quote_id, nonce)
    return info.value


# --- success -----------------------------------------------------------------------------------
def test_click_with_the_card_nonce_books_exactly_as_quoted(world):
    quote = world.quote("A", "D")
    quote_id = quote.data["quote_id"]
    message = world.done(quote.cards)
    world.clock.advance(minutes=3, seconds=12)
    out = world.airline.book(world.alice, quote_id, message["cards"][0]["state"]["nonce"])

    assert_shape(SCHEMAS["booking_ok"], {k: None for k in out}, "booking_ok")
    assert_card(out["ticket"], SCHEMAS)
    assert_message(out["message"], SCHEMAS)
    card = quote.cards[0]
    assert out["ticket"] == {
        "type": "ticket", "id": out["ticket"]["id"], "quote_id": quote_id,
        "origin": "A", "destination": "D", "legs": card["legs"], "transfers": ["H1", "H3", "H4"],
        "distance_display": "130.0 km", "price_display": "$16.90", "booked_at": "2026-11-23T08:03:12Z",
    }
    assert out["message"]["role"] == "system" and out["message"]["text"] == "Ticket booked."
    assert out["message"]["cards"] == [out["ticket"]]
    example = json.loads((FIXTURES / "booking_ok.json").read_text(encoding="utf-8"))
    assert out["message"]["text"] == example["message"]["text"]

    row = world.sql("select user_id, quote_id, distance, price, network_fingerprint, confirmed_message_id "
                    "from tickets")
    assert row == [(world.alice, quote_id, "130." + Z, "16.9" + "0" * 29,
                    world.networks.current().fingerprint.id, message["id"])]
    assert world.sql("select status, closed_at from quotes") == [("booked", "2026-11-23T08:03:12Z")]
    assert world.sql("select nonce_used_at from quote_cards") == [("2026-11-23T08:03:12Z",)]
    assert world.store.list_messages(world.alice, world.conv)[-1]["id"] == out["message"]["id"]


def test_either_card_of_a_quote_can_be_clicked_but_only_one_ticket_results(world):
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    n1 = world.done(quote.cards)["cards"][0]["state"]["nonce"]
    n2 = world.done(world.airline.request_booking(world.alice, world.conv, quote_id).cards)["cards"][0]["state"]["nonce"]
    world.airline.book(world.alice, quote_id, n2)
    assert refused(world, world.alice, quote_id, n1).code == "already_booked"
    assert world.ticket_count() == 1


def test_two_quotes_of_one_conversation_are_independent(world):
    """Q1: no supersede. Both stay bookable; booking one leaves the other untouched."""
    q1, n1 = world.presented("A", "D")
    q2, n2 = world.presented("D", "A")
    assert world.status(q1) == world.status(q2) == "presented"
    world.airline.book(world.alice, q2, n2)
    assert world.status(q1) == "presented"
    world.airline.book(world.alice, q1, n1)
    assert world.ticket_count() == 2


# --- refusals, one per code --------------------------------------------------------------------
def test_error_response_has_the_contract_shape(world):
    quote_id, _ = world.presented()
    err = refused(world, world.alice, quote_id, "n_wrong")
    assert_shape(SCHEMAS["error"], err.as_response(), "error")
    assert err.as_response() == {"error": {"code": "invalid_nonce", "message": err.message}}
    stale = json.loads((FIXTURES / "booking_error_stale.json").read_text(encoding="utf-8"))
    assert BookingError("stale").as_response() == stale
    assert {c: BookingError(c).status for c in BookingError._TEXT} == {
        "not_found": 404, "invalid_nonce": 409, "not_presented": 409, "expired": 409,
        "already_booked": 409, "stale": 409, "conversation_busy": 409, "data_unavailable": 503}


def test_not_found_for_missing_quotes_and_other_users_quotes(world):
    """B1. Bob gets the same answer for Alice's quote as for no quote, even with her nonce."""
    quote_id, nonce = world.presented()
    assert refused(world, world.alice, "q_missing", nonce).code == "not_found"
    err = refused(world, world.bob, quote_id, nonce)
    assert err.code == "not_found" and err.status == 404
    assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
    assert world.sql("select nonce_used_at from quote_cards") == [(None,)]  # her nonce still works
    world.airline.book(world.alice, quote_id, nonce)


def test_not_presented_before_done(world):
    """B2. A quote whose card never went out with `done` cannot be booked, whatever is sent."""
    quote_id = world.quote().data["quote_id"]
    for nonce in ("", "n_guess", None):
        assert refused(world, world.alice, quote_id, nonce).code == "not_presented"
    # the nonce the card would get is of no use either
    with world.store.read() as con:
        guess = world.airline._nonce(records.get_quote(con, quote_id), "m_guess")
    assert refused(world, world.alice, quote_id, guess).code == "not_presented"
    assert world.ticket_count() == 0 and world.status(quote_id) == "quoted"


@pytest.mark.parametrize("nonce", ["", "n_", "n_" + "0" * 32, "é", "\ud800", "n_\udfff" + "0" * 31, None, 7,
                                   ["n_x"], {"nonce": "x"}])
def test_invalid_nonce_values(world, nonce):
    """B3. Anything but the nonce of a presented card of this quote is refused, with no effect."""
    quote_id, good = world.presented()
    assert refused(world, world.alice, quote_id, nonce).code == "invalid_nonce"
    assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
    world.airline.book(world.alice, quote_id, good)  # the real one still works


def test_nonce_is_bound_to_quote_user_and_message(world):
    q1, n1 = world.presented("A", "D")
    q2, n2 = world.presented("A", "C")
    q3, n3 = world.presented("A", "D", world.bob, world.bob_conv)
    assert len({n1, n2, n3}) == 3
    for quote_id, nonce in ((q1, n2), (q2, n1), (q1, n3)):
        assert refused(world, world.alice, quote_id, nonce).code == "invalid_nonce"
    assert refused(world, world.bob, q3, n1).code == "invalid_nonce"
    assert world.ticket_count() == 0


def test_nonce_from_another_server_secret_is_invalid_until_reload(world):
    """A restart with a new secret: open pages hold dead nonces, a reload hands out working ones."""
    quote_id, old = world.presented()
    restarted = Airline(world.store, world.networks, nonce_secret=b"another-secret-0123456789")
    with pytest.raises(BookingError) as info:
        restarted.book(world.alice, quote_id, old)
    assert info.value.code == "invalid_nonce"
    fresh = restarted.messages(world.alice, world.conv)[0]["cards"][0]["state"]["nonce"]
    assert fresh != old
    restarted.book(world.alice, quote_id, fresh)
    assert Airline(world.store, world.networks)._secret != Airline(world.store, world.networks)._secret


def test_expired_at_fifteen_minutes(world):
    """B4. Bookable up to 14:59 after the quote, refused from 15:00 on, and for good."""
    q1, n1 = world.presented()
    world.clock.advance(minutes=14, seconds=59)
    q2, n2 = world.presented()  # a later quote has its own clock
    world.airline.book(world.alice, q1, n1)

    q3, n3 = world.presented()
    world.clock.advance(minutes=14, seconds=59.9)
    world.airline.book(world.alice, q3, n3)  # still inside the 15th minute

    world.clock.advance(seconds=0.1)  # q2 is now 15:00.0 old
    err = refused(world, world.alice, q2, n2)
    assert err.code == "expired" and err.status == 409
    assert world.sql("select status, status_reason, closed_at from quotes where id = ?", (q2,)) == \
        [("expired", "validity elapsed", "2026-11-23T08:29:59Z")]
    assert refused(world, world.alice, q2, n2).code == "expired"  # Q3: terminal
    assert world.ticket_count() == 2


def test_expiry_wins_over_a_wrong_nonce_and_over_not_presented(world):
    presented, _ = world.presented()
    quoted = world.quote().data["quote_id"]
    world.clock.advance(minutes=20)
    assert refused(world, world.alice, presented, "n_wrong").code == "expired"
    assert refused(world, world.alice, quoted, "n_wrong").code == "expired"


def test_already_booked_on_replay(world):
    """B5. The same click again: no second ticket, the error names the existing one."""
    quote_id, nonce = world.presented()
    ticket = world.airline.book(world.alice, quote_id, nonce)["ticket"]
    for again in (nonce, "n_other", None):
        err = refused(world, world.alice, quote_id, again)
        assert err.code == "already_booked" and err.ticket_id == ticket["id"]
    world.clock.advance(minutes=30)  # booked stays booked, it does not expire
    assert refused(world, world.alice, quote_id, nonce).code == "already_booked"
    assert world.ticket_count() == 1 and len(world.store.list_messages(world.alice, world.conv)) == 2


# --- B6: re-check against the current locations.db ---------------------------------------------
STALE_CHANGES = {
    "hub link removed": ["delete from hub_links where hub_a = 'H3' and hub_b = 'H4'"],
    "spoke removed": ["update locations set connected_hubs = '' where city_name = 'A'"],
    "endpoint moved": ["update locations set coordinate = '0,-11' where city_name = 'A'"],
    "transfer hub moved": ["update locations set coordinate = '31,40' where city_name = 'H3'"],
    "moved without changing any distance": [  # the whole route shifted: same legs, other data
        "update locations set coordinate = '100,90' where city_name = 'A'",
        "update locations set coordinate = '100,100' where city_name = 'H1'",
        "update locations set coordinate = '130,140' where city_name = 'H3'",
        "update locations set coordinate = '190,140' where city_name = 'H4'",
        "update locations set coordinate = '190,150' where city_name = 'D'",
        "update locations set coordinate = '130,100' where city_name = 'H2'",
    ],
    "cheaper route appeared": ["insert into hub_links values ('H1', 'H4')"],
    "equally cheap route with fewer legs appeared": [
        "update locations set coordinate = '60,80' where city_name = 'H3'",  # H1 H3 H4 collinear
        "update locations set coordinate = '120,160' where city_name = 'H4'",
        "update locations set coordinate = '120,170' where city_name = 'D'",
    ],
    "city removed": ["update locations set connected_hubs = 'H3' where city_name = 'C'",
                     "delete from locations where city_name = 'D'"],
    "transfer city is no longer a hub": [
        "delete from hub_links where hub_a = 'H3' or hub_b = 'H3'",
        "update locations set connected_hubs = 'H4' where city_name = 'C'",
        "update locations set is_hub = 0, connected_hubs = 'H4' where city_name = 'H3'"],
}


@pytest.mark.parametrize("change", STALE_CHANGES)
def test_changed_route_data_makes_the_quote_stale(world, change):
    quote_id, nonce = world.presented("A", "D")
    statements = STALE_CHANGES[change]
    if change == "equally cheap route with fewer legs appeared":
        # quote on the collinear layout first, then add the direct link H1 -- H4 (same length)
        world.edit_db(*statements)
        quote_id, nonce = world.presented("A", "D")
        statements = ["insert into hub_links values ('H1', 'H4')"]
    world.edit_db(*statements)

    err = refused(world, world.alice, quote_id, nonce)
    assert err.code == "stale" and err.status == 409 and err.reason
    assert world.ticket_count() == 0
    status, reason = world.sql("select status, status_reason from quotes where id = ?", (quote_id,))[0]
    assert status == "stale" and reason == err.reason
    assert refused(world, world.alice, quote_id, nonce).code == "stale"  # Q3: terminal
    state = world.airline.messages(world.alice, world.conv)[-1]["cards"][0]["state"]
    assert state == {"status": "stale", "bookable": False, "nonce": None, "ticket_id": None}
    r = world.airline.request_booking(world.alice, world.conv, quote_id)
    assert r.code == "stale" and r.cards[0] == {
        "type": "notice", "code": "stale",
        "message": "Route data changed since this quote. Please ask for a new quote."}


UNREADABLE = {
    "file missing": lambda w: os.remove(w.db),
    "database invalid": lambda w: w.edit_db("update locations set coordinate = 'oops' where city_name = 'U'"),
    "tables gone": lambda w: w.edit_db("drop table hub_links"),
    "not a database": lambda w: w.replace_db(b"not sqlite at all"),
}


@pytest.mark.parametrize("how", UNREADABLE)
def test_unreadable_locations_db_refuses_without_touching_the_quote(world, how):
    """Not readable is not "changed": 503 data_unavailable, the quote stays presented, the nonce
    stays valid, and the same click books once the data is back."""
    quote_id, nonce = world.presented()
    original = world.db.read_bytes()
    UNREADABLE[how](world)
    for _ in range(2):
        err = refused(world, world.alice, quote_id, nonce)
        assert err.code == "data_unavailable" and err.status == 503 and err.reason
        assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
        assert world.sql("select nonce_used_at from quote_cards") == [(None,)]
    state = world.airline.messages(world.alice, world.conv)[0]["cards"][0]["state"]
    assert state["bookable"] is True and state["nonce"] == nonce
    world.replace_db(original)
    world.airline.book(world.alice, quote_id, nonce)
    assert world.ticket_count() == 1


def test_unreadable_then_changed_data_is_stale(world):
    quote_id, nonce = world.presented()
    world.edit_db("update locations set coordinate = 'oops' where city_name = 'U'")
    assert refused(world, world.alice, quote_id, nonce).code == "data_unavailable"
    world.edit_db("update locations set coordinate = '5,5' where city_name = 'U'",
                  "insert into hub_links values ('H1', 'H4')")
    assert refused(world, world.alice, quote_id, nonce).code == "stale"
    assert world.status(quote_id) == "stale"


def test_a_committed_change_that_sits_in_the_wal_never_books_at_the_old_price(world):
    """Named regression (review round 01, finding 1). In WAL mode a committed change lives in
    `-wal` while the main file's bytes, and so the fingerprint, stay the same. A WAL-mode
    locations.db is refused outright, so the click is refused instead of booking stale data."""
    quote_id, nonce = world.presented("A", "D")
    admin = sqlite3.connect(world.db)
    try:
        assert admin.execute("pragma journal_mode = wal").fetchone() == ("wal",)
        admin.execute("update locations set coordinate = '0,-500' where city_name = 'A'")
        admin.commit()  # committed, visible to every reader, but only in locations.db-wal
        reader = sqlite3.connect(world.db)
        assert reader.execute("select coordinate from locations where city_name = 'A'").fetchone() == ("0,-500",)
        reader.close()
        err = refused(world, world.alice, quote_id, nonce)
        assert err.code == "data_unavailable" and "WAL" in err.reason
        assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
        with pytest.raises(Exception, match="WAL"):
            world.airline.quote_route(world.alice, world.conv, "A", "D")  # no quote on it either
        admin.execute("pragma journal_mode = delete")  # checkpoint: back to a plain file
    finally:
        admin.close()
    world.bump_mtime()
    err = refused(world, world.alice, quote_id, nonce)
    assert err.code == "stale" and err.reason == "coordinate changed: A"
    assert world.ticket_count() == 0
    assert world.quote("A", "D").cards[0]["price_display"] == "$80.60"  # the current data


FRESH_CHANGES = {
    "unserved city moved": ["update locations set coordinate = '6,6' where city_name = 'U'"],
    "city added": ["insert into locations values ('N', '1000,1000', 0, 'I1')"],
    "other island changed": ["update locations set coordinate = '504,504' where city_name = 'I2'"],
    "far-away link added": ["insert into hub_links values ('H2', 'I1')"],
    "off-route spoke removed": ["update locations set connected_hubs = '' where city_name = 'B'"],
}


@pytest.mark.parametrize("change", FRESH_CHANGES)
def test_unrelated_changes_do_not_block_the_booking(world, change):
    """The file changed (new fingerprint) but the quoted route recomputes identically."""
    quote_id, nonce = world.presented("A", "D")
    before = world.networks.current().fingerprint.id
    world.edit_db(*FRESH_CHANGES[change])
    world.airline.book(world.alice, quote_id, nonce)
    after = world.networks.current().fingerprint.id
    assert after != before
    # the ticket records the database it was verified against, the quote the one it was made on
    assert world.sql("select t.network_fingerprint, q.network_fingerprint from tickets t "
                     "join quotes q on q.id = t.quote_id") == [(after, before)]


def test_replaced_file_with_the_same_content_books(world):
    quote_id, nonce = world.presented()
    world.replace_db(world.db.read_bytes())
    world.airline.book(world.alice, quote_id, nonce)
    assert world.ticket_count() == 1


def test_change_and_restore_before_the_click_books(world):
    quote_id, nonce = world.presented()
    original = world.db.read_bytes()
    world.edit_db("delete from hub_links where hub_a = 'H3' and hub_b = 'H4'")
    assert world.airline.quote_route(world.alice, world.conv, "A", "D").code == "unreachable"
    world.replace_db(original)
    world.airline.book(world.alice, quote_id, nonce)
    assert world.ticket_count() == 1


def test_a_change_that_keeps_size_and_mtime_is_still_caught(world):
    """The re-check hashes the file (strict), so it does not depend on mtime or size."""
    quote_id, nonce = world.presented("A", "D")
    stat = world.db.stat()
    con = sqlite3.connect(world.db)
    con.execute("update locations set coordinate = '0,-12' where city_name = 'A'")  # '0,-10': same length
    con.commit()
    con.close()
    os.utime(world.db, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = world.db.stat()
    assert (after.st_size, after.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
    assert world.networks.current().cities["A"].y == -10  # the cheap check does not notice ...
    assert refused(world, world.alice, quote_id, nonce).code == "stale"  # ... the booking does
    assert world.networks.current().cities["A"].y == -12


def test_a_new_quote_after_stale_books_on_the_new_data(world):
    quote_id, nonce = world.presented("A", "D")
    world.edit_db("insert into hub_links values ('H1', 'H4')")
    assert refused(world, world.alice, quote_id, nonce).code == "stale"
    new_id, new_nonce = world.presented("A", "D")
    ticket = world.airline.book(world.alice, new_id, new_nonce)["ticket"]
    assert ticket["transfers"] == ["H1", "H4"] and world.ticket_count() == 1


# --- B3: nothing but the click books -----------------------------------------------------------
def test_no_tool_and_no_message_creates_a_ticket_or_reveals_a_nonce(world):
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    nonce = world.done(quote.cards)["cards"][0]["state"]["nonce"]
    seen = [quote]
    for text in ("yes, book it", "ok confirm", "go ahead and buy the ticket", "no, don't book"):
        world.store.add_message(world.alice, world.conv, "user", text)
        seen.append(world.airline.request_booking(world.alice, world.conv, quote_id))
        seen.append(world.airline.list_tickets(world.alice))
        seen.append(world.airline.find_city("D"))
    assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
    for result in seen:
        assert nonce not in json.dumps([result.data, result.cards])
        for card in result.cards:
            if "state" in card:
                assert card["state"]["nonce"] is None and card["state"]["bookable"] is False
    assert [name for name in dir(Airline) if "book" in name.lower() and not name.startswith("_")] == \
        ["book", "book_async", "request_booking"]  # book_async is book in a worker thread


# --- B7: atomic --------------------------------------------------------------------------------
def test_a_failure_inside_the_booking_transaction_leaves_nothing_behind(world, monkeypatch):
    quote_id, nonce = world.presented()
    real_add = records.add_message

    def failing(con, now, conversation_id, role, *args, **kwargs):
        if role == "system":
            raise RuntimeError("disk full")
        return real_add(con, now, conversation_id, role, *args, **kwargs)

    monkeypatch.setattr(records, "add_message", failing)
    with pytest.raises(RuntimeError, match="disk full"):
        world.airline.book(world.alice, quote_id, nonce)
    assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
    assert world.sql("select nonce_used_at from quote_cards") == [(None,)]
    assert not world.airline.locks.locked(world.conv)  # the conversation lock was released

    monkeypatch.undo()
    world.airline.book(world.alice, quote_id, nonce)  # nothing was consumed: the click works
    assert world.ticket_count() == 1 and world.status(quote_id) == "booked"


# --- B5/B7 under concurrency -------------------------------------------------------------------
def click_together(airlines, user, quote_id, nonces):
    """Every (airline, nonce) pair clicks at the same moment; returns outcomes in order."""
    barrier = threading.Barrier(len(airlines))
    outcomes = [None] * len(airlines)

    def click(i):
        barrier.wait(5)
        try:
            outcomes[i] = airlines[i].book(user, quote_id, nonces[i])["ticket"]["id"]
        except BookingError as exc:
            outcomes[i] = exc
        except Exception as exc:  # pragma: no cover - reported by the assertions below
            outcomes[i] = repr(exc)

    threads = [threading.Thread(target=click, args=(i,)) for i in range(len(airlines))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return outcomes


def assert_one_winner(world, quote_id, outcomes):
    tickets = [o for o in outcomes if isinstance(o, str)]
    errors = [o for o in outcomes if not isinstance(o, str)]
    assert len(tickets) == 1, outcomes
    assert all(isinstance(e, BookingError) and e.code == "already_booked" and e.ticket_id == tickets[0]
               for e in errors), outcomes
    assert world.sql("select id from tickets where quote_id = ?", (quote_id,)) == [(tickets[0],)]
    assert world.status(quote_id) == "booked"
    assert len([m for m in world.store.list_messages(world.alice, world.conv) if m["role"] == "system"]) == 1


def test_concurrent_clicks_with_the_same_nonce_make_one_ticket(world):
    for _ in range(5):
        quote_id, nonce = world.presented()
        outcomes = click_together([world.airline] * 8, world.alice, quote_id, [nonce] * 8)
        assert_one_winner(world, quote_id, outcomes)
        world.store.add_message(world.alice, world.conv, "user", "next")
        # keep one system message per round countable
        with world.store.write() as con:
            con.execute("delete from tool_calls")
            con.execute("update messages set role = 'user' where role = 'system'")
    assert world.ticket_count() == 5


def test_concurrent_clicks_on_two_cards_of_one_quote_make_one_ticket(world):
    quote = world.quote()
    quote_id = quote.data["quote_id"]
    n1 = world.done(quote.cards)["cards"][0]["state"]["nonce"]
    n2 = world.done(world.airline.request_booking(world.alice, world.conv, quote_id).cards)["cards"][0]["state"]["nonce"]
    outcomes = click_together([world.airline] * 6, world.alice, quote_id, [n1, n2] * 3)
    assert_one_winner(world, quote_id, outcomes)


def test_the_database_alone_serializes_bookings_without_the_shared_lock(world):
    """Separate Airline objects (separate lock registries, separate connections) stand in for
    separate processes: BEGIN IMMEDIATE + the UNIQUE ticket still allow exactly one ticket."""
    quote_id, nonce = world.presented()
    airlines = [Airline(world.store, NetworkProvider(world.db), nonce_secret=SECRET) for _ in range(8)]
    outcomes = click_together(airlines, world.alice, quote_id, [nonce] * 8)
    assert_one_winner(world, quote_id, outcomes)


def test_concurrent_clicks_on_a_stale_quote_all_fail(world):
    quote_id, nonce = world.presented()
    world.edit_db("insert into hub_links values ('H1', 'H4')")
    outcomes = click_together([world.airline] * 6, world.alice, quote_id, [nonce] * 6)
    assert [getattr(o, "code", o) for o in outcomes] == ["stale"] * 6
    assert world.ticket_count() == 0


# --- the conversation lock ---------------------------------------------------------------------
def test_book_waits_for_the_conversation_lock_held_by_a_chat_turn(world):
    quote_id, nonce = world.presented()
    with world.airline.locks.hold(world.conv):  # a chat turn is running
        with pytest.raises(BookingError) as info:
            world.airline.book(world.alice, quote_id, nonce, lock_timeout=0.05)
        err = info.value
        assert err.code == "conversation_busy" and err.status == 409
        assert err.as_response()["error"]["code"] == "conversation_busy"
        assert world.ticket_count() == 0 and world.status(quote_id) == "presented"
        assert world.sql("select nonce_used_at from quote_cards") == [(None,)]

        done = threading.Event()
        result = {}

        def click():
            result["out"] = world.airline.book(world.alice, quote_id, nonce, lock_timeout=None)
            done.set()

        thread = threading.Thread(target=click)
        thread.start()
        assert not done.wait(0.2)  # blocked behind the turn
        # the turn can still use the airline: tools do not take the lock themselves
        assert world.airline.request_booking(world.alice, world.conv, quote_id).ok
    thread.join(5)
    assert done.is_set() and result["out"]["ticket"]["quote_id"] == quote_id


def test_book_gives_up_waiting_by_default(world, monkeypatch):
    """Without an explicit timeout a click does not hang for a whole LLM turn."""
    from app.airline import service

    assert service.BOOK_LOCK_TIMEOUT == 5.0
    assert Airline.book.__kwdefaults__["lock_timeout"] == service.BOOK_LOCK_TIMEOUT
    quote_id, nonce = world.presented()
    waited = []
    real_hold = world.airline.locks.hold

    def hold(conversation_id, timeout=None):
        waited.append(timeout)
        return real_hold(conversation_id, timeout=0.02)

    monkeypatch.setattr(world.airline.locks, "hold", hold)
    with real_hold(world.conv):
        assert refused(world, world.alice, quote_id, nonce).code == "conversation_busy"
    assert waited == [5.0]


def test_book_async_does_not_block_the_event_loop_while_a_turn_holds_the_lock(world):
    """Review round 01, finding 3: a click arriving on the event loop during a streamed turn."""
    quote_id, nonce = world.presented()
    ticks = []

    async def main():
        async with world.airline.locks.hold_async(world.conv):  # the streamed turn
            click = asyncio.create_task(world.airline.book_async(world.alice, quote_id, nonce))
            for _ in range(5):
                await asyncio.sleep(0.02)  # the loop keeps running while the click waits
                ticks.append(click.done())
        return await click

    out = asyncio.run(main())
    assert ticks == [False] * 5 and out["ticket"]["quote_id"] == quote_id

    async def busy():
        async with world.airline.locks.hold_async(world.conv):
            with pytest.raises(BookingError) as info:
                await world.airline.book_async(world.alice, quote_id, nonce, lock_timeout=0.05)
            return info.value.code

    assert asyncio.run(busy()) == "conversation_busy"


def test_locks_are_per_conversation(world):
    quote_id, nonce = world.presented(user=world.bob, conv=world.bob_conv)
    with world.airline.locks.hold(world.conv):  # Alice's conversation is busy
        world.airline.book(world.bob, quote_id, nonce, lock_timeout=0.05)
    assert world.ticket_count() == 1


def test_lock_registry_does_not_grow():
    locks = ConversationLocks()
    with locks.hold("c_1"):
        assert locks.locked("c_1") and not locks.locked("c_2")
        with pytest.raises(ConversationBusy):
            with locks.hold("c_1", timeout=0.01):
                pass
        assert locks.locked("c_1")
    assert not locks.locked("c_1") and locks._entries == {}
    with pytest.raises(KeyError):
        with locks.hold("c_3"):
            raise KeyError("released on error too")
    assert locks._entries == {}


def test_async_and_sync_holders_share_one_lock():
    locks = ConversationLocks()
    order = []

    async def turn(name, hold_for):
        async with locks.hold_async("c_1"):
            order.append(f"{name} in")
            await asyncio.sleep(hold_for)
            order.append(f"{name} out")

    def sync_click():
        with locks.hold("c_1"):
            order.append("click in")
            order.append("click out")

    async def main():
        first = asyncio.create_task(turn("turn1", 0.05))
        await asyncio.sleep(0.01)
        click = asyncio.create_task(asyncio.to_thread(sync_click))
        second = asyncio.create_task(turn("turn2", 0.0))
        await asyncio.sleep(0.01)
        assert order == ["turn1 in"]  # both wait; the event loop keeps running meanwhile
        await asyncio.gather(first, click, second)

    asyncio.run(main())
    assert order[:2] == ["turn1 in", "turn1 out"]
    pairs = [order[i:i + 2] for i in range(0, len(order), 2)]
    assert sorted(map(tuple, pairs)) == [("click in", "click out"), ("turn1 in", "turn1 out"),
                                         ("turn2 in", "turn2 out")]  # never interleaved
    assert locks._entries == {}


def test_async_waiter_can_time_out_or_be_cancelled_without_leaking_the_lock():
    locks = ConversationLocks()

    async def main():
        async with locks.hold_async("c_1"):
            with pytest.raises(ConversationBusy):
                async with locks.hold_async("c_1", timeout=0.03):
                    pass

            async def waiter():
                async with locks.hold_async("c_1"):
                    raise AssertionError("must not get the lock")

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not locks.locked("c_1")
        async with locks.hold_async("c_1", timeout=0.05):  # free again
            pass

    asyncio.run(main())
    assert locks._entries == {}
