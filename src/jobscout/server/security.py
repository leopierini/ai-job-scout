"""Keeps the app reachable only from this computer's own pages.

A browser page from another site can send requests to 127.0.0.1 (CSRF) or point its own domain at it
(DNS rebinding). Three checks stop both: the Host must be local, any Origin must be local, and anything
that changes data must carry a header that a cross-site page cannot add without our consent (there is
no CORS middleware, so the browser never allows it).

Every response also forbids framing. Inside a frame the app is same-origin, so its own requests carry the
header above; without this, another site could overlay the app and get a click on "Find jobs"."""

from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCAL_HOSTS = {"127.0.0.1", "localhost"}
APP_HEADER = "x-requested-by"
APP_HEADER_VALUE = "ai-job-scout"
SAFE_METHODS = {"GET", "HEAD"}
NO_FRAMING = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}
BLOCKED = "This request was blocked. The app only answers pages opened from this computer."


def _hostname(value: str, *, prefix: str) -> str | None:
    try:
        return urlsplit(prefix + value).hostname
    except ValueError:
        return None


def _blocked(scope: Scope) -> bool:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    if _hostname(headers.get("host", ""), prefix="//") not in LOCAL_HOSTS:
        return True
    if "origin" in headers and _hostname(headers["origin"], prefix="") not in LOCAL_HOSTS:
        return True
    return scope["method"] not in SAFE_METHODS and headers.get(APP_HEADER) != APP_HEADER_VALUE


def _forbid_framing(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            for name, value in NO_FRAMING.items():
                headers[name] = value
        await send(message)

    return wrapped


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = _forbid_framing(send)
        if _blocked(scope):
            response = JSONResponse({"error": BLOCKED, "fields": None}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
