"""L2 usage limits: messages per user per UTC day, the site's daily token budget, 429 before the
stream, nothing stored on refusal, no double pass under a race, day rollover, /api/me usage."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings as hsettings, strategies as st

from app.api import Settings, session
from app.api.limits import resets_at
from app.main import create_app
from app.store import SCHEMA_VERSION, Store, day_start
from app.store.records import OUTPUT_TOKENS_ESTIMATE

from .agent_helpers import Call, FakeLLM
from .airline_helpers import SECRET, FakeClock, World, _learn, assert_shape
from .test_http_api import SCHEMAS_ME, SECRET_KEY, parse_sse

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "contract" / "fixtures" / "rate_limited.json").read_text())
SCHEMA_429 = _learn(None, FIXTURE)
HELLO = ["Hello."]  # one LLM call of 110 tokens (FakeLLM usage)


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def app_client(world, *steps, default=HELLO, **settings):
    base = dict(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    base.update(settings)
    llm = FakeLLM(*steps, default=default)
    return TestClient(create_app(world.db, Settings(**base), clock=world.clock, llm=llm)), llm


def as_user(client, user_id):
    client.cookies.clear()
    client.cookies.set(session.COOKIE, session.issue(SECRET_KEY, user_id, 3600))


def send(client, conv, text="hello"):
    return client.post(f"/api/conversations/{conv}/messages", json={"text": text})


def assert_429(res, scope, resets):
    assert res.status_code == 429, res.text
    assert not res.headers["content-type"].startswith("text/event-stream")
    body = res.json()
    assert_shape(SCHEMA_429, body)
    err = body["error"]
    assert err["code"] == "rate_limited" and err["scope"] == scope and err["resets_at"] == resets
    assert "00:00 UTC" in err["message"]
    return err


def stored_user_messages(world, user, conv):
    return [m for m in world.store.list_messages(user, conv) if m["role"] == "user"]


# --- per-user limit -------------------------------------------------------------------------
def test_the_user_limit_refuses_before_the_stream_and_stores_nothing(world):
    c, llm = app_client(world, daily_messages_per_user=2)
    with c as client:
        as_user(client, world.alice)
        for _ in range(2):
            res = send(client, world.conv)
            assert res.status_code == 200 and parse_sse(res.text)[-1][0] == "done"
        calls = len(llm.requests)
        err = assert_429(send(client, world.conv, "third"), "user", "2026-11-24T00:00:00Z")
        assert "2 messages" in err["message"]
        assert len(llm.requests) == calls  # the model was not called
        assert [m["text"] for m in stored_user_messages(world, world.alice, world.conv)] == ["hello", "hello"]
        me = client.get("/api/me").json()
        assert_shape(SCHEMAS_ME, me)
        assert me["usage"] == {"messages_today": 2, "daily_limit": 2, "resets_at": "2026-11-24T00:00:00Z"}
        # a new conversation does not reset the count
        other = client.post("/api/conversations", json={}).json()["id"]
        assert_429(send(client, other), "user", "2026-11-24T00:00:00Z")
        # another user is not affected
        as_user(client, world.bob)
        assert send(client, world.bob_conv).status_code == 200
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1


def test_me_usage_counts_todays_messages(world):
    c, _ = app_client(world)
    with c as client:
        as_user(client, world.alice)
        assert client.get("/api/me").json()["usage"] == {
            "messages_today": 0, "daily_limit": 50, "resets_at": "2026-11-24T00:00:00Z"}
        for _ in range(3):
            send(client, world.conv)
        assert client.get("/api/me").json()["usage"]["messages_today"] == 3


def test_refused_bad_messages_do_not_use_a_slot(world):
    c, llm = app_client(world, daily_messages_per_user=1)
    with c as client:
        as_user(client, world.alice)
        for bad in ("", "   ", "x" * 5000):
            assert send(client, world.conv, bad).status_code == 400
        assert client.get("/api/me").json()["usage"]["messages_today"] == 0
        assert send(client, world.conv).status_code == 200
        assert send(client, world.conv).status_code == 429


def test_ownership_and_missing_llm_come_before_the_limit(world):
    c, _ = app_client(world, daily_messages_per_user=0)
    with c as client:
        as_user(client, world.bob)
        assert send(client, world.conv).json()["error"]["code"] == "not_found"
        as_user(client, world.alice)
        assert_429(send(client, world.conv), "user", "2026-11-24T00:00:00Z")
    base = dict(app_db=world.dir / "app.db", session_secret=SECRET_KEY, nonce_secret=SECRET,
                daily_messages_per_user=0, dev_user="demo", dev_password="pw")  # dev login on: alice is a dev user
    with TestClient(create_app(world.db, Settings(**base), clock=world.clock)) as client:  # no LLM
        as_user(client, world.alice)
        assert send(client, world.conv).json()["error"]["code"] == "llm_unavailable"


# --- day rollover (injected clock) ----------------------------------------------------------
def test_the_count_resets_at_utc_midnight(world):
    world.clock.now = datetime(2026, 11, 23, 23, 59, 58, tzinfo=timezone.utc)
    c, _ = app_client(world, daily_messages_per_user=1)
    with c as client:
        as_user(client, world.alice)
        assert send(client, world.conv).status_code == 200
        world.clock.advance(seconds=1)  # 23:59:59, same day
        assert_429(send(client, world.conv), "user", "2026-11-24T00:00:00Z")
        world.clock.advance(seconds=1)  # 00:00:00 next day
        assert client.get("/api/me").json()["usage"] == {
            "messages_today": 0, "daily_limit": 1, "resets_at": "2026-11-25T00:00:00Z"}
        assert send(client, world.conv).status_code == 200
        assert_429(send(client, world.conv), "user", "2026-11-25T00:00:00Z")


def test_reset_time_is_the_next_utc_midnight_in_any_zone():
    plus8 = timezone(timedelta(hours=8))
    assert resets_at(datetime(2026, 11, 24, 7, 0, tzinfo=plus8)) == "2026-11-24T00:00:00Z"  # 23:00 UTC 23rd
    assert resets_at(datetime(2026, 11, 24, 8, 0, tzinfo=plus8)) == "2026-11-25T00:00:00Z"
    assert resets_at(datetime(2026, 12, 31, 12, tzinfo=timezone.utc)) == "2027-01-01T00:00:00Z"


# --- site token budget ----------------------------------------------------------------------
def test_the_site_budget_counts_every_users_tokens(world):
    # alice's turn costs 2 LLM calls x 110 tokens = 220 >= budget 200
    c, _ = app_client(world, [Call("quote_route", origin="A", destination="D")], ["Here is your route."],
                      daily_token_budget=200)
    with c as client:
        as_user(client, world.alice)
        events = parse_sse(send(client, world.conv, "A to D").text)
        card = events[-1][1]["message"]["cards"][0]
        assert world.store.tokens_today() == 220
        as_user(client, world.bob)
        err = assert_429(send(client, world.bob_conv), "site", "2026-11-24T00:00:00Z")
        assert "budget" in err["message"]
        assert stored_user_messages(world, world.bob, world.bob_conv) == []
        assert client.get("/api/me").json()["usage"]["messages_today"] == 0
        # booking, tickets and history are never limited
        as_user(client, world.alice)
        assert_429(send(client, world.conv), "site", "2026-11-24T00:00:00Z")
        booked = client.post(f"/api/quotes/{card['id']}/book", json={"nonce": card["state"]["nonce"]})
        assert booked.status_code == 200, booked.text
        assert len(client.get("/api/tickets").json()["tickets"]) == 1
        assert client.get(f"/api/conversations/{world.conv}").status_code == 200
        assert client.get("/api/conversations").status_code == 200
        assert client.get(f"/api/quotes/{card['id']}/trace").status_code == 200
        # next UTC day: the budget is fresh
        world.clock.now = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
        assert world.store.tokens_today() == 0
        assert send(client, world.conv).status_code == 200


def test_the_user_scope_wins_when_both_are_reached(world):
    c, _ = app_client(world, daily_messages_per_user=1, daily_token_budget=100)
    with c as client:
        as_user(client, world.alice)
        assert send(client, world.conv).status_code == 200  # 110 tokens
        assert_429(send(client, world.conv), "user", "2026-11-24T00:00:00Z")
        as_user(client, world.bob)
        assert_429(send(client, world.bob_conv), "site", "2026-11-24T00:00:00Z")


def test_review_10_calls_without_a_token_count_are_estimated(world):
    """Review #10: a call with no reported usage (stream error, cancel, server without usage) is not
    free: request chars / 3 + a full output allowance."""
    from app.store.records import CHARS_PER_TOKEN_ESTIMATE

    request = {"messages": [{"role": "user", "content": "x" * 3000}]}
    world.store.add_llm_call(world.alice, world.conv, request, {"error": "stream broke"}, tokens=None)
    world.store.add_llm_call(world.alice, world.conv, {}, {}, tokens=7)
    stored = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
    assert world.store.tokens_today() == 7 + stored // CHARS_PER_TOKEN_ESTIMATE + OUTPUT_TOKENS_ESTIMATE


def test_review_10_unreported_calls_can_exhaust_the_site_budget(world):
    c, _ = app_client(world, daily_token_budget=OUTPUT_TOKENS_ESTIMATE)
    with c as client:
        as_user(client, world.alice)
        world.store.add_llm_call(world.alice, world.conv, {}, {"error": "x"}, tokens=None)
        assert_429(send(client, world.conv), "site", "2026-11-24T00:00:00Z")


# --- concurrency: count + reserve in one write transaction ---------------------------------
@pytest.mark.parametrize("limit", [1, 3])
def test_racing_reservations_never_exceed_the_limit(tmp_path, limit):
    world = World(tmp_path / "w")
    threads, start, results = 12, threading.Barrier(12), []
    lock = threading.Lock()

    def worker():
        store = Store(world.store.path, clock=world.clock)  # own connections, like separate requests
        start.wait()
        got = store.reserve_message(world.alice, world.conv, daily_limit=limit, token_budget=10**9)
        with lock:
            results.append(got)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    passed = [r for r, _ in results if r is not None]
    assert len(passed) == limit and len(set(passed)) == limit
    assert sorted(scope for r, scope in results if r is None) == ["user"] * (threads - limit)
    assert world.store.messages_today(world.alice) == limit


def test_racing_requests_at_the_limit_through_the_api(world):
    """Two messages sent at the same moment for the last slot: exactly one stream."""
    c, _ = app_client(world, daily_messages_per_user=1)
    with c as client:
        as_user(client, world.alice)
        conv2 = client.post("/api/conversations", json={}).json()["id"]
        start, statuses = threading.Barrier(2), []

        def go(conv):
            start.wait()
            statuses.append(send(client, conv).status_code)

        pool = [threading.Thread(target=go, args=(conv,)) for conv in (world.conv, conv2)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
        assert sorted(statuses) == [200, 429]
        assert client.get("/api/me").json()["usage"]["messages_today"] == 1


@hsettings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["alice", "bob"]), st.integers(0, 30 * 3600)), max_size=25),
       st.integers(0, 3))
def test_reservations_match_a_reference_count(tmp_path_factory, sends, limit):
    """Model check: at each moment a reservation passes iff that user's passes in the same UTC day
    are below the limit (reference: a plain per-day counter)."""
    world = World(tmp_path_factory.mktemp("w"))
    convs = {"alice": (world.alice, world.conv), "bob": (world.bob, world.bob_conv)}
    base = world.clock.now
    used: dict[tuple[str, object], int] = {}
    for who, offset in sorted(sends, key=lambda s: s[1]):
        world.clock.now = base + timedelta(seconds=offset)
        user, conv = convs[who]
        key = (who, day_start(world.clock.now))
        expect = used.get(key, 0) < limit
        got, scope = world.store.reserve_message(user, conv, daily_limit=limit, token_budget=10**9)
        assert (got is not None) == expect and (scope is None) == expect
        if expect:
            used[key] = used.get(key, 0) + 1
        assert world.store.messages_today(user) == used.get(key, 0)


# --- store / settings -----------------------------------------------------------------------
def test_reserve_checks_ownership(world):
    from app.store import NotFound

    with pytest.raises(NotFound):
        world.store.reserve_message(world.bob, world.conv, daily_limit=5, token_budget=10)
    assert world.store.messages_today(world.bob) == 0


def test_release_only_touches_the_users_own_reservation(world):
    rid, _ = world.store.reserve_message(world.alice, world.conv, daily_limit=5, token_budget=10)
    world.store.release_message(world.bob, rid)
    assert world.store.messages_today(world.alice) == 1
    world.store.release_message(world.alice, rid)
    assert world.store.messages_today(world.alice) == 0


def test_a_v1_database_is_migrated_and_keeps_its_data(tmp_path):
    path = tmp_path / "app.db"
    store = Store(path, clock=FakeClock())
    user = store.upsert_user("dev", "demo", "demo")
    con = sqlite3.connect(path)
    con.execute("DROP TABLE message_usage")
    con.execute("DROP INDEX llm_calls_by_time")
    con.execute("ALTER TABLE users DROP COLUMN session_epoch")
    con.execute("ALTER TABLE messages DROP COLUMN status")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    again = Store(path, clock=FakeClock())
    assert again.get_user(user["id"])["display_name"] == "demo"
    assert again.messages_today(user["id"]) == 0
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    con.close()


def test_settings_from_env():
    s = Settings.from_env({"DAILY_MESSAGES_PER_USER": "7", "DAILY_TOKEN_BUDGET": "2_500_000"})
    assert s.daily_messages_per_user == 7 and s.daily_token_budget == 2_500_000
    d = Settings.from_env({"DAILY_TOKEN_BUDGET": "lots"})
    assert d.daily_messages_per_user == 50 and d.daily_token_budget == 2_000_000
