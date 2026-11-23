"""Error bodies: HTTP status + {"error": {"code", "message"}} for every failure."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..airline import BookingError, NotFound
from ..core.network import NetworkDataError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None,
                 extra: dict | None = None):
        """`extra`: more keys inside `error` (429 rate_limited adds `scope` and `resets_at`)."""
        self.status, self.code, self.message, self.headers = status, code, message, headers
        self.extra = extra


def body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def respond(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(body(code, message), status_code=status)


_BY_STATUS = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        content = body(e.code, e.message)
        content["error"].update(e.extra or {})
        response = JSONResponse(content, status_code=e.status)
        response.headers.update(e.headers or {})
        return response

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, e: NotFound):
        return respond(404, "not_found", "Not found.")

    @app.exception_handler(BookingError)
    async def _booking(_: Request, e: BookingError):
        return JSONResponse(e.as_response(), status_code=e.status)

    @app.exception_handler(NetworkDataError)
    async def _data(_: Request, e: NetworkDataError):
        return respond(503, "data_unavailable", "Route data could not be read right now. Please try again.")

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        return respond(400, "bad_request", "The request body is not valid.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = _BY_STATUS.get(e.status_code, "bad_request" if e.status_code < 500 else "internal_error")
        return respond(e.status_code, code, "Not found." if e.status_code == 404 else str(e.detail))
