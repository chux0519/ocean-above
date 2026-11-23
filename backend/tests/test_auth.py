"""L1 auth: Google ID token verification, invite codes, dev switch, login throttle, client IP,
config / me fields, conversation title fallback, and that no secret reaches the logs."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.api import Settings
from app.api.google import ISSUERS, LEEWAY_SECONDS, GoogleVerifier, InvalidToken, KeysUnavailable
from app.api.logins import InviteCodes, Throttle, account_key, clean_name, client_ip, parse_networks, throttle_key
from app.main import create_app

from .airline_helpers import SECRET, World, assert_shape
from .test_http_api import SCHEMAS_CONFIG, SCHEMAS_ME, SCHEMAS_SUMMARY, assert_error

CLIENT_ID = "123-example.apps.googleusercontent.com"
NOW = 1_795_000_000.0  # 2026-11-17, any fixed instant


# --- key material and tokens --------------------------------------------------------------------
def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY_A, KEY_B, KEY_EVIL = _rsa(), _rsa(), _rsa()


def jwk(private, kid):
    d = RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    return {**d, "kid": kid, "alg": "RS256", "use": "sig"}


def claims(**over):
    base = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1000001",
            "email": "ada@example.com", "email_verified": True, "name": "Ada Example",
            "iat": int(NOW) - 10, "exp": int(NOW) + 3600}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def token(private=KEY_A, kid="a", alg="RS256", **over):
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims(**over), private, algorithm=alg, headers=headers)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Keys:
    """A fake JWKS endpoint: counts fetches, can fail, can rotate."""

    def __init__(self, *keys):
        self.doc = {"keys": list(keys)}
        self.fetches = 0
        self.fail = False

    def __call__(self):
        self.fetches += 1
        if self.fail:
            raise OSError("network down")
        return self.doc


def verifier(keys=None, clock=None, **kw):
    keys = keys if keys is not None else Keys(jwk(KEY_A, "a"))
    return GoogleVerifier(CLIENT_ID, fetch=keys, clock=clock or Clock(), **kw), keys


def rejects(v, tok, reason=None):
    with pytest.raises(InvalidToken) as e:
        v.verify(tok)
    if reason:
        assert str(e.value) == reason, str(e.value)


# --- GoogleVerifier ----------------------------------------------------------------------------
def test_a_valid_token_gives_the_identity():
    v, _ = verifier()
    who = v.verify(token())
    assert (who.sub, who.email, who.name) == ("1000001", "ada@example.com", "Ada Example")
    for iss in ISSUERS:
        assert v.verify(token(iss=iss)).sub == "1000001"


def test_forged_signature_is_rejected():
    v, _ = verifier()
    rejects(v, token(private=KEY_EVIL, kid="a"), "signature")
    # a valid token with one payload byte changed
    head, body, sig = token().split(".")
    other = jwt.utils.base64url_encode(json.dumps(claims(sub="1000002")).encode()).decode()
    rejects(v, f"{head}.{other}.{sig}", "signature")


def test_wrong_audience_and_issuer_are_rejected():
    v, _ = verifier()
    rejects(v, token(aud="someone-else.apps.googleusercontent.com"), "aud")
    rejects(v, token(aud=[CLIENT_ID + "x"]), "aud")
    rejects(v, token(iss="https://evil.example.com"), "iss")
    rejects(v, token(iss="accounts.google.com.evil.example"), "iss")


def test_expiry_with_a_small_leeway():
    clock = Clock()
    v, _ = verifier(clock=clock)
    tok = token(exp=int(NOW) + 100)
    assert v.verify(tok)
    clock.t = NOW + 100 + LEEWAY_SECONDS - 1
    assert v.verify(tok)  # within the leeway
    clock.t = NOW + 100 + LEEWAY_SECONDS
    rejects(v, tok, "expired")
    clock.t = NOW
    rejects(v, token(iat=int(NOW) + LEEWAY_SECONDS + 5), "issued in the future")


def test_a_replayed_token_is_fine_while_valid_but_still_checked():
    clock = Clock()
    v, _ = verifier(clock=clock)
    tok = token(exp=int(NOW) + 60)
    assert v.verify(tok).sub == v.verify(tok).sub  # stateless: the same token twice
    clock.t = NOW + 60 + LEEWAY_SECONDS
    rejects(v, tok, "expired")  # replay after expiry fails


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_email_must_be_verified(value):
    v, _ = verifier()
    rejects(v, token(email_verified=value), "email not verified")


def test_required_claims_and_malformed_tokens():
    v, _ = verifier()
    for missing in ("exp", "iat", "sub", "iss", "aud"):
        tok = jwt.encode({k: x for k, x in claims().items() if k != missing}, KEY_A, "RS256", headers={"kid": "a"})
        rejects(v, tok)
    rejects(v, token(sub=""), "sub")
    rejects(v, token(exp="soon"))
    for bad in ("", "abc", "a.b.c", "x" * 9000):
        rejects(v, bad)
    rejects(v, token(kid=None), "no kid")


def test_algorithm_confusion_is_rejected():
    v, _ = verifier()
    pem = KEY_A.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    # HS256 keyed with the public key, and alg=none
    head = jwt.utils.base64url_encode(json.dumps({"alg": "HS256", "kid": "a", "typ": "JWT"}).encode()).decode()
    body = jwt.utils.base64url_encode(json.dumps(claims()).encode()).decode()
    sig = jwt.utils.base64url_encode(hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest()).decode()
    rejects(v, f"{head}.{body}.{sig}", "algorithm")
    rejects(v, jwt.encode(claims(), None, algorithm="none", headers={"kid": "a"}), "algorithm")


def test_unknown_kid_refetches_once_then_is_rate_limited():
    clock = Clock()
    keys = Keys(jwk(KEY_A, "a"))
    v, _ = verifier(keys, clock, min_refetch=60)
    assert v.verify(token()) and keys.fetches == 1
    assert v.verify(token()) and keys.fetches == 1  # cached
    clock.t += 61
    keys.doc = {"keys": [jwk(KEY_A, "a"), jwk(KEY_B, "b")]}  # Google rotated
    assert v.verify(token(private=KEY_B, kid="b")).sub and keys.fetches == 2
    # forged kids: at most one refetch per min_refetch window
    rejects(v, token(private=KEY_EVIL, kid="zz"), "unknown kid")
    assert keys.fetches == 2
    clock.t += 61
    rejects(v, token(private=KEY_EVIL, kid="zz"), "unknown kid")
    rejects(v, token(private=KEY_EVIL, kid="zz"), "unknown kid")
    assert keys.fetches == 3
    # a known kid still works without fetching
    assert v.verify(token(private=KEY_B, kid="b")) and keys.fetches == 3


def test_keys_expire_after_the_ttl():
    clock = Clock()
    v, keys = verifier(clock=clock, ttl=3600)
    v.verify(token())
    clock.t += 3599
    v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60))
    assert keys.fetches == 1
    clock.t += 2
    keys.doc = {"keys": [jwk(KEY_B, "b")]}  # key "a" retired
    rejects(v, token(iat=int(clock.t), exp=int(clock.t) + 60), "unknown kid")
    assert keys.fetches == 2


def test_jwks_fetch_failure_is_unavailable_not_invalid():
    keys = Keys(jwk(KEY_A, "a"))
    keys.fail = True
    clock = Clock()
    v, _ = verifier(keys, clock)
    with pytest.raises(KeysUnavailable):
        v.verify(token())
    keys.fail = False
    clock.t += 61
    assert v.verify(token(iat=int(clock.t), exp=int(clock.t) + 60))
    for doc in ({}, {"keys": []}, {"keys": [{"kid": "x", "kty": "EC"}]}, {"keys": [{"kid": "a", "kty": "RSA", "n": "!"}]}):
        k = Keys()
        k.doc = doc
        with pytest.raises(KeysUnavailable):
            GoogleVerifier(CLIENT_ID, fetch=k, clock=Clock()).verify(token())


# --- invite codes, names, client ip, throttle --------------------------------------------------
def test_review_1_invite_account_key_is_the_code_not_its_position():
    codes = InviteCodes(["alpha-1", "beta-2"])
    key = codes.match("beta-2")
    assert key == account_key("beta-2") and "beta-2" not in key and len(key) == 32
    # removing or reordering other codes does not change it
    assert InviteCodes(["beta-2"]).match("beta-2") == InviteCodes(["gamma", "beta-2", "alpha-1"]).match("beta-2") == key
    assert codes.match("alpha-1") not in (None, key)
    for bad in ("", "alpha-", "alpha-1 ", "ALPHA-1", "\ud800"):
        assert codes.match(bad) is None
    assert codes.active(key) and not InviteCodes(["alpha-1"]).active(key)
    assert len(InviteCodes(["a", "a", "b"])) == 2
    assert not InviteCodes([])


def test_name_bounds():
    assert clean_name("  Ada   Lovelace ") == "Ada Lovelace"
    assert clean_name("x" * 40) == "x" * 40
    for bad in ("", "   ", "x" * 41, "a\x00b", "a‮b"):
        assert clean_name(bad) is None


def test_client_ip_trusts_forwarding_headers_only_from_a_proxy():
    trusted = parse_networks(None)
    h = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1, 172.18.0.3"}
    assert client_ip("203.0.113.50", h, trusted) == "203.0.113.50"  # public peer: headers ignored
    assert client_ip("172.18.0.2", h, trusted, trust_cf=True) == "203.0.113.9"  # Caddy → Cloudflare's header
    assert client_ip("172.18.0.2", h, trusted) == "198.51.100.1"  # not behind Cloudflare: XFF only
    assert client_ip("172.18.0.2", {"x-forwarded-for": "198.51.100.1, 172.18.0.3"}, trusted) == "198.51.100.1"
    assert client_ip("172.18.0.2", {"x-forwarded-for": "garbage"}, trusted) == "172.18.0.2"
    assert client_ip("172.18.0.2", {"cf-connecting-ip": "nope"}, trusted, trust_cf=True) == "172.18.0.2"
    assert client_ip("172.18.0.2", h, parse_networks("none")) == "172.18.0.2"
    assert client_ip("10.1.2.3", h, parse_networks("172.16.0.0/12")) == "10.1.2.3"


def test_throttle_sliding_window():
    clock = Clock(0.0)
    t = Throttle(3, 60, clock=clock)
    assert [t.hit("ip") for _ in range(3)] == [None] * 3
    assert t.hit("ip") == 60 and t.hit("other") is None
    clock.t = 59.5
    assert t.hit("ip") == pytest.approx(0.5)
    clock.t = 60
    assert t.hit("ip") is None


def test_throttle_memory_is_bounded():
    clock = Clock(0.0)
    t = Throttle(1, 60, clock=clock)
    t.MAX_KEYS = 100
    for i in range(1000):
        t.hit(f"k{i}")
    assert len(t._hits) <= 100


# --- HTTP -----------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCEAN_ENV_FILE", str(tmp_path / "no-such.env"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "w")


def settings(world, **kw):
    base = dict(app_db=world.dir / "app.db", dev_user="", dev_password="", session_secret=b"s" * 32,
                nonce_secret=SECRET, google_client_id=CLIENT_ID, invite_codes=["code-one", "code-two"])
    base.update(kw)
    return Settings(**base)


def app_client(world, peer=("203.0.113.7", 50000), **kw):
    c = TestClient(create_app(world.db, settings(world, **kw), clock=world.clock), client=peer)
    return c


@pytest.fixture
def client(world):
    with app_client(world) as c:
        c.app.state.google, c.keys = verifier()  # the fake JWKS endpoint
        yield c


def test_config_lists_the_login_methods(client, world):
    cfg = client.get("/api/config").json()
    assert cfg == {"dev_login": False, "invite_login": True, "google_client_id": CLIENT_ID}
    assert_shape(SCHEMAS_CONFIG, cfg)
    with app_client(world, google_client_id=None, invite_codes=[]) as c:
        assert c.get("/api/config").json() == {"dev_login": False, "invite_login": False, "google_client_id": None}
        assert_error(c.post("/api/auth/google", json={"credential": token()}), 404, "not_found")
        assert_error(c.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"}), 404, "not_found")
        assert_error(c.post("/api/auth/dev", json={"username": "", "password": ""}), 404, "not_found")


def test_google_login(client):
    res = client.post("/api/auth/google", json={"credential": token()})
    assert res.status_code == 200, res.text
    user = res.json()
    assert user["auth_provider"] == "google" and user["email"] == "ada@example.com"
    assert user["display_name"] == "Ada Example"
    assert "oa_session=" in res.headers["set-cookie"]
    me = client.get("/api/me").json()
    assert me["email"] == "ada@example.com"
    assert_shape(SCHEMAS_ME, {**me, "email": None})  # contract: string | null; the fixture shows null
    # same Google account → same user; name and email refreshed
    again = client.post("/api/auth/google", json={"credential": token(name="Ada L", email="ada@example.org")}).json()
    assert again["id"] == user["id"] and again["display_name"] == "Ada L" and again["email"] == "ada@example.org"
    other = client.post("/api/auth/google", json={"credential": token(sub="999", name=None)}).json()
    assert other["id"] != user["id"] and other["display_name"] == "ada@example.com"  # email when no name


def test_google_login_failures(client):
    client.cookies.clear()
    for bad in (token(private=KEY_EVIL), token(aud="x"), token(iss="evil"), token(exp=int(NOW) - 3600),
                token(email_verified=False), "not-a-jwt"):
        assert_error(client.post("/api/auth/google", json={"credential": bad}), 401, "invalid_credentials")
    assert_error(client.post("/api/auth/google", json={}), 400, "bad_request")
    assert_error(client.get("/api/me"), 401, "unauthorized")


def test_google_keys_unreachable_is_503(client):
    client.app.state.google, keys = verifier()
    keys.fail = True
    assert_error(client.post("/api/auth/google", json={"credential": token()}), 503, "auth_unavailable")


def test_invite_login(client):
    res = client.post("/api/auth/invite", json={"code": "code-one", "name": "  Grace  Hopper "})
    assert res.status_code == 200, res.text
    grace = res.json()
    assert (grace["auth_provider"], grace["display_name"], grace["email"]) == ("invite", "Grace Hopper", None)
    assert_shape(SCHEMAS_ME, client.get("/api/me").json())
    # one account per code: another name is the same account with a new display name; another code is another account
    again = client.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"}).json()
    assert (again["id"], again["display_name"]) == (grace["id"], "Ada")
    assert client.post("/api/auth/invite", json={"code": "code-two", "name": "Grace Hopper"}).json()["id"] != grace["id"]


def test_invite_failures(client):
    assert_error(client.post("/api/auth/invite", json={"code": "code-three", "name": "Ada"}), 401, "invalid_credentials")
    assert_error(client.post("/api/auth/invite", json={"code": "", "name": "Ada"}), 401, "invalid_credentials")
    for name in ("", "   ", "x" * 41):
        assert_error(client.post("/api/auth/invite", json={"code": "code-one", "name": name}), 400, "bad_request")
    assert client.post("/api/auth/invite", json={"code": "code-one", "name": "x" * 40}).status_code == 200
    assert_error(client.post("/api/auth/invite", json={"code": "code-one"}), 400, "bad_request")


def test_invite_users_are_isolated(client):
    client.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"})
    mine = client.post("/api/conversations", json={}).json()
    client.post("/api/auth/invite", json={"code": "code-two", "name": "Ada"})
    assert client.get("/api/conversations").json() == {"conversations": []}
    assert_error(client.get(f"/api/conversations/{mine['id']}"), 404, "not_found")


def test_login_throttle_per_ip(world):
    with app_client(world, peer=("172.18.0.2", 1), trust_cf_connecting_ip=True) as c:  # behind Caddy + Cloudflare
        clock = Clock(0.0)
        c.app.state.login_throttle = Throttle(10, 60, clock=clock)
        cf = {"cf-connecting-ip": "203.0.113.1"}
        for i in range(10):
            ep = ("/api/auth/invite", {"code": "nope", "name": "x"}) if i % 2 else \
                 ("/api/auth/google", {"credential": "bad"})
            assert c.post(ep[0], json=ep[1], headers=cf).status_code == 401
        res = c.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"}, headers=cf)
        assert_error(res, 429, "rate_limited")  # even the right code: the 11th attempt in a minute
        assert int(res.headers["retry-after"]) == 60
        # another client behind the same proxy is not affected
        assert c.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"},
                      headers={"cf-connecting-ip": "203.0.113.2"}).status_code == 200
        clock.t = 60
        assert c.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"}, headers=cf).status_code == 200


def test_throttle_ignores_forwarding_headers_from_untrusted_peers(world):
    with app_client(world) as c:  # public peer, e.g. the api port exposed by mistake
        for i in range(10):
            c.post("/api/auth/invite", json={"code": "nope", "name": "x"},
                   headers={"cf-connecting-ip": f"198.51.100.{i}"})
        assert_error(c.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"},
                            headers={"cf-connecting-ip": "198.51.100.99"}), 429, "rate_limited")


def test_no_secrets_in_logs_or_settings_repr(client, caplog):
    caplog.set_level(logging.DEBUG)
    secret_token = token(private=KEY_EVIL)
    good_token = token()
    client.post("/api/auth/google", json={"credential": secret_token})
    client.post("/api/auth/google", json={"credential": good_token})
    client.post("/api/auth/invite", json={"code": "code-two-typo", "name": "Ada"})
    client.post("/api/auth/invite", json={"code": "code-two", "name": "Ada"})
    client.app.state.google, keys = verifier()
    keys.fail = True
    client.post("/api/auth/google", json={"credential": good_token})
    text = caplog.text
    for secret in (secret_token, good_token, good_token.split(".")[2], "code-two", "code-one"):
        assert secret not in text
    assert "rejected" in text  # the reason is logged
    text_after = caplog.text
    for k in (account_key("code-one"), account_key("code-two")):  # the account key is not the code, but keep it out too
        assert k not in text_after
    r = repr(Settings(dev_user="demo", dev_password="hunter2", session_secret=b"topsecret",
                      nonce_secret=b"noncesecret", invite_codes=["code-one"]))
    for secret in ("hunter2", "topsecret", "noncesecret", "code-one"):
        assert secret not in r


def test_settings_from_env_auth_fields():
    s = Settings.from_env({"GOOGLE_CLIENT_ID": f" {CLIENT_ID} ", "INVITE_CODES": " a1, ,b2 ,",
                           "TRUSTED_PROXIES": "172.16.0.0/12", "DAILY_MESSAGES_PER_USER": "7"})
    assert (s.google_client_id, s.invite_codes, s.invite_login) == (CLIENT_ID, ["a1", "b2"], True)
    assert [str(n) for n in s.trusted_proxies] == ["172.16.0.0/12"] and s.daily_messages_per_user == 7
    empty = Settings.from_env({"GOOGLE_CLIENT_ID": " "})
    assert (empty.google_client_id, empty.invite_login, empty.dev_login) == (None, False, False)
    assert len(empty.trusted_proxies) > 0  # defaults
    assert empty.trust_cf_connecting_ip is False
    assert Settings.from_env({"TRUST_CF_CONNECTING_IP": "1"}).trust_cf_connecting_ip is True


# --- conversation title fallback ----------------------------------------------------------------
def test_conversation_title_is_never_empty(client, world):
    user = client.post("/api/auth/invite", json={"code": "code-one", "name": "Ada"}).json()
    made = client.post("/api/conversations", json={}).json()
    assert made["title"] == "New conversation"
    assert_shape(SCHEMAS_SUMMARY, made)
    listing = client.get("/api/conversations").json()["conversations"]
    assert [c["title"] for c in listing] == ["New conversation"]
    # a conversation whose turn stored the user message but never set a title
    world.store.add_message(user["id"], made["id"], "user", "   ")
    world.store.add_message(user["id"], made["id"], "user", "  Fly me\n to   Lima " + "x" * 100)
    detail = client.get(f"/api/conversations/{made['id']}").json()
    assert detail["title"] == ("Fly me to Lima " + "x" * 100)[:60]
    assert client.get("/api/conversations").json()["conversations"][0]["title"] == detail["title"]
    world.store.set_conversation_title(user["id"], made["id"], "Guangzhou to Lima")
    assert client.get(f"/api/conversations/{made['id']}").json()["title"] == "Guangzhou to Lima"


def test_first_user_text_checks_ownership(world):
    from app.store.db import NotFound

    with pytest.raises(NotFound):
        world.store.first_user_text(world.bob, world.conv)
    assert world.store.first_user_text(world.alice, world.conv) == ""

