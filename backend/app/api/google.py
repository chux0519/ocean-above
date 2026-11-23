"""Google Sign-In: verify a Google Identity Services ID token ourselves.

The browser gets an ID token (a JWT signed by Google, RS256) from the GIS button and posts it to
`/api/auth/google`. We check it against Google's published keys (JWKS) and our client id; after
that Google is out of the loop — the user gets our own session cookie. No client secret, no
Google API scopes, no server-to-server call except fetching the public keys.

Checks: signature with the key named by the token's `kid` (RS256 only), `aud == client id`,
`iss` is Google, `exp` not passed (small leeway), `iat` not in the future, `sub` present,
`email_verified` is true. A replayed token is accepted while it is valid (the check is stateless);
it only proves the same Google account again.

Keys (review #2): cached for `ttl` seconds. A token with an unknown `kid` (Google rotated its keys)
allows one refetch, at most once per `min_refetch` seconds, so forged kids cannot make us hammer
Google. A failed fetch keeps the last good key set (usable for up to `max_stale` seconds after it
was fetched) and backs off: the next attempt waits `min_refetch`, doubling per consecutive failure
up to `max_backoff`. Only one fetch runs at a time (single flight) and it runs **outside** the
lock, so a slow Google never blocks requests that can use the cached keys. `KeysUnavailable`
(the API answers 503) only when there is no usable key set at all.
The clock and the fetch function are injectable for tests.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx
import jwt

log = logging.getLogger(__name__)

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 60
MAX_TOKEN_CHARS = 8192
MAX_TOKEN_LIFETIME = 24 * 3600  # exp - iat; Google issues 1 h tokens
FETCH_TIMEOUT = 3.0


class InvalidToken(Exception):
    """The token is not a valid Google ID token for this app. The reason is for logs and tests only."""


class KeysUnavailable(Exception):
    """Google's public keys could not be fetched."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str | None
    name: str | None


def fetch_jwks(url: str = JWKS_URL, timeout: float = FETCH_TIMEOUT) -> dict:
    """GET the JWKS document. Any failure (network, status, json) raises."""
    res = httpx.get(url, timeout=timeout, follow_redirects=False)
    res.raise_for_status()
    return res.json()


