"""Request guards in front of every `/api/*` route (plain ASGI, before any body parsing).

- **Body size** (review #11): a request body over `MAX_BODY_BYTES` is refused with 413
  `payload_too_large` — by `Content-Length` before reading, else while reading (chunked). The body
  is buffered (it is small JSON) and replayed to the route, so nothing parses an oversized body.
- **Cross-site writes** (review #12, CSRF beyond SameSite=Lax): a state-changing request
  (anything but GET / HEAD / OPTIONS) is refused with 403 `forbidden_origin` when the browser
  says it came from another site (`Sec-Fetch-Site: cross-site`) or from a sibling subdomain of the
  same site (`same-site`; SameSite=Lax still sends the cookie there), or when its `Origin` is not
  this host. The host is `Host`, or `X-Forwarded-Host` set by the Next.js dev rewrite (a page
  cannot set that header on a cross-origin request without a CORS preflight, which we never
  allow). Requests with neither header (curl, tests, server-side calls) carry no ambient browser
  credentials worth guarding and pass.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

MAX_BODY_BYTES = 64 * 1024
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _error(status: int, code: str, message: str) -> tuple[int, bytes]:
    return status, json.dumps({"error": {"code": code, "message": message}}).encode()


TOO_LARGE = _error(413, "payload_too_large", "The request is too large.")
FORBIDDEN = _error(403, "forbidden_origin", "This request must come from the Ocean Above site.")


def _headers(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    return out


def _host_of(origin: str) -> str | None:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return parts.netloc.lower()


def same_origin(method: str, headers: dict[str, str]) -> bool:
    """Is this request allowed by the cross-site rule above?"""
    if method.upper() in SAFE_METHODS:
        return True
    site = headers.get("sec-fetch-site", "").strip().lower()
    if site in ("cross-site", "same-site"):
        return False
    origin = headers.get("origin")
    if origin is None:
        return True
    got = _host_of(origin.strip())  # "null" (sandboxed frame, some redirects) → None → refused
    if got is None:
        return False
    hosts = {headers.get("host", "").strip().lower()}
    forwarded = headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
    if forwarded:
        hosts.add(forwarded)
    hosts.discard("")
    return got in hosts


class ApiGuard:
    def __init__(self, app, max_body: int = MAX_BODY_BYTES):
        self.app, self.max_body = app, max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = _headers(scope)
        if not same_origin(scope.get("method", "GET"), headers):
            return await _respond(send, FORBIDDEN)
        length = headers.get("content-length", "").strip()
        if length.isdigit() and int(length) > self.max_body:
            return await _respond(send, TOO_LARGE)
        if scope.get("method", "GET").upper() in SAFE_METHODS and not length:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # client went away while sending
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body:
                return await _respond(send, TOO_LARGE)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body, replayed = b"".join(chunks), False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # afterwards: http.disconnect etc. from the server

        return await self.app(scope, replay, send)


async def _respond(send, error: tuple[int, bytes]) -> None:
    status, body = error
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
