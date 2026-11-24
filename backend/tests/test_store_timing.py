"""The optional app.db timing hook (#41): off by default, and on or off the store behaves the same."""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from app.store import Store, StoreUnavailable
from app.store import db as store_db

from .airline_helpers import FakeClock

ENDLESS = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"


@pytest.fixture
def hook():
    """Install a recording hook for one test; always removed afterwards."""
    seen: list[store_db.OpTiming] = []
    previous = store_db.set_timing_hook(seen.append)
    assert previous is None
    yield seen
    store_db.set_timing_hook(None)


def scenario(path) -> list:
    """Every kind of store outcome; returns what an observer of the store sees (ids normalised)."""
    out: list = []
    store = Store(path, clock=FakeClock(), busy_timeout=0.1, operation_timeout=0.2)
    user = store.upsert_user("dev", "u", "U")["id"]
    conv = store.create_conversation(user, "t")["id"]
    store.add_message(user, conv, "user", "hello")
    out.append([(m["role"], m["text"]) for m in store.list_messages(user, conv)])
    # the body raises: rolled back, the exception passes through unchanged
    with pytest.raises(KeyError):
        with store.write() as con:
            con.execute("UPDATE users SET display_name = 'X' WHERE id = ?", (user,))
            raise KeyError("boom")
    out.append(store.get_user(user)["display_name"])
    with pytest.raises(KeyError):
        with store.read() as con:
            raise KeyError("boom")
    # a SQL error in the body
    with pytest.raises(sqlite3.OperationalError) as info:
        with store.write() as con:
            con.execute("SELECT * FROM no_such_table")
    out.append((type(info.value).__name__, str(info.value)))
    # lock wait past busy_timeout
    other = sqlite3.connect(store.path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreUnavailable) as info:
            store.create_conversation(user)
        out.append(str(info.value))
        out.append(len(store.list_conversations(user)))  # readers are not blocked (WAL)
    finally:
        other.execute("ROLLBACK")
        other.close()
    # operation deadline
    for ctx in (store.write, store.read):
        with pytest.raises(StoreUnavailable, match="interrupted"):
            with ctx() as con:
                con.execute(ENDLESS).fetchone()
    out.append(store.get_user(user)["display_name"])
    out.append(len(store.list_conversations(user)))
    return out


def test_the_hook_is_off_by_default():
    assert store_db._timing_hook is None


def test_with_the_hook_on_the_store_behaves_exactly_as_with_it_off(tmp_path, hook):
    on = scenario(tmp_path / "on.db")
    assert hook, "the hook saw the operations"
    store_db.set_timing_hook(None)
    off = scenario(tmp_path / "off.db")
    assert on == off


def test_outcomes_and_spans(tmp_path, hook):
    store = Store(tmp_path / "app.db", busy_timeout=0.1, operation_timeout=0.2)
    hook.clear()  # the constructor's migration is not timed
    user = store.upsert_user("dev", "u", "U")["id"]
    (w,) = hook
    assert w.kind == "write" and w.outcome == "commit"
    assert 0 <= w.wait <= w.total and 0 <= w.hold <= w.total

    hook.clear()
    store.get_user(user)
    (r,) = hook
    assert r.kind == "read" and r.outcome == "ok" and r.wait is not None and r.hold is not None

    hook.clear()
    with pytest.raises(ValueError):
        with store.write():
            raise ValueError
    assert [(t.kind, t.outcome) for t in hook] == [("write", "rollback")]
    assert hook[0].hold is not None

    hook.clear()
    other = sqlite3.connect(store.path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreUnavailable):
            store.create_conversation(user)
    finally:
        other.execute("ROLLBACK")
        other.close()
    (b,) = hook
    assert (b.kind, b.outcome, b.wait, b.hold) == ("write", "busy", None, None)
    assert b.total >= 0.09

    hook.clear()
    with pytest.raises(StoreUnavailable):
        with store.write() as con:
            con.execute(ENDLESS).fetchone()
    assert [(t.kind, t.outcome) for t in hook] == [("write", "timeout")]


def test_wait_measures_the_writer_lock(tmp_path, hook):
    store = Store(tmp_path / "app.db")
    user = store.upsert_user("dev", "u", "U")["id"]
    other = sqlite3.connect(store.path, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, lambda: other.execute("ROLLBACK"))
    release.start()
    hook.clear()
    try:
        store.create_conversation(user)
    finally:
        release.join()
        other.close()
    (w,) = hook
    assert w.outcome == "commit" and w.wait >= 0.2 and w.hold < w.wait


def test_a_failing_hook_changes_nothing(tmp_path):
    def broken(_):
        raise RuntimeError("hook bug")

    store_db.set_timing_hook(broken)
    try:
        store = Store(tmp_path / "app.db")
        user = store.upsert_user("dev", "u", "U")["id"]
        assert store.get_user(user)["display_name"] == "U"
    finally:
        assert store_db.set_timing_hook(None) is broken
    assert store_db._timing_hook is None


def test_overhead_when_off_is_one_lookup(tmp_path):
    """Not a benchmark: with the hook off no clock is read at all."""
    store = Store(tmp_path / "app.db")
    calls = []
    real = time.perf_counter
    try:
        time.perf_counter = lambda: calls.append(1) or real()  # type: ignore[assignment]
        store.upsert_user("dev", "u", "U")
        with store.read() as con:
            con.execute("SELECT 1").fetchone()
    finally:
        time.perf_counter = real  # type: ignore[assignment]
    assert calls == []