class GoogleVerifier:
    def __init__(self, client_id: str, *, fetch: Callable[[], dict] = fetch_jwks,
                 clock: Callable[[], float] = time.time, ttl: float = 3600.0, min_refetch: float = 60.0,
                 max_backoff: float = 900.0, max_stale: float = 24 * 3600.0, wait: float = FETCH_TIMEOUT + 1):
        if not client_id:
            raise ValueError("client_id is required")
        self.client_id = client_id
        self._fetch = fetch
        self._clock = clock
        self._ttl = ttl
        self._min_refetch = min_refetch
        self._max_backoff = max_backoff
        self._max_stale = max_stale
        self._wait = wait  # how long a request without a usable key waits for a running fetch
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None  # last successful fetch
        self._next_attempt = -math.inf  # no fetch is started before this time
        self._failures = 0  # consecutive failed fetches
        self._fetching = False
        self._cond = threading.Condition(threading.Lock())

    # --- keys ---------------------------------------------------------------------------------
    def _load(self) -> dict[str, jwt.PyJWK]:
        """Fetch and parse the key set (no lock held). Raises KeysUnavailable."""
        try:
            doc = self._fetch()
            keys = {}
            for entry in doc.get("keys", []):
                kid = entry.get("kid")
                if not isinstance(kid, str) or entry.get("kty") != "RSA":
                    continue
                try:
                    keys[kid] = jwt.PyJWK(entry, algorithm="RS256")
                except jwt.PyJWTError:
                    continue
        except Exception as e:  # network, http status, bad json, bad shape
            log.warning("google jwks fetch failed: %s", type(e).__name__)
            raise KeysUnavailable() from None
        if not keys:
            log.warning("google jwks fetch returned no usable keys")
            raise KeysUnavailable()
        return keys

    def _usable(self, now: float) -> dict[str, jwt.PyJWK]:
        """The cached keys while not older than `max_stale` (lock held)."""
        if self._fetched_at is None or now - self._fetched_at >= self._max_stale:
            return {}
        return self._keys

    def _wants_fetch(self, kid: str, now: float) -> bool:
        """Is a fetch due and allowed? (lock held)"""
        if self._fetching or now < self._next_attempt:
            return False
        if not self._usable(now) or now - self._fetched_at >= self._ttl:
            return True
        return kid not in self._keys  # possibly rotated; `_next_attempt` bounds how often

    def _fetch_outside_lock(self) -> None:
        """Run one fetch with the lock released, then publish the result. Called with the lock
        held and `_fetching` set; returns with the lock held."""
        self._cond.release()
        try:
            keys, ok = self._load(), True
        except KeysUnavailable:
            keys, ok = None, False
        finally:
            self._cond.acquire()
        now = self._clock()
        if ok:
            self._keys, self._fetched_at, self._failures = keys, now, 0
            self._next_attempt = now + self._min_refetch
        else:  # keep the last good keys; back off
            self._failures += 1
            self._next_attempt = now + min(self._min_refetch * 2 ** (self._failures - 1), self._max_backoff)

    def _key(self, kid: str) -> jwt.PyJWK:
        with self._cond:
            now = self._clock()
            if self._wants_fetch(kid, now):
                self._fetching = True
                try:
                    self._fetch_outside_lock()
                finally:
                    self._fetching = False
                    self._cond.notify_all()
            elif self._fetching and kid not in self._usable(now):
                # someone else is fetching and we have nothing to use yet: wait for that fetch only
                self._cond.wait_for(lambda: not self._fetching, timeout=self._wait)
            keys = self._usable(self._clock())
            key = keys.get(kid)
        if key is not None:
            return key
        if not keys:
            raise KeysUnavailable()
        raise InvalidToken("unknown kid")

    # --- tokens -------------------------------------------------------------------------------
    def verify(self, token: str) -> GoogleIdentity:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            raise InvalidToken("malformed")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidToken("malformed") from None
        if header.get("alg") not in ALGORITHMS:
            raise InvalidToken("algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidToken("no kid")
        key = self._key(kid)
        try:
            # PyJWT checks signature, aud and iss; time claims are checked below with our clock.
            claims = jwt.decode(
                token, key=key.key, algorithms=ALGORITHMS, audience=self.client_id, issuer=list(ISSUERS),
                options={"require": ["exp", "iat", "iss", "aud", "sub"],
                         "verify_exp": False, "verify_iat": False, "verify_nbf": False},
            )
        except jwt.InvalidAudienceError:
            raise InvalidToken("aud") from None
        except jwt.InvalidIssuerError:
            raise InvalidToken("iss") from None
        except jwt.InvalidSignatureError:
            raise InvalidToken("signature") from None
        except jwt.PyJWTError as e:
            raise InvalidToken(type(e).__name__) from None
        now = self._clock()
        exp, iat = claims.get("exp"), claims.get("iat")
        if not _number(exp) or not _number(iat):
            raise InvalidToken("time claims")
        if exp - iat > MAX_TOKEN_LIFETIME:
            raise InvalidToken("lifetime")
        if exp <= now - LEEWAY_SECONDS:
            raise InvalidToken("expired")
        if iat > now + LEEWAY_SECONDS:
            raise InvalidToken("issued in the future")
        if claims.get("email_verified") is not True:
            raise InvalidToken("email not verified")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise InvalidToken("sub")
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        name = claims.get("name") if isinstance(claims.get("name"), str) else None
        return GoogleIdentity(sub=sub, email=email, name=name)


def _number(value) -> bool:
    """A finite JSON number (review #9: NaN / Infinity parse as floats and pass every comparison)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


__all__ = ["GoogleIdentity", "GoogleVerifier", "InvalidToken", "KeysUnavailable", "fetch_jwks"]
