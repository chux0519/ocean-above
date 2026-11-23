"""Invite-code login, the per-IP login throttle and how we find the client IP.

Invite codes (`INVITE_CODES`, comma-separated) let reviewers in without Google. Only SHA-256
digests of the codes are kept in memory; a submitted code is hashed and compared with every
digest using `hmac.compare_digest` (no early exit, so timing does not tell which code is close).
**One account per code** (review #1, #3, #6): the user is `(invite, account_key(code))`, a
domain-separated SHA-256 of the code itself — not its position in the list, so codes can be
removed (revoked) or reordered without moving anyone into someone else's account. The name is only
the display name (updated on every login); it is not part of the identity. One code = one account
= one daily message limit. Give each reviewer their own code.

Client IP: behind Cloudflare → cloudflared → Caddy → api the TCP peer is always Caddy. Only when
the peer is a trusted proxy (`TRUSTED_PROXIES`, CIDRs; default loopback + private ranges, because
the api port is only reachable inside the compose network) do we look at forwarding headers:
`CF-Connecting-IP` **only when `TRUST_CF_CONNECTING_IP` is on** (review #4: the header is only
trustworthy when Cloudflare's edge set it, i.e. the deployment really sits behind Cloudflare),
else the right-most untrusted `X-Forwarded-For` entry. From any other peer the headers are ignored.
The throttle key of an IPv6 client is its /64 (review #5: one host usually owns the whole /64).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import threading
import time
import unicodedata
from collections import deque
from typing import Callable, Iterable

NAME_MAX = 40
DEFAULT_TRUSTED_PROXIES = ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                           "fc00::/7")


# --- invite codes ------------------------------------------------------------------------------
def parse_codes(raw: str | None) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


_KEY_DOMAIN = b"ocean-above invite account v1\0"


def _digest(code: str) -> bytes:
    return hashlib.sha256(_KEY_DOMAIN + code.encode("utf-8", "surrogatepass")).digest()


def account_key(code: str) -> str:
    """The stable account key of an invite code (`users.provider_sub`): independent of the code's
    position in `INVITE_CODES`, never the code itself."""
    return _digest(code).hex()[:32]


class InviteCodes:
    def __init__(self, codes: Iterable[str]):
        self._digests = list(dict.fromkeys(_digest(c) for c in codes))  # duplicates are one code
        self._keys = frozenset(d.hex()[:32] for d in self._digests)

    def __bool__(self) -> bool:
        return bool(self._digests)

    def __len__(self) -> int:
        return len(self._digests)

    def match(self, code: str) -> str | None:
        """The account key of the matching code, or None. Compares with every code (constant work)."""
        got = _digest(code)
        found = False
        for d in self._digests:
            found |= hmac.compare_digest(got, d)
        return got.hex()[:32] if found else None

    def active(self, key: str) -> bool:
        """Is the code behind this account key still configured? (a removed code = revoked)"""
        return key in self._keys


def clean_name(name: str) -> str | None:
    """Display name: NFKC, whitespace collapsed; None unless 1–40 characters without control chars."""
    text = " ".join(unicodedata.normalize("NFKC", name).split())
    if not 1 <= len(text) <= NAME_MAX:
        return None
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") for ch in text):
        return None
    return text


# --- client ip ---------------------------------------------------------------------------------
def parse_networks(raw: str | None) -> tuple:
    """`TRUSTED_PROXIES`: unset → defaults; "none" or empty → trust no proxy."""
    if raw is None:
        items: Iterable[str] = DEFAULT_TRUSTED_PROXIES
    elif raw.strip().lower() in ("", "none"):
        items = ()
    else:
        items = [x.strip() for x in raw.split(",") if x.strip()]
    return tuple(ipaddress.ip_network(x, strict=False) for x in items)


def _ip(text: str | None):
    try:
        return ipaddress.ip_address((text or "").strip())
    except ValueError:
        return None


def _trusted(ip, networks) -> bool:
    return ip is not None and any(ip.version == n.version and ip in n for n in networks)


def client_ip(peer: str | None, headers, trusted, trust_cf: bool = False) -> str:
    """The IP the throttle keys on (see module doc)."""
    peer_ip = _ip(peer)
    if not _trusted(peer_ip, trusted):
        return str(peer_ip) if peer_ip else (peer or "unknown")
    cf = _ip(headers.get("cf-connecting-ip")) if trust_cf else None
    if cf is not None:
        return str(cf)
    for item in reversed((headers.get("x-forwarded-for") or "").split(",")):
        ip = _ip(item)
        if ip is None:
            break
        if not _trusted(ip, trusted):
            return str(ip)
    return str(peer_ip)


def throttle_key(ip: str) -> str:
    """Bucket for the login throttle: IPv4 address as is, IPv6 by its /64 (an IPv4-mapped IPv6
    address counts as the IPv4 address)."""
    addr = _ip(ip)
    if addr is None or addr.version == 4:
        return ip
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


# --- throttle ----------------------------------------------------------------------------------
class Throttle:
    """At most `limit` attempts per key in any `window` seconds (sliding window, in memory;
    the api runs as one worker). Every attempt counts, successful or not."""

    MAX_KEYS = 50_000

    def __init__(self, limit: int = 10, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record an attempt. None when allowed; else seconds until the next attempt is allowed."""
        with self._lock:
            now = self._clock()
            if len(self._hits) >= self.MAX_KEYS:
                self._prune(now)
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(q[0] + self.window - now, 0.0)
            q.append(now)
            return None

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[k]
        if len(self._hits) >= self.MAX_KEYS:  # still full of active keys: forget the oldest
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[: len(self._hits) // 2]:
                del self._hits[k]
