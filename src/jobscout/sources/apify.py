"""Minimal Apify client: run an actor and get its dataset items."""

import time
from collections.abc import Callable

import httpx

from jobscout.errors import ApifyError
from jobscout.http import request_json

RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?timeout=300"
LIMIT_MESSAGE = "Apify monthly limit reached. It resets on your billing day. Add credit or wait."
TIMEOUT_MESSAGE = "Apify took too long to answer. This search was skipped and will be retried next run."
UNREACHABLE_MESSAGE = "Apify couldn't be reached. Check your internet connection and try again."
AUTH_MESSAGE = "Apify rejected the token. Check the token and try again."


class ApifyClient:
    def __init__(
        self, token: str, client: httpx.Client, *, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._token = token
        self._client = client
        self._sleep = sleep  # injectable so tests do not wait on retries

    def run_actor(self, actor: str, payload: dict) -> list[dict]:
        """Run `actor` with `payload` and return its items. Raises ApifyError; messages never contain the token."""
        try:
            data = request_json(
                self._client,
                "POST",
                RUN_URL.format(actor=actor),
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=320,
                # Retry only what surely never started a run: a 429, or no connection at all. A 5xx or a
                # timeout may come after the run started, and sending it again could bill it twice.
                retry_statuses=frozenset({429}),
                retry_errors=(httpx.ConnectError, httpx.ConnectTimeout),
                sleep=self._sleep,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403 and "limit" in exc.response.text.lower():
                raise ApifyError(LIMIT_MESSAGE, kind="limit") from None
            if status == 401:
                raise ApifyError(AUTH_MESSAGE, kind="auth") from None
            raise ApifyError(f"Apify request failed (HTTP {status}).", kind="other") from None
        except httpx.ReadTimeout:
            raise ApifyError(TIMEOUT_MESSAGE, kind="other") from None
        except httpx.TransportError:
            raise ApifyError(UNREACHABLE_MESSAGE, kind="other") from None
        except httpx.HTTPError:
            raise ApifyError("Apify request failed. Try again.", kind="other") from None
        if not isinstance(data, list):
            raise ApifyError("Apify returned an unexpected response.", kind="other")
        return data
