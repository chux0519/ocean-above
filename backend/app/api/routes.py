"""All endpoints of docs/api/contract.md except the message stream (501 until T3 is merged)."""
from __future__ import annotations

import asyncio
import hmac
import json

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..airline import Airline
from ..core.network import NetworkProvider
from ..store import Store
from . import session
from .errors import ApiError, install_handlers, respond
from .settings import SESSION_TTL_SECONDS, Settings

PING_COUNT = 5


class Credentials(BaseModel):
    username: str
    password: str


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


def user_view(user: dict) -> dict:
    return {"id": user["id"], "display_name": user["display_name"], "auth_provider": user["auth_provider"]}


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
        return {"dev_login": s.dev_login, "google_client_id": s.google_client_id}

    @r.post("/auth/dev")
    def auth_dev(creds: Credentials, request: Request):
        s: Settings = request.app.state.settings
        ok = s.dev_login and hmac.compare_digest(creds.username.encode(), s.dev_user.encode()) \
            & hmac.compare_digest(creds.password.encode(), s.dev_password.encode())
        if not ok:
            raise ApiError(401, "invalid_credentials", "Wrong username or password.")
        user = request.app.state.store.upsert_user("dev", s.dev_user, s.dev_user)
        response = JSONResponse(user_view(user))
        _set_cookie(response, s, user["id"])
        return response

    @r.post("/auth/logout")
    def logout(request: Request):
        s: Settings = request.app.state.settings
        response = JSONResponse({"ok": True})
        response.delete_cookie(session.COOKIE, path="/", httponly=True, samesite="lax", secure=s.cookie_secure)
        return response

    @r.get("/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return user_view(user)

    @r.get("/conversations")
    def conversations(request: Request, user: dict = Depends(current_user)) -> dict:
        return {"conversations": request.app.state.store.list_conversations(user["id"])}

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
        return await asyncio.to_thread(request.app.state.store.create_conversation, user["id"])

    @r.get("/conversations/{conversation_id}")
    def conversation(conversation_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
        summary = request.app.state.store.get_conversation(user["id"], conversation_id)
        if summary is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        return {**summary, "messages": request.app.state.airline.messages(user["id"], conversation_id)}

    @r.post("/conversations/{conversation_id}/messages")
    def send_message(conversation_id: str, body: Text, request: Request, user: dict = Depends(current_user)):
        if request.app.state.store.get_conversation(user["id"], conversation_id) is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        return respond(501, "not_implemented", "Sending messages is not wired up yet.")

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


def install(app: FastAPI, settings: Settings, networks: NetworkProvider, clock=None) -> None:
    """Called from the app's lifespan: opens app.db and shares one Airline (one lock set)."""
    app.state.settings = settings
    app.state.store = store = Store(settings.app_db, **({'clock': clock} if clock else {}))
    app.state.airline = Airline(store, networks, nonce_secret=settings.nonce_secret)


def mount(app: FastAPI) -> None:
    install_handlers(app)
    app.include_router(build_router())
