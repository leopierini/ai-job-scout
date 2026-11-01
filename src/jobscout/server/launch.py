"""Starting the server: pick a port, reuse a running app, open the browser."""

import os
import socket
import threading
import time
import webbrowser
from collections.abc import Callable

import httpx
import uvicorn
from fastapi import FastAPI

from jobscout.server.app import create_app

DEFAULT_PORT = 8765
PORT_TRIES = 20  # after the preferred port, try this many more
HOST = "127.0.0.1"
START_TIMEOUT = 15  # seconds to wait for the server before giving up on opening the browser


def _probe_health(port: int) -> dict | None:
    """The /api/health answer of whatever listens on `port`, or None."""
    try:
        r = httpx.get(f"http://{HOST}:{port}/api/health", timeout=0.5, trust_env=False)  # never via a proxy
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return body if r.status_code == 200 and isinstance(body, dict) else None


def _is_posix() -> bool:
    return os.name == "posix"


def _is_free(port: int) -> bool:
    """Whether uvicorn could bind `port`. Mirrors asyncio, which sets SO_REUSEADDR only on POSIX: there it
    lets a port in TIME_WAIT be reused. On Windows the same option would let us bind a port another program
    is listening on, so we'd report it free and then uvicorn's bind would fail."""
    with socket.socket() as s:
        if _is_posix():
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def pick_port(
    preferred: int = DEFAULT_PORT, probe: Callable[[int], dict | None] = _probe_health
) -> tuple[int, bool]:
    """(port, already_running): a port in the range where our app already answers (a second copy would share
    its data), else the first free one."""
    ports = range(preferred, preferred + PORT_TRIES + 1)
    for port in ports:
        health = probe(port)
        if health and health.get("app") == "ai-job-scout":
            return port, True
    for port in ports:
        if _is_free(port):
            return port, False
    raise OSError(f"No free port between {preferred} and {preferred + PORT_TRIES}. Close other programs and try again.")


def _open_when_up(server: uvicorn.Server, url: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)


def _run_server(app: FastAPI, port: int, open_browser: bool) -> None:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    if open_browser:
        threading.Thread(target=_open_when_up, args=(server, f"http://{HOST}:{port}/"), daemon=True).start()
    server.run()


def serve(port: int | None, open_browser: bool, sample: bool) -> int:
    port, running = pick_port(port or DEFAULT_PORT)
    url = f"http://{HOST}:{port}/"
    if running:
        print(f"AI Job Scout is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return 0
    print(f"AI Job Scout is running at {url} . Keep this window open; close it to stop the app.", flush=True)
    _run_server(create_app(sample=sample), port, open_browser)
    return 0
