"""All endpoints of docs/api/contract.md except the message stream (501 until T3 is merged)."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..agent import Agent, LLMConfig, LLMNotConfigured, OpenAIChatClient, encode_stream
from ..agent.loop import TITLE_CHARS
from ..agent.sse import HEADERS as SSE_HEADERS, MEDIA_TYPE as SSE_MEDIA_TYPE
from ..airline import Airline, NotFound
from ..core.network import NetworkProvider
from ..store import Store
from . import session
from .google import GoogleVerifier, InvalidToken, KeysUnavailable
from .logins import InviteCodes, Throttle, clean_name, client_ip, name_key
from .errors import ApiError, install_handlers, respond
from .limits import reserve_message, usage_view
from .settings import SESSION_TTL_SECONDS, Settings

PING_COUNT = 5
NEW_CONVERSATION_TITLE = "New conversation"
GOOGLE_NAME_MAX = 80

log = logging.getLogger(__name__)


class Credentials(BaseModel):
    username: str
    password: str


class GoogleCredential(BaseModel):
    credential: str


class Invite(BaseModel):
    code: str
    name: str


class Text(BaseModel):
    text: str


class Nonce(BaseModel):
    nonce: str


def current_user(request: Request) -> dict:
    """The signed-in user, or 401. The cookie only names the user; the row must still exist."""
    s: Settings = request.app.state.settings
    user_id = session.verify(s.session_secret, request.cookies.get(session.COOKIE))
    user = request.app.state.store.get_user(user_id) if user_id else None
    if user is None:
        raise ApiError(401, "unauthorized", "Please sign in.")
    return user


def _octets(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def user_view(user: dict) -> dict:
    return {"id": user["id"], "display_name": user["display_name"], "auth_provider": user["auth_provider"],
            "email": user["email"]}


def summary_view(store: Store, user_id: str, summary: dict) -> dict:
    """Contract: the title is never empty — first user message, else "New conversation"."""
    if summary["title"].strip():
        return summary
    first = " ".join(store.first_user_text(user_id, summary["id"]).split())[:TITLE_CHARS]
    return {**summary, "title": first or NEW_CONVERSATION_TITLE}


def throttle_login(request: Request) -> None:
    """Login endpoints: at most N attempts per client IP per minute (contract), else 429."""
    s: Settings = request.app.state.settings
    ip = client_ip(request.client.host if request.client else None, request.headers, s.trusted_proxies)
    wait = request.app.state.login_throttle.hit(ip)
    if wait is not None:
        raise ApiError(429, "rate_limited", "Too many sign-in attempts. Please wait a minute and try again.",
                       headers={"Retry-After": str(max(1, math.ceil(wait)))})


def _signed_in(settings: Settings, user: dict) -> JSONResponse:
    response = JSONResponse(user_view(user))
    _set_cookie(response, settings, user["id"])
    return response


def _set_cookie(response, settings: Settings, user_id: str) -> None:
    response.set_cookie(session.COOKIE, session.issue(settings.session_secret, user_id, SESSION_TTL_SECONDS),
                        max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, path="/")


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health(request: Request) -> dict:
        net = request.app.state.networks.current()
        return {"status": "ok", "network_fingerprint": net.fingerprint.id}

    @r.get("/config")
    def config(request: Request) -> dict:
        s: Settings = request.app.state.settings
        return {"dev_login": s.dev_login, "invite_login": s.invite_login, "google_client_id": s.google_client_id}

    @r.post("/auth/dev")
    def auth_dev(creds: Credentials, request: Request):
        s: Settings = request.app.state.settings
        if not s.dev_login:
            raise ApiError(404, "not_found", "Not found.")
        throttle_login(request)
        # surrogatepass: a lone surrogate in the JSON must be a wrong password, not a 500
        ok = s.dev_login and hmac.compare_digest(_octets(creds.username), _octets(s.dev_user)) \
            & hmac.compare_digest(_octets(creds.password), _octets(s.dev_password))
        if not ok:
            raise ApiError(401, "invalid_credentials", "Wrong username or password.")
        user = request.app.state.store.upsert_user("dev", s.dev_user, s.dev_user)
        return _signed_in(s, user)

    @r.post("/auth/google")
    def auth_google(body: GoogleCredential, request: Request):
        s: Settings = request.app.state.settings
        verifier: GoogleVerifier | None = request.app.state.google
        if verifier is None:
            raise ApiError(404, "not_found", "Not found.")
        throttle_login(request)
        try:
            who = verifier.verify(body.credential)
        except KeysUnavailable:
            raise ApiError(503, "auth_unavailable",
                           "Google sign-in is unavailable right now. Please try again later.") from None
        except InvalidToken as e:
            log.info("google sign-in rejected: %s", e)  # the reason only, never the token
            raise ApiError(401, "invalid_credentials", "Google sign-in could not be verified.") from None
        name = " ".join((who.name or who.email or "Google user").split())[:GOOGLE_NAME_MAX] or "Google user"
        user = request.app.state.store.upsert_user("google", who.sub, name, who.email)
        return _signed_in(s, user)

    @r.post("/auth/invite")
    def auth_invite(body: Invite, request: Request):
        s: Settings = request.app.state.settings
        codes: InviteCodes = request.app.state.invite_codes
        if not codes:
            raise ApiError(404, "not_found", "Not found.")
        throttle_login(request)
        index = codes.match(body.code)
        if index is None:
            raise ApiError(401, "invalid_credentials", "That invite code is not valid.")
        name = clean_name(body.name)
        if name is None:
            raise ApiError(400, "bad_request", "The name must be 1–40 characters.")
        user = request.app.state.store.upsert_user("invite", f"{index}:{name_key(name)}", name)
        return _signed_in(s, user)

    @r.post("/auth/logout")
    def logout(request: Request):
        s: Settings = request.app.state.settings
        response = JSONResponse({"ok": True})
        response.delete_cookie(session.COOKIE, path="/", httponly=True, samesite="lax", secure=s.cookie_secure)
        return response

    @r.get("/me")
    def me(request: Request, user: dict = Depends(current_user)) -> dict:
        return {**user_view(user), "usage": usage_view(request.app.state.store, request.app.state.settings,
                                                        user["id"])}

    @r.get("/conversations")
    def conversations(request: Request, user: dict = Depends(current_user)) -> dict:
        store: Store = request.app.state.store
        return {"conversations": [summary_view(store, user["id"], c)
                                  for c in store.list_conversations(user["id"])]}

    @r.post("/conversations")
    async def new_conversation(request: Request, user: dict = Depends(current_user)) -> dict:
        # The contract's request is `{}`; an empty body is accepted too, anything else must be an object.
        raw = await request.body()
        if raw.strip():
            try:
                ok = isinstance(json.loads(raw), dict)
            except ValueError:
                ok = False
            if not ok:
                raise ApiError(400, "bad_request", "The request body is not valid.")
        created = await asyncio.to_thread(request.app.state.store.create_conversation, user["id"])
        return {**created, "title": created["title"] or NEW_CONVERSATION_TITLE}

    @r.get("/conversations/{conversation_id}")
    def conversation(conversation_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
        summary = request.app.state.store.get_conversation(user["id"], conversation_id)
        if summary is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        summary = summary_view(request.app.state.store, user["id"], summary)
        return {**summary, "messages": request.app.state.airline.messages(user["id"], conversation_id)}

    @r.post("/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, body: Text, request: Request,
                           user: dict = Depends(current_user)):
        # Everything that can fail is decided here, before the response headers go out.
        app = request.app
        if await asyncio.to_thread(app.state.store.get_conversation, user["id"], conversation_id) is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        agent: Agent | None = app.state.agent
        if agent is None:
            raise ApiError(503, "llm_unavailable", "The assistant is not configured on this server.")
        try:
            reservation = await asyncio.to_thread(reserve_message, app.state.store, app.state.settings,
                                                  user["id"], conversation_id)
        except NotFound:
            raise ApiError(404, "not_found", "Conversation not found.") from None
        try:
            events = agent.stream_turn(user["id"], conversation_id, body.text)
        except (NotFound, ValueError) as e:
            # refused before anything was stored: the slot is given back
            await asyncio.to_thread(app.state.store.release_message, user["id"], reservation)
            if isinstance(e, NotFound):
                raise ApiError(404, "not_found", "Conversation not found.") from None
            raise ApiError(400, "bad_request", str(e) or "The message is not valid.") from None
        return StreamingResponse(encode_stream(events), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)

    @r.post("/quotes/{quote_id}/book")
    async def book(quote_id: str, body: Nonce, request: Request, user: dict = Depends(current_user)) -> dict:
        return await request.app.state.airline.book_async(user["id"], quote_id, body.nonce)

    @r.get("/quotes/{quote_id}/trace")
    def trace(quote_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
        return request.app.state.airline.trace(user["id"], quote_id)

    @r.get("/tickets")
    def tickets(request: Request, user: dict = Depends(current_user)) -> dict:
        return {"tickets": request.app.state.airline.tickets(user["id"])}

    @r.get("/debug/sse-ping")
    async def sse_ping(request: Request):
        interval = request.app.state.settings.ping_interval

        async def events():
            for seq in range(1, PING_COUNT + 1):
                if seq > 1:
                    await asyncio.sleep(interval)
                yield f"event: ping\ndata: {json.dumps({'seq': seq})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r


def install(app: FastAPI, settings: Settings, networks: NetworkProvider, clock=None, *,
            llm=None, agent_kwargs: dict | None = None) -> None:
    """Called from the app's lifespan: opens app.db and shares one Airline (one lock set) between
    the booking route and the agent. `llm` (tests) replaces the OpenAI-compatible client; with no
    API key configured there is no agent and the message route answers 503 llm_unavailable."""
    app.state.settings = settings
    app.state.store = store = Store(settings.app_db, **({"clock": clock} if clock else {}))
    app.state.google = GoogleVerifier(settings.google_client_id) if settings.google_client_id else None
    app.state.invite_codes = InviteCodes(settings.invite_codes)
    app.state.login_throttle = Throttle(settings.login_attempts_per_minute, 60.0)
    app.state.airline = airline = Airline(store, networks, nonce_secret=settings.nonce_secret)
    if llm is None:
        try:
            llm = OpenAIChatClient(LLMConfig.from_env())
        except LLMNotConfigured:
            llm = None
    app.state.llm = llm
    app.state.agent = Agent(airline, llm, **(agent_kwargs or {})) if llm is not None else None


async def shutdown(app: FastAPI) -> None:
    """Let running turns finish (they are stored even when the client left), then close the LLM client."""
    if getattr(app.state, "agent", None) is not None:
        await app.state.agent.wait_idle()
    close = getattr(getattr(app.state, "llm", None), "aclose", None)
    if close is not None:
        await close()


def mount(app: FastAPI) -> None:
    install_handlers(app)
    app.include_router(build_router())
