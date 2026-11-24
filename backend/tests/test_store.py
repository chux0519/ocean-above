import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.store import SCHEMA_VERSION, NotFound, Store, iso, parse_iso, records

from .airline_helpers import FakeClock


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data" / "app.db", clock=FakeClock())


def raw(store, query, args=()):
    con = sqlite3.connect(store.path)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


def test_schema_has_the_design_tables_and_is_versioned(store):
    tables = {r[0] for r in raw(store, "select name from sqlite_master where type = 'table'")}
    assert {"users", "conversations", "messages", "llm_calls", "tool_calls", "quotes", "quote_cards",
            "tickets"} <= tables
    assert raw(store, "pragma user_version") == [(SCHEMA_VERSION,)]
    assert raw(store, "pragma journal_mode") == [("wal",)]


def test_opening_again_keeps_the_data_and_does_not_migrate_twice(store):
    user = store.upsert_user("dev", "demo", "demo")
    again = Store(store.path)
    assert again.get_user(user["id"])["display_name"] == "demo"
    assert raw(store, "pragma user_version") == [(SCHEMA_VERSION,)]


def test_a_newer_schema_is_refused(store):
    con = sqlite3.connect(store.path)
    con.execute(f"pragma user_version = {SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(RuntimeError, match="schema"):
        Store(store.path)


def test_times_are_utc_seconds_and_need_a_timezone():
    moment = datetime(2026, 11, 23, 16, 0, 0, 987654, tzinfo=timezone(timedelta(hours=8)))
    assert iso(moment) == "2026-11-23T08:00:00Z"
    assert parse_iso("2026-11-23T08:00:00Z") == datetime(2026, 11, 23, 8, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        iso(datetime(2026, 11, 23))


def test_users_are_unique_per_provider_and_subject(store):
    a = store.upsert_user("dev", "demo", "demo", email="demo@example-mail.test")
    b = store.upsert_user("dev", "demo", "Demo User")
    c = store.upsert_user("google", "demo", "demo")
    assert a["id"] == b["id"] != c["id"]
    assert a["id"].startswith("u_") and b["display_name"] == "Demo User"
    assert store.get_user("u_missing") is None
    assert raw(store, "select count(*) from users") == [(2,)]


def test_conversations_newest_first_with_preview_and_ownership(store):
    alice = store.upsert_user("dev", "alice", "Alice")["id"]
    bob = store.upsert_user("dev", "bob", "Bob")["id"]
    first = store.create_conversation(alice, "first")
    store.clock.advance(seconds=5)
    second = store.create_conversation(alice)
    assert set(first) == {"id", "title", "created_at", "updated_at", "preview"}
    assert [c["id"] for c in store.list_conversations(alice)] == [second["id"], first["id"]]

    store.clock.advance(seconds=5)
    store.add_message(alice, first["id"], "user", "x" * 500)
    listed = store.list_conversations(alice)
    assert [c["id"] for c in listed] == [first["id"], second["id"]]  # updated_at moved
    assert listed[0]["preview"] == "x" * 120 and listed[0]["updated_at"] == "2026-11-23T08:00:10Z"
    assert listed[1]["preview"] == ""

    assert store.get_conversation(alice, first["id"])["title"] == "first"
    assert store.get_conversation(bob, first["id"]) is None  # not the owner: same as missing
    assert store.list_conversations(bob) == []
    assert store.set_conversation_title(bob, first["id"], "hijack") is False
    assert store.set_conversation_title(alice, first["id"], "Guangzhou to Lima") is True
    assert store.get_conversation(alice, first["id"])["title"] == "Guangzhou to Lima"


def test_messages_keep_insertion_order_even_within_one_second(store):
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    sent = [store.add_message(user, conv, role, f"m{i}", [{"type": "notice", "code": "same_city", "message": "é"}])
            for i, role in enumerate(["user", "assistant", "system", "user"])]
    got = store.list_messages(user, conv)
    assert [m["id"] for m in got] == [m["id"] for m in sent]
    assert got[1]["cards"] == [{"type": "notice", "code": "same_city", "message": "é"}]
    assert got[0]["guard_problems"] is None and all(m["id"].startswith("m_") for m in got)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_message(user, conv, "robot", "bad role")
    with pytest.raises(NotFound):
        store.add_message(user, "c_missing", "user", "no such conversation")


def test_call_logs_round_trip_json(store):
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    message = store.add_message(user, conv, "user", "hi", guard_problems=None)
    store.add_llm_call(user, conv, {"messages": [{"role": "user", "content": "hi"}]}, {"text": "héllo"},
                       latency_ms=120, tokens=42, model="example-model")
    store.add_tool_call(user, message["id"], "find_city", {"name": "Lima"}, {"code": "ok"}, latency_ms=3)
    store.add_tool_call(user, message["id"], "quote_route", {"origin": 1}, error="bad arguments")
    llm = store.list_llm_calls(user, conv)
    assert llm[0]["response"] == {"text": "héllo"} and llm[0]["tokens"] == 42
    tools = store.list_tool_calls(user, message["id"])
    assert [t["name"] for t in tools] == ["find_city", "quote_route"]
    assert tools[0]["result"] == {"code": "ok"} and tools[1]["result"] is None
    assert tools[1]["error"] == "bad arguments"


def test_conversation_level_methods_check_the_owner_in_the_same_call(store):
    """M12: nothing can be read from or written into another user's conversation by id."""
    alice = store.upsert_user("dev", "alice", "Alice")["id"]
    bob = store.upsert_user("dev", "bob", "Bob")["id"]
    conv = store.create_conversation(alice)["id"]
    message = store.add_message(alice, conv, "user", "private")["id"]
    for call in (
        lambda: store.add_message(bob, conv, "user", "intruder"),
        lambda: store.list_messages(bob, conv),
        lambda: store.add_llm_call(bob, conv, {}, {}),
        lambda: store.list_llm_calls(bob, conv),
        lambda: store.add_tool_call(bob, message, "find_city", {}),
        lambda: store.list_tool_calls(bob, message),
        lambda: store.list_messages(alice, "c_missing"),
        lambda: store.add_tool_call(alice, "m_missing", "find_city", {}),
    ):
        with pytest.raises(NotFound) as info:
            call()
        assert info.value.status == 404 and info.value.code == "not_found"
    assert [m["text"] for m in store.list_messages(alice, conv)] == ["private"]
    assert raw(store, "select count(*) from llm_calls") == [(0,)]
    assert raw(store, "select count(*) from tool_calls") == [(0,)]


def test_an_error_that_already_rolled_back_is_not_masked(store):
    """sqlite ends the transaction itself on some errors; the original error must surface."""
    with pytest.raises(KeyError, match="original"):
        with store.write() as con:
            con.execute("ROLLBACK")  # what sqlite does on e.g. a full disk
            raise KeyError("original")


def test_write_rolls_back_on_error_and_commits_otherwise(store):
    with pytest.raises(ZeroDivisionError):
        with store.write() as con:
            records.upsert_user(con, store.now(), "dev", "ghost", "ghost")
            1 / 0
    assert raw(store, "select count(*) from users") == [(0,)]
    with store.write() as con:
        records.upsert_user(con, store.now(), "dev", "real", "real")
    assert raw(store, "select count(*) from users") == [(1,)]


def test_write_is_begin_immediate_so_writers_queue(store):
    """A second writer cannot even start while the first transaction is open."""
    order = []
    inside = threading.Event()
    release = threading.Event()

    def first():
        with store.write() as con:
            records.upsert_user(con, store.now(), "dev", "one", "one")
            inside.set()
            release.wait(5)
            order.append("first commits")

    def second():
        inside.wait(5)
        with store.write() as con:
            order.append("second starts")
            assert con.execute("select count(*) from users").fetchone()[0] == 1

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    inside.wait(5)
    threads[1].join(0.2)  # the second writer is blocked on BEGIN IMMEDIATE
    assert order == []
    release.set()
    for t in threads:
        t.join(5)
    assert order == ["first commits", "second starts"]


def _quote(store, con, user, conv):
    return records.insert_quote(
        con, now=store.now(), expires_at=store.now(), conversation_id=conv, user_id=user,
        origin="A", destination="B",
        legs=[{"from": "A", "to": "B", "distance": "1.0", "from_coord": ["0", "0"],
               "to_coord": ["1", "0"], "source": "hub_links(A, B)"}],
        distance="1.0", price="0.13", network_fingerprint="sha256:test",
    )


def test_database_guards_one_ticket_per_presented_quote_and_terminal_states(store):
    """The schema itself refuses the states B2, B5, B7 and Q3 forbid, whatever the code does."""
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    message = store.add_message(user, conv, "assistant", "card")["id"]
    with store.write() as con:
        quote = _quote(store, con, user, conv)
        assert quote["status"] == "quoted" and quote["legs"][0]["to_coord"] == ["1", "0"]
        with pytest.raises(sqlite3.IntegrityError, match="presented"):  # B2: not presented yet
            records.insert_ticket(con, now=store.now(), quote=quote, network_fingerprint="x",
                                  confirmed_message_id=message)
        records.set_quote_status(con, quote, "presented", store.now())
        ticket = records.insert_ticket(con, now=store.now(), quote=quote, network_fingerprint="x",
                                       confirmed_message_id=message)
        assert ticket["id"].startswith("t_") and ticket["legs_snapshot"] == quote["legs"]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):  # B5
            records.insert_ticket(con, now=store.now(), quote=quote, network_fingerprint="x",
                                  confirmed_message_id=message)
        records.set_quote_status(con, quote, "booked", store.now())
        assert quote["closed_at"] == store.now()
        for other in ("presented", "expired", "stale", "quoted"):  # Q3
            with pytest.raises(sqlite3.IntegrityError, match="terminal"):
                con.execute("update quotes set status = ? where id = ?", (other, quote["id"]))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("update tickets set price = '0' where id = ?", (ticket["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("update quotes set status = 'cancelled' where id = ?", (quote["id"],))


def test_set_quote_status_refuses_a_row_that_changed_underneath(store):
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    with store.write() as con:
        quote = _quote(store, con, user, conv)
        stale_copy = dict(quote)
        records.set_quote_status(con, quote, "presented", store.now())
        with pytest.raises(RuntimeError, match="changed underneath"):
            records.set_quote_status(con, stale_copy, "expired", store.now())


def test_nonce_of_a_card_is_consumed_once(store):
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    message = store.add_message(user, conv, "assistant", "card")["id"]
    with store.write() as con:
        quote = _quote(store, con, user, conv)
        records.add_quote_card(con, quote["id"], message, store.now())
        records.add_quote_card(con, quote["id"], message, store.now())  # same card twice: one row
        assert len(records.quote_cards(con, quote["id"])) == 1
        assert records.mark_nonce_used(con, quote["id"], message, store.now()) is True
        assert records.mark_nonce_used(con, quote["id"], message, store.now()) is False
        assert records.mark_nonce_used(con, quote["id"], "m_other", store.now()) is False


def test_a_v3_database_is_migrated_to_v4_and_old_messages_are_completed(tmp_path):
    """#48: messages.status is added by migration v4; every older row reads `completed`, nothing is lost."""
    path = tmp_path / "app.db"
    store = Store(path, clock=FakeClock())
    user = store.upsert_user("dev", "demo", "demo")["id"]
    conv = store.create_conversation(user)["id"]
    store.add_message(user, conv, "user", "hello")
    store.add_message(user, conv, "assistant", "hi", [{"type": "notice", "code": "same_city", "message": "x"}],
                      [{"kind": "max_steps", "value": "6"}])
    before = [{k: v for k, v in m.items() if k != "status"} for m in store.list_messages(user, conv)]
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE messages DROP COLUMN status")  # back to the v3 layout
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()
    again = Store(path, clock=FakeClock())
    after = again.list_messages(user, conv)
    assert [m["status"] for m in after] == ["completed", "completed"]
    assert [{k: v for k, v in m.items() if k != "status"} for m in after] == before
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    with pytest.raises(sqlite3.IntegrityError):  # only the two statuses
        con.execute("UPDATE messages SET status = 'paused'")
    con.close()
    stopped = again.add_message(user, conv, "assistant", "", status="cancelled")
    assert again.list_messages(user, conv)[-1]["status"] == stopped["status"] == "cancelled"
    with pytest.raises(ValueError):
        again.add_message(user, conv, "assistant", "", status="paused")
