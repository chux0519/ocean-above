"""Regression tests for docs/iterations/05-launch/review-auth.md, one or more per finding
(`test_review_<n>_…`). #1 key properties and #10 store estimate live in test_auth.py / test_limits.py."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import Settings, session
from app.api.google import GoogleVerifier, KeysUnavailable
from app.api.guards import MAX_BODY_BYTES, ApiGuard, same_origin
from app.api.logins import Throttle, client_ip, parse_networks, throttle_key
from app.main import create_app

from .airline_helpers import SECRET, World
from .test_auth import CLIENT_ID, KEY_A, KEY_B, KEY_EVIL, NOW, Clock, Keys, jwk, rejects, token, verifier
from .test_http_api import assert_error


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def make_settings(world, **kw):
    base = dict(app_db=world.dir / "app.db", dev_user="", dev_password="", session_secret=b"s" * 32,
                nonce_secret=SECRET, google_client_id=CLIENT_ID, invite_codes=["code-one", "code-two"])
    base.update(kw)
    return Settings(**base)


def app_client(world, peer=("203.0.113.7", 50000), **kw):
    return TestClient(create_app(world.db, make_settings(world, **kw), clock=world.clock), client=peer)


def invite(c, code, name="Ada"):
    return c.post("/api/auth/invite", json={"code": code, "name": name})


# --- #1 invite account key must not depend on the code's position -------------------------------
def test_review_1_removing_a_code_does_not_move_accounts(world):
    with app_client(world, invite_codes=["code-a", "code-b", "code-c"]) as c:
        b = invite(c, "code-b", "alice").json()
        b_conv = c.post("/api/conversations", json={}).json()["id"]
        c_user = invite(c, "code-c", "alice").json()
    # code-a leaked and is removed: positions shift, accounts must not
    with app_client(world, invite_codes=["code-b", "code-c"]) as c:
        again_c = invite(c, "code-c", "alice").json()
        assert again_c["id"] == c_user["id"]
        assert_error(c.get(f"/api/conversations/{b_conv}"), 404, "not_found")  # not b's data
        again_b = invite(c, "code-b", "alice").json()
        assert again_b["id"] == b["id"] and c.get(f"/api/conversations/{b_conv}").status_code == 200
    with app_client(world, invite_codes=["code-c", "code-b"]) as c:  # reordered
        assert invite(c, "code-b", "x").json()["id"] == b["id"]


# --- #2 JWKS: keep last good keys, bounded refetch, single flight, no fetch under the lock --------
def test_review_2_failed_fetches_are_rate_limited_and_back_off():
    keys = Keys(jwk(KEY_A, "a"))
    keys.fail = True
    clock = Clock()
    v, _ = verifier(keys, clock, min_refetch=60, max_backoff=240)
    for _ in range(5):
        with pytest.raises(KeysUnavailable):
            v.verify(token())
    assert keys.fetches == 1  # not one per request
    clock.t += 59
    with pytest.raises(KeysUnavailable):
        v.verify(token())
    assert keys.fetches == 1
    attempts = []
    for _ in range(6):  # wait after the n-th failure: 60, 120, 240 (cap), 240, ...
        clock.t += 0.5
        while True:
            before = keys.fetches
            with pytest.raises(KeysUnavailable):
                v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60))
            if keys.fetches > before:
                attempts.append(clock.t)
                break
            clock.t += 1
    gaps = [round(b - a) for a, b in zip(attempts, attempts[1:])]
    assert gaps == [120, 240, 240, 240, 240]  # doubling from min_refetch, capped at max_backoff
    keys.fail = False
    clock.t += 241
    assert v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60)).sub


def test_review_2_a_failed_refetch_keeps_the_cached_keys():
    keys = Keys(jwk(KEY_A, "a"))
    clock = Clock()
    v, _ = verifier(keys, clock)
    good = token()
    assert v.verify(good).sub
    clock.t += 61
    keys.fail = True
    rejects(v, token(private=KEY_EVIL, kid="forged"), "unknown kid")  # forces a refetch, which fails
    assert keys.fetches == 2
    assert v.verify(good).sub  # the valid key is still there


def test_review_2_stale_keys_are_used_while_google_is_down_then_expire():
    keys = Keys(jwk(KEY_A, "a"))
    clock = Clock()
    v, _ = verifier(keys, clock, ttl=3600, max_stale=24 * 3600)
    v.verify(token())
    keys.fail = True
    clock.t += 3601  # past the ttl, fetch fails → stale keys still verify
    assert v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60)).sub
    clock.t = NOW + 24 * 3600  # older than max_stale → nothing usable → 503
    with pytest.raises(KeysUnavailable):
        v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60))


def test_review_2_the_fetch_runs_outside_the_lock_and_only_once():
    started, release = threading.Event(), threading.Event()
    doc = {"keys": [jwk(KEY_A, "a")]}
    calls = []

    def slow_fetch():
        calls.append(1)
        if len(calls) >= 2:  # the refresh after the ttl hangs (Google slow)
            started.set()
            release.wait(5)
        return doc

    clock = Clock()
    v = GoogleVerifier(CLIENT_ID, fetch=slow_fetch, clock=clock, ttl=3600)
    v.verify(token())
    clock.t += 3601
    tok = token(iat=int(clock.t), exp=int(clock.t) + 60)
    t1 = threading.Thread(target=v.verify, args=(tok,))
    t1.start()
    assert started.wait(2)
    # while the fetch hangs, a request with a cached kid is answered at once and starts no fetch
    t0 = time.monotonic()
    assert v.verify(tok).sub
    assert time.monotonic() - t0 < 0.5 and len(calls) == 2
    release.set()
    t1.join(2)
    assert not t1.is_alive() and len(calls) == 2


def test_review_2_concurrent_cold_requests_share_one_fetch():
    gate = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        gate.wait(2)
        return {"keys": [jwk(KEY_A, "a")]}

    v = GoogleVerifier(CLIENT_ID, fetch=fetch, clock=Clock())
    results = []
    threads = [threading.Thread(target=lambda: results.append(v.verify(token()).sub)) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(3)
    assert len(calls) == 1 and results == ["1000001"] * 5


def test_review_2_rotation_still_works():
    keys = Keys(jwk(KEY_A, "a"))
    clock = Clock()
    v, _ = verifier(keys, clock)
    v.verify(token())
    clock.t += 61
    keys.doc = {"keys": [jwk(KEY_B, "b")]}
    assert v.verify(token(private=KEY_B, kid="b")).sub and keys.fetches == 2


# --- #3 / #6 one account per code -----------------------------------------------------------
def test_review_3_one_code_is_one_account_and_one_daily_limit(world):
    with app_client(world, daily_messages_per_user=1) as c:
        ids = {invite(c, "code-one", f"name-{i}").json()["id"] for i in range(5)}
        assert len(ids) == 1  # new names do not create new accounts (no fresh limits)
        (uid,) = ids
        conv = c.post("/api/conversations", json={}).json()["id"]
        store = c.app.state.store
        assert store.reserve_message(uid, conv, daily_limit=1, token_budget=10**9)[0] is not None
        invite(c, "code-one", "someone-else")
        me = c.get("/api/me").json()
        assert me["id"] == uid and me["usage"]["messages_today"] == 1


def test_review_6_the_name_is_not_a_credential(world):
    """Same code → same account whatever the name; a different code never reaches it."""
    with app_client(world) as c:
        a = invite(c, "code-one", "Grace").json()
        assert invite(c, "code-two", "Grace").json()["id"] != a["id"]
        assert invite(c, "code-one", "GRACE").json()["id"] == a["id"]


# --- #4 CF-Connecting-IP only when switched on --------------------------------------------------
def test_review_4_cf_header_is_ignored_unless_trusted():
    trusted = parse_networks(None)
    assert client_ip("172.18.0.2", {"cf-connecting-ip": "8.8.8.8"}, trusted) == "172.18.0.2"
    assert client_ip("172.18.0.2", {"cf-connecting-ip": "8.8.8.8"}, trusted, trust_cf=True) == "8.8.8.8"


def test_review_4_spoofed_cf_headers_do_not_reset_the_throttle(world):
    with app_client(world, peer=("172.18.0.2", 1)) as c:  # a non-Cloudflare proxy in front
        c.app.state.login_throttle = Throttle(10, 60, clock=Clock(0.0))
        for i in range(10):
            c.post("/api/auth/invite", json={"code": "nope", "name": "x"},
                   headers={"cf-connecting-ip": f"198.51.100.{i}"})
        res = c.post("/api/auth/invite", json={"code": "code-one", "name": "x"},
                     headers={"cf-connecting-ip": "198.51.100.200"})
        assert_error(res, 429, "rate_limited")


# --- #5 IPv6 /64 bucket ---------------------------------------------------------------------------
def test_review_5_ipv6_addresses_share_their_64():
    assert throttle_key("2001:db8:1:2::1") == throttle_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert throttle_key("2001:db8:1:3::1") != throttle_key("2001:db8:1:2::1")
    assert throttle_key("203.0.113.5") == "203.0.113.5"
    assert throttle_key("::ffff:203.0.113.5") == "203.0.113.5"
    assert throttle_key("unknown") == "unknown"


def test_review_5_rotating_ipv6_addresses_hits_the_throttle(world):
    with app_client(world, peer=("172.18.0.2", 1), trust_cf_connecting_ip=True) as c:
        c.app.state.login_throttle = Throttle(10, 60, clock=Clock(0.0))
        for i in range(10):
            c.post("/api/auth/invite", json={"code": "nope", "name": "x"},
                   headers={"cf-connecting-ip": f"2001:db8:0:1::{i + 1:x}"})
        assert_error(c.post("/api/auth/invite", json={"code": "code-one", "name": "x"},
                            headers={"cf-connecting-ip": "2001:db8:0:1:abcd::1"}), 429, "rate_limited")
        assert c.post("/api/auth/invite", json={"code": "code-one", "name": "x"},
                      headers={"cf-connecting-ip": "2001:db8:0:2::1"}).status_code == 200


# --- #7 sessions can be revoked -------------------------------------------------------------------
def test_review_7_logout_revokes_copied_cookies(world):
    with app_client(world) as c:
        invite(c, "code-one")
        stolen = c.cookies.get(session.COOKIE)
        other_device = invite(c, "code-one") and c.cookies.get(session.COOKIE)
        assert c.get("/api/me").status_code == 200
        assert c.post("/api/auth/logout").json() == {"ok": True}
        for cookie in (stolen, other_device):
            c.cookies.clear()
            c.cookies.set(session.COOKIE, cookie)
            assert_error(c.get("/api/me"), 401, "unauthorized")
        # signing in again works and gets a fresh epoch
        invite(c, "code-one")
        assert c.get("/api/me").status_code == 200


def test_review_7_logout_with_a_stale_cookie_does_not_log_out_the_new_session(world):
    with app_client(world) as c:
        invite(c, "code-one")
        old = c.cookies.get(session.COOKIE)
        c.post("/api/auth/logout")
        invite(c, "code-one")
        fresh = c.cookies.get(session.COOKIE)
        c.cookies.clear()
        c.cookies.set(session.COOKIE, old)
        c.post("/api/auth/logout")  # replaying a revoked cookie revokes nothing
        c.cookies.clear()
        c.cookies.set(session.COOKIE, fresh)
        assert c.get("/api/me").status_code == 200


def test_review_7_removing_a_code_ends_its_sessions(world):
    with app_client(world) as c:
        invite(c, "code-one")
        cookie = c.cookies.get(session.COOKIE)
    with app_client(world, invite_codes=["code-two"]) as c:
        c.cookies.set(session.COOKIE, cookie)
        assert_error(c.get("/api/me"), 401, "unauthorized")
    with app_client(world) as c:  # code put back: the account (and its session) is valid again
        c.cookies.set(session.COOKIE, cookie)
        assert c.get("/api/me").status_code == 200


def test_review_7_turning_logins_off_ends_their_sessions(world):
    with app_client(world, dev_user="demo", dev_password="pw") as c:
        assert c.post("/api/auth/dev", json={"username": "demo", "password": "pw"}).status_code == 200
        dev_cookie = c.cookies.get(session.COOKIE)
    with app_client(world) as c:  # dev login off
        c.cookies.set(session.COOKIE, dev_cookie)
        assert_error(c.get("/api/me"), 401, "unauthorized")
    with app_client(world, dev_user="demo", dev_password="pw") as c:
        c.cookies.set(session.COOKIE, dev_cookie)
        assert c.get("/api/me").status_code == 200
    google_user = world.store.upsert_user("google", "g-1", "G")
    cookie = session.issue(b"s" * 32, google_user["id"], 3600)
    with app_client(world, google_client_id=None) as c:
        c.cookies.set(session.COOKIE, cookie)
        assert_error(c.get("/api/me"), 401, "unauthorized")
    with app_client(world) as c:
        c.cookies.set(session.COOKIE, cookie)
        assert c.get("/api/me").status_code == 200


def test_review_7_old_format_cookies_are_rejected(world):
    alice = world.alice
    legacy = f"{alice}.{int(time.time()) + 3600}"  # L1 form: user.expires.sig
    legacy += "." + hmac.new(b"s" * 32, legacy.encode(), hashlib.sha256).hexdigest()[:40]
    with app_client(world, dev_user="demo", dev_password="pw") as c:
        c.cookies.set(session.COOKIE, legacy)
        assert_error(c.get("/api/me"), 401, "unauthorized")


# --- #8 non-ASCII cookie → 401, not 500 -----------------------------------------------------------
@pytest.mark.parametrize("value", ["u_1.0.1.é", "u_1.0.1795000000." + "é" * 40, "ü.0.9999999999.abc", "u_1.١.9.x"])
def test_review_8_non_ascii_cookies_are_unauthorized(world, value):
    assert session.verify(b"k", value) is None
    with app_client(world) as c:
        res = c.get("/api/me", headers={"cookie": f"{session.COOKIE}={value}".encode("latin-1", "replace")})
        assert_error(res, 401, "unauthorized")


# --- #9 non-finite or too long time claims -------------------------------------------------------
@pytest.mark.parametrize("claim,value", [("exp", math.nan), ("exp", math.inf), ("iat", math.nan),
                                         ("iat", -math.inf)])
def test_review_9_non_finite_time_claims_are_rejected(claim, value):
    v, _ = verifier()
    rejects(v, token(**{claim: value}), "time claims")


def test_review_9_lifetime_over_a_day_is_rejected():
    v, _ = verifier()
    rejects(v, token(exp=int(NOW) + 25 * 3600), "lifetime")
    assert v.verify(token(exp=int(NOW) + 3600)).sub


# --- #11 request size -----------------------------------------------------------------------------
def test_review_11_oversized_bodies_are_refused_before_parsing(world):
    with app_client(world, dev_user="demo", dev_password="pw") as c:
        big = json.dumps({"code": "x" * (MAX_BODY_BYTES + 1), "name": "Ada"})
        assert_error(c.post("/api/auth/invite", content=big, headers={"content-type": "application/json"}),
                     413, "payload_too_large")
        # chunked (no Content-Length) is counted while reading
        def chunks():
            for _ in range(MAX_BODY_BYTES // 1024 + 2):
                yield b" " * 1024
        assert_error(c.post("/api/auth/invite", content=chunks(), headers={"content-type": "application/json"}),
                     413, "payload_too_large")
        # under the body limit but over a field bound: 400, and no throttle slot is the issue here
        for path, body in [("/api/auth/invite", {"code": "x" * 201, "name": "Ada"}),
                           ("/api/auth/invite", {"code": "code-one", "name": "x" * 201}),
                           ("/api/auth/dev", {"username": "u" * 201, "password": "p"}),
                           ("/api/auth/google", {"credential": "x" * 8193})]:
            assert_error(c.post(path, json=body), 400, "bad_request")
        # normal requests still work, including the body being replayed to the route
        assert c.post("/api/auth/dev", json={"username": "demo", "password": "pw"}).status_code == 200
        assert c.post("/api/conversations", json={}).status_code == 200


def test_review_11_message_text_bound(world):
    with app_client(world, dev_user="demo", dev_password="pw") as c:
        c.post("/api/auth/dev", json={"username": "demo", "password": "pw"})
        conv = c.post("/api/conversations", json={}).json()["id"]
        assert_error(c.post(f"/api/conversations/{conv}/messages", json={"text": "x" * 8001}), 400, "bad_request")


def test_review_11_guard_passes_disconnects_and_other_paths():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["path"], await receive(), await receive()))

    guard = ApiGuard(app)
    messages = iter([{"type": "http.request", "body": b"{}", "more_body": False}, {"type": "http.disconnect"}])

    async def receive():
        return next(messages)

    async def send(_):
        raise AssertionError("no response expected")

    scope = {"type": "http", "path": "/api/x", "method": "POST", "headers": []}
    asyncio.run(guard(scope, receive, send))
    assert seen == [("/api/x", {"type": "http.request", "body": b"{}", "more_body": False},
                     {"type": "http.disconnect"})]


# --- #12 Secure warning and cross-site writes -----------------------------------------------------
def test_review_12_insecure_cookie_with_public_logins_is_warned(world, caplog):
    assert Settings(google_client_id=CLIENT_ID).warnings()
    assert Settings(invite_codes=["a"]).warnings()
    assert not Settings(google_client_id=CLIENT_ID, cookie_secure=True).warnings()
    assert not Settings(dev_user="d", dev_password="p").warnings()  # local dev
    with app_client(world):
        pass
    assert "COOKIE_SECURE" in caplog.text


@pytest.mark.parametrize("headers,ok", [
    ({}, True),  # curl / server-side
    ({"sec-fetch-site": "same-origin"}, True),
    ({"sec-fetch-site": "none"}, True),  # typed into the address bar / bookmark
    ({"sec-fetch-site": "cross-site"}, False),
    ({"sec-fetch-site": "same-site", "origin": "https://other.example.net"}, False),  # sibling subdomain
    ({"origin": "https://ocean.example.net"}, True),
    ({"origin": "https://OCEAN.example.net"}, True),
    ({"origin": "https://evil.example.net"}, False),
    ({"origin": "null"}, False),
    ({"origin": "https://ocean.example.net:8443"}, False),
    ({"origin": "http://192.168.1.5:3100", "x-forwarded-host": "192.168.1.5:3100"}, True),  # Next dev rewrite
    ({"origin": "https://evil.example.net", "x-forwarded-host": "ocean.example.net"}, False),
])
def test_review_12_cross_site_writes_are_refused(headers, ok):
    assert same_origin("POST", {"host": "ocean.example.net", **headers}) is ok
    assert same_origin("GET", {"host": "ocean.example.net", **headers}) is True


def test_review_12_cross_site_post_over_http(world):
    with app_client(world) as c:
        invite(c, "code-one")
        res = c.post("/api/conversations", json={}, headers={"sec-fetch-site": "same-site",
                                                              "origin": "https://other.example.net"})
        assert_error(res, 403, "forbidden_origin")
        res = c.post("/api/conversations", content=b"", headers={"origin": "https://evil.example.net"})
        assert_error(res, 403, "forbidden_origin")
        assert c.get("/api/conversations", headers={"sec-fetch-site": "cross-site"}).status_code == 200
        assert c.post("/api/conversations", json={}, headers={"sec-fetch-site": "same-origin",
                                                               "origin": "http://testserver"}).status_code == 200
        # login endpoints too (login CSRF)
        assert_error(c.post("/api/auth/invite", json={"code": "code-two", "name": "x"},
                            headers={"sec-fetch-site": "cross-site"}), 403, "forbidden_origin")
