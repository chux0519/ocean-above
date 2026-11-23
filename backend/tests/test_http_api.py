"""HTTP API (T5): every endpoint, auth, ownership and the contract fixtures' shapes."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import Settings, session
from app.main import create_app

from .agent_helpers import Call, FakeLLM
from .airline_helpers import SECRET, World, assert_card, assert_message, assert_shape, fixture_schemas

SCHEMAS = fixture_schemas()
FIXTURES = Path(__file__).resolve().parents[2] / "contract" / "fixtures"


def _learned(doc):
    from .airline_helpers import _learn

    return _learn(None, doc)


SCHEMAS_SUMMARY = _learned(json.loads((FIXTURES / "conversations.json").read_text())["conversations"][0])
SCHEMAS_ME = _learned(json.loads((FIXTURES / "me.json").read_text()))
SCHEMAS_CONFIG = _learned(json.loads((FIXTURES / "config.json").read_text()))
SCHEMAS_TICKETS = _learned(json.loads((FIXTURES / "tickets.json").read_text()))
SECRET_KEY = b"session-secret-0123456789"


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    """Whatever .env or environment the machine has, the app finds no API key unless a test injects a fake."""
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def make_settings(world, **kw):
    base = dict(app_db=world.dir / "app.db", dev_user="demo", dev_password="pw",
                session_secret=SECRET_KEY, nonce_secret=SECRET, ping_interval=0.01)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def client(world):
    with TestClient(create_app(world.db, make_settings(world), clock=world.clock)) as c:
        yield c


def as_user(client, user_id):
    client.cookies.clear()
    client.cookies.set(session.COOKIE, session.issue(SECRET_KEY, user_id, 3600))


def assert_error(res, status, code):
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code and body["error"]["message"]
    assert_shape(SCHEMAS["error"], body)


# --- public endpoints -----------------------------------------------------------------------
def test_health_and_config(client, world):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert set(res.json()) == {"status", "network_fingerprint"} and res.json()["status"] == "ok"
    assert res.json()["network_fingerprint"].startswith("sha256:")
    assert client.get("/health").json()["status"] == "ok"  # the old path stays
    cfg = client.get("/api/config")
    assert cfg.json() == {"dev_login": True, "google_client_id": None}
    assert_shape(SCHEMAS_CONFIG, cfg.json())


def test_config_without_dev_account(world):
    with TestClient(create_app(world.db, make_settings(world, dev_user="", dev_password=""))) as c:
        assert c.get("/api/config").json()["dev_login"] is False
        assert_error(c.post("/api/auth/dev", json={"username": "", "password": ""}), 401, "invalid_credentials")


def test_sse_ping(client):
    with client.stream("GET", "/api/debug/sse-ping") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        text = "".join(res.iter_text())
    events = [e for e in text.split("\n\n") if e]
    assert len(events) == 5
    for i, e in enumerate(events, 1):
        assert e == f'event: ping\ndata: {{"seq": {i}}}'


def test_sse_ping_first_event_is_immediate(world):
    settings = make_settings(world, ping_interval=30)
    with TestClient(create_app(world.db, settings)) as c:
        with c.stream("GET", "/api/debug/sse-ping") as res:
            t = time.monotonic()
            first = next(res.iter_text())
            assert time.monotonic() - t < 5 and first.startswith("event: ping")


# --- auth -----------------------------------------------------------------------------------
def test_login_me_logout(client):
    assert_error(client.get("/api/me"), 401, "unauthorized")
    assert_error(client.post("/api/auth/dev", json={"username": "demo", "password": "bad"}), 401, "invalid_credentials")
    assert_error(client.post("/api/auth/dev", json={"username": "x", "password": "pw"}), 401, "invalid_credentials")
    assert_error(client.post("/api/auth/dev", json={"username": "demo"}), 400, "bad_request")
    res = client.post("/api/auth/dev", json={"username": "demo", "password": "pw"})
    assert res.status_code == 200
    user = res.json()
    assert set(user) == {"id", "display_name", "auth_provider"} and user["auth_provider"] == "dev"
    assert user["id"].startswith("u_")
    cookie = res.headers["set-cookie"].lower()
    assert "oa_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    assert client.get("/api/me").json() == user
    assert_shape(SCHEMAS_ME, user)
    again = client.post("/api/auth/dev", json={"username": "demo", "password": "pw"}).json()
    assert again["id"] == user["id"]  # same account every time
    res = client.post("/api/auth/logout")
    assert res.json() == {"ok": True}
    assert_error(client.get("/api/me"), 401, "unauthorized")


def test_lone_surrogates_in_credentials_are_a_wrong_login_not_a_500(client):
    for body in ('{"username": "\\ud800", "password": "pw"}', '{"username": "demo", "password": "\\udc00x"}'):
        res = client.post("/api/auth/dev", content=body, headers={"content-type": "application/json"})
        assert_error(res, 401, "invalid_credentials")


def test_cookie_secure_flag(world):
    with TestClient(create_app(world.db, make_settings(world, cookie_secure=True))) as c:
        res = c.post("/api/auth/dev", json={"username": "demo", "password": "pw"})
        assert "secure" in res.headers["set-cookie"].lower()


def test_settings_from_env():
    s = Settings.from_env({"DEV_AUTH_USER": "u", "DEV_AUTH_PASSWORD": "p", "COOKIE_SECURE": "1", "APP_DB": "/x/a.db"})
    assert (s.dev_login, s.cookie_secure, str(s.app_db)) == (True, True, "/x/a.db")
    assert Settings.from_env({}).dev_login is False and Settings.from_env({"COOKIE_SECURE": ""}).cookie_secure is False


def test_bad_cookies_are_unauthorized(client, world):
    now = time.time()
    for value in ["", "junk", "a.b.c", session.issue(b"another-secret-0123456789", world.alice, 3600),
                  session.issue(SECRET_KEY, world.alice, -10),  # expired
                  session.issue(SECRET_KEY, "u_gone", 3600)]:  # signed but no such user
        client.cookies.clear()
        client.cookies.set(session.COOKIE, value)
        assert_error(client.get("/api/me"), 401, "unauthorized")
    assert session.verify(SECRET_KEY, session.issue(SECRET_KEY, "u_1", 60, now), now) == "u_1"


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/me", None), ("get", "/api/conversations", None), ("post", "/api/conversations", {}),
    ("get", "/api/conversations/c_x", None), ("post", "/api/conversations/c_x/messages", {"text": "hi"}),
    ("post", "/api/quotes/q_x/book", {"nonce": "n"}), ("get", "/api/quotes/q_x/trace", None),
    ("get", "/api/tickets", None),
])
def test_every_protected_endpoint_needs_login(client, method, path, body):
    res = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert_error(res, 401, "unauthorized")


# --- conversations --------------------------------------------------------------------------
def test_conversations_flow(client, world):
    as_user(client, world.alice)
    listing = client.get("/api/conversations").json()
    assert [c["id"] for c in listing["conversations"]] == [world.conv]  # Bob's is not here
    made = client.post("/api/conversations", json={})
    assert made.status_code == 200
    assert_shape(SCHEMAS_SUMMARY, made.json())
    assert client.post("/api/conversations").status_code == 200  # an empty body is fine
    assert_error(client.post("/api/conversations", content=b"[1]"), 400, "bad_request")
    assert_error(client.post("/api/conversations", content=b"{oops"), 400, "bad_request")
    ids = [c["id"] for c in client.get("/api/conversations").json()["conversations"]]
    assert ids[0] != world.conv and len(ids) == 3 and ids[-1] == world.conv  # newest first
    for c in client.get("/api/conversations").json()["conversations"]:
        assert_shape(SCHEMAS_SUMMARY, c)


def test_conversation_detail_with_cards(client, world):
    as_user(client, world.alice)
    world.airline.store.add_message(world.alice, world.conv, "user", "A to D please")
    quote = world.quote("A", "D")
    world.done(quote.cards, text="Here is your route.")
    res = client.get(f"/api/conversations/{world.conv}")
    assert res.status_code == 200
    body = res.json()
    assert_shape(SCHEMAS_SUMMARY, {k: v for k, v in body.items() if k != "messages"})
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    for m in body["messages"]:
        assert_message(m, SCHEMAS)
    card = body["messages"][1]["cards"][0]
    assert_card(card, SCHEMAS)
    assert card["state"]["bookable"] and card["state"]["nonce"]  # the server's current state
    assert body["preview"] == "Here is your route."


def test_unknown_conversation_is_404(client, world):
    as_user(client, world.alice)
    assert_error(client.get("/api/conversations/c_nope"), 404, "not_found")


# --- booking, trace, tickets ----------------------------------------------------------------
def test_book_trace_and_tickets(client, world):
    as_user(client, world.alice)
    assert client.get("/api/tickets").json() == {"tickets": []}
    quote_id, nonce = world.presented("A", "D")
    trace = client.get(f"/api/quotes/{quote_id}/trace")
    assert trace.status_code == 200
    assert_shape(SCHEMAS["trace"], trace.json())
    assert trace.json()["quote_id"] == quote_id

    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={}), 400, "bad_request")
    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={"nonce": "n_wrong"}), 409, "invalid_nonce")
    res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out) == {"ticket", "message"}
    assert_shape(SCHEMAS["booking_ok"], {k: None for k in out})
    assert_card(out["ticket"], SCHEMAS)
    assert_message(out["message"], SCHEMAS)
    assert out["message"]["role"] == "system" and out["ticket"]["quote_id"] == quote_id
    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}), 409, "already_booked")

    tickets = client.get("/api/tickets").json()
    assert [t["id"] for t in tickets["tickets"]] == [out["ticket"]["id"]]
    assert_card(tickets["tickets"][0], SCHEMAS)
    assert_shape(SCHEMAS_TICKETS, tickets)
    detail = client.get(f"/api/conversations/{world.conv}").json()
    assert detail["messages"][-1]["id"] == out["message"]["id"]


def test_book_maps_each_error_code(client, world):
    as_user(client, world.alice)
    # not_presented: quoted but never shown
    q = world.quote("A", "D").data["quote_id"]
    assert_error(client.post(f"/api/quotes/{q}/book", json={"nonce": "n_x"}), 409, "not_presented")
    # expired
    quote_id, nonce = world.presented("A", "C")
    world.clock.advance(minutes=16)
    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}), 409, "expired")
    # stale: the data changed after the quote
    world.clock.now = world.clock.now.replace(hour=9)
    quote_id, nonce = world.presented("B", "D")
    world.edit_db("DELETE FROM hub_links WHERE hub_a = 'H3' AND hub_b = 'H4'")
    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}), 409, "stale")
    # data_unavailable
    quote_id, nonce = world.presented("A", "B")
    world.db.rename(world.dir / "moved.db")
    res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})
    assert_error(res, 503, "data_unavailable")


def test_book_while_a_turn_runs_is_conversation_busy(client, world, monkeypatch):
    import app.airline.service as service

    as_user(client, world.alice)
    quote_id, nonce = world.presented("A", "D")
    monkeypatch.setattr(service.Airline.book, "__kwdefaults__", {"lock_timeout": 0.05})
    with client.app.state.airline.locks.hold(world.conv):  # a chat turn is running
        res = client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce})
    assert_error(res, 409, "conversation_busy")
    assert world.ticket_count() == 0


# --- ownership ------------------------------------------------------------------------------
def test_other_users_objects_are_404(client, world):
    quote_id, nonce = world.presented("A", "D", user=world.alice, conv=world.conv)
    as_user(client, world.bob)
    assert_error(client.get(f"/api/conversations/{world.conv}"), 404, "not_found")
    assert_error(client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"}), 404, "not_found")
    assert_error(client.get(f"/api/quotes/{quote_id}/trace"), 404, "not_found")
    assert_error(client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}), 404, "not_found")
    assert client.get("/api/tickets").json() == {"tickets": []}
    assert [c["id"] for c in client.get("/api/conversations").json()["conversations"]] == [world.bob_conv]
    # nothing happened to Alice's quote
    assert world.status(quote_id) == "presented" and world.ticket_count() == 0
    as_user(client, world.alice)
    assert client.post(f"/api/quotes/{quote_id}/book", json={"nonce": nonce}).status_code == 200
    as_user(client, world.bob)  # Alice's ticket is invisible to Bob
    assert client.get("/api/tickets").json() == {"tickets": []}


def test_unknown_quote_is_404(client, world):
    as_user(client, world.alice)
    assert_error(client.get("/api/quotes/q_nope/trace"), 404, "not_found")
    assert_error(client.post("/api/quotes/q_nope/book", json={"nonce": "n"}), 404, "not_found")


# --- messages (SSE) -------------------------------------------------------------------------
def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            head, data = block.split("\n", 1)
            assert head.startswith("event: ") and data.startswith("data: "), block
            out.append((head[len("event: "):], json.loads(data[len("data: "):])))
    return out


def chat_client(world, *steps, **kw):
    llm = FakeLLM(*steps, **kw)
    return TestClient(create_app(world.db, make_settings(world), clock=world.clock, llm=llm)), llm


QUOTE_TURN = [[Call("quote_route", origin="A", destination="D")], ["Here is your route."]]


def test_message_stream_then_book_the_presented_card(world):
    c, llm = chat_client(world, *QUOTE_TURN)
    with c as client:
        as_user(client, world.alice)
        res = client.post(f"/api/conversations/{world.conv}/messages", json={"text": "A to D please"})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["cache-control"] == "no-cache, no-transform"
        events = parse_sse(res.text)
        kinds = [k for k, _ in events]
        assert kinds[0] == "user_message" and kinds[-1] == "done" and kinds.count("done") == 1
        assert "card" in kinds and "delta" in kinds and kinds.index("card") < kinds.index("done")
        assert_message(events[0][1], SCHEMAS)
        assert events[0][1]["role"] == "user" and events[0][1]["text"] == "A to D please"
        for kind, data in events:
            if kind == "card":
                assert_card(data["card"], SCHEMAS)
                assert data["card"]["state"]["bookable"] is False  # not before `done`
        message = events[-1][1]["message"]
        assert_message(message, SCHEMAS)
        assert message["role"] == "assistant" and message["text"] == "Here is your route."
        card = message["cards"][0]
        assert card["type"] == "quote" and card["state"]["bookable"] and card["state"]["nonce"]
        # the click: the same Airline serves the agent and the booking route
        booked = client.post(f"/api/quotes/{card['id']}/book", json={"nonce": card["state"]["nonce"]})
        assert booked.status_code == 200, booked.text
        assert booked.json()["ticket"]["quote_id"] == card["id"]
        detail = client.get(f"/api/conversations/{world.conv}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "system"]
        assert detail["messages"][1]["cards"][0]["state"]["status"] == "booked"
    assert world.ticket_count() == 1


def test_the_agent_and_the_booking_route_share_one_airline(world):
    c, _ = chat_client(world, *QUOTE_TURN)
    with c as client:
        assert client.app.state.agent is not None
        assert client.app.state.airline.locks is client.app.state.agent._locks


def test_message_to_another_users_conversation_is_404(world):
    c, llm = chat_client(world, *QUOTE_TURN)
    with c as client:
        as_user(client, world.bob)
        assert_error(client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"}), 404, "not_found")
        assert_error(client.post("/api/conversations/c_nope/messages", json={"text": "hi"}), 404, "not_found")
        client.cookies.clear()
        assert_error(client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"}), 401, "unauthorized")
    assert llm.requests == []  # the model was never called
    assert world.store.list_messages(world.alice, world.conv) == []


def test_bad_message_bodies_are_400_before_the_stream_starts(world):
    c, llm = chat_client(world, *QUOTE_TURN)
    with c as client:
        as_user(client, world.alice)
        url = f"/api/conversations/{world.conv}/messages"
        assert_error(client.post(url, json={}), 400, "bad_request")
        assert_error(client.post(url, json={"text": ""}), 400, "bad_request")
        assert_error(client.post(url, json={"text": "   "}), 400, "bad_request")
        assert_error(client.post(url, json={"text": "x" * 5000}), 400, "bad_request")
        assert_error(client.post(url, json={"text": 5}), 400, "bad_request")
    assert llm.requests == [] and world.store.list_messages(world.alice, world.conv) == []


def test_without_an_llm_key_only_the_message_route_is_unavailable(client, world):
    assert client.app.state.agent is None
    as_user(client, world.alice)
    assert_error(client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"}), 503, "llm_unavailable")
    # ownership still comes first
    as_user(client, world.bob)
    assert_error(client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hi"}), 404, "not_found")
    assert client.get("/api/conversations").status_code == 200 and client.get("/api/tickets").status_code == 200


def test_llm_failure_is_an_sse_error_event(world):
    from app.agent import LLMError

    c, _ = chat_client(world, default=LLMError("down", retryable=False))
    with c as client:
        as_user(client, world.alice)
        res = client.post(f"/api/conversations/{world.conv}/messages", json={"text": "hello"})
        events = parse_sse(res.text)
        assert [k for k, _ in events] == ["user_message", "error"]
        assert events[-1][1]["code"] == "llm_unavailable"


def test_a_key_in_the_environment_builds_the_real_client(world, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    with TestClient(create_app(world.db, make_settings(world))) as client:
        assert client.app.state.agent is not None  # built, never called


def test_unknown_path_and_method_use_the_error_body(client):
    assert_error(client.get("/api/nope"), 404, "not_found")
    assert_error(client.delete("/api/config"), 405, "method_not_allowed")

