"""Google Sign-In: verify a Google Identity Services ID token ourselves.

The browser gets an ID token (a JWT signed by Google, RS256) from the GIS button and posts it to
`/api/auth/google`. We check it against Google's published keys (JWKS) and our client id; after
that Google is out of the loop — the user gets our own session cookie. No client secret, no
Google API scopes, no server-to-server call except fetching the public keys.

Checks: signature with the key named by the token's `kid` (RS256 only), `aud == client id`,
`iss` is Google, `exp` not passed (small leeway), `iat` not in the future, `sub` present,
`email_verified` is true. A replayed token is accepted while it is valid (the check is stateless);
it only proves the same Google account again.

Keys are cached for `ttl` seconds. A token with an unknown `kid` (Google rotated its keys) forces
one refetch, at most once per `min_refetch` seconds so forged kids cannot make us hammer Google.
If the keys are needed and cannot be fetched → `KeysUnavailable` (the API answers 503).
The clock and the fetch function are injectable for tests.
"""
from __future__ import annotations

import logging
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


class InvalidToken(Exception):
    """The token is not a valid Google ID token for this app. The reason is for logs and tests only."""


class KeysUnavailable(Exception):
    """Google's public keys could not be fetched."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str | None
    name: str | None


def fetch_jwks(url: str = JWKS_URL, timeout: float = 5.0) -> dict:
    """GET the JWKS document. Any failure (network, status, json) raises."""
    res = httpx.get(url, timeout=timeout, follow_redirects=False)
    res.raise_for_status()
    return res.json()


class GoogleVerifier:
    def __init__(self, client_id: str, *, fetch: Callable[[], dict] = fetch_jwks,
                 clock: Callable[[], float] = time.time, ttl: float = 3600.0, min_refetch: float = 60.0):
        if not client_id:
            raise ValueError("client_id is required")
        self.client_id = client_id
        self._fetch = fetch
        self._clock = clock
        self._ttl = ttl
        self._min_refetch = min_refetch
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    # --- keys ---------------------------------------------------------------------------------
    def _refresh(self) -> None:
        """Replace the key set. Called with the lock held."""
        self._fetched_at = self._clock()  # also on failure: the refetch limit counts attempts
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
            self._keys = {}
            log.warning("google jwks fetch failed: %s", type(e).__name__)
            raise KeysUnavailable() from None
        if not keys:
            self._keys = {}
            log.warning("google jwks fetch returned no usable keys")
            raise KeysUnavailable()
        self._keys = keys

    def _key(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = self._clock()
            fresh = self._fetched_at is not None and now - self._fetched_at < self._ttl and self._keys
            if not fresh:
                self._refresh()
            elif kid not in self._keys and now - self._fetched_at >= self._min_refetch:
                self._refresh()  # keys rotated since the last fetch
            key = self._keys.get(kid)
        if key is None:
            raise InvalidToken("unknown kid")
        return key

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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


__all__ = ["GoogleIdentity", "GoogleVerifier", "InvalidToken", "KeysUnavailable", "fetch_jwks"]
