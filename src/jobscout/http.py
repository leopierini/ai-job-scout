"""HTTP helper with retries for transient failures."""

import time
from collections.abc import Callable
from typing import Any

import httpx

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    retries: int = 3,
    retry_on_timeout: bool = True,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
    retry_errors: tuple[type[httpx.TransportError], ...] = (httpx.TransportError,),
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Send a request and return the parsed JSON body, or None if the body is empty or not JSON.

    `retries` is the total number of attempts. The transport errors in `retry_errors` and the
    statuses in `retry_statuses` are retried, waiting 2**attempt seconds between attempts (1 s, 2 s,
    ...). Any other error status raises `httpx.HTTPStatusError` at once; when attempts run out, the
    last error is raised. With `retry_on_timeout=False` a read timeout is raised at once: the server
    may have acted on the request already, so sending it again could do the work (and bill it) twice.
    """
    attempts = max(1, retries)
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = client.request(method, url, json=json, headers=headers, timeout=timeout)
        except httpx.TransportError as exc:
            timed_out = isinstance(exc, httpx.ReadTimeout) and not retry_on_timeout
            if last or timed_out or not isinstance(exc, retry_errors):
                raise
            sleep(2**attempt)
            continue
        if response.status_code in retry_statuses and not last:
            sleep(2**attempt)
            continue
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return None
    raise AssertionError("unreachable")  # pragma: no cover
