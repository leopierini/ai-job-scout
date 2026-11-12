"""Webhook output: one JSON POST per job."""

import time
from collections.abc import Callable

import httpx

from jobscout.errors import WebhookError
from jobscout.http import request_json
from jobscout.models import STATUS_LABELS, JobRow


UNREACHABLE_MESSAGE = "The webhook couldn't be reached. Check the URL and that the service is running."
TIMEOUT_MESSAGE = "The webhook took too long to answer. Try again."
FAILED_MESSAGE = "The webhook request failed. Check the URL and try again."


def job_payload(row: JobRow) -> dict:
    return {
        "id": row.pk,
        "source": row.source,
        "job_id": row.job_id,
        "title": row.title,
        "company": row.company,
        "location": row.location,
        "salary": row.salary,
        "url": row.url,
        "score": row.score,
        "reason": row.reason,
        "flags": row.flags,
        "status": STATUS_LABELS[row.status],
        "start_date": row.start_date,
        "deadline": row.deadline,
        "posted": row.posted,
        "recruiter": row.recruiter,
        "recruiter_title": row.recruiter_title,
        "recruiter_url": row.recruiter_url,
        "keyword": row.keyword,
    }


class WebhookOutput:
    name = "webhook"

    def __init__(
        self, url: str, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._url = url
        self._client = client
        self._sleep = sleep

    def deliver(self, row: JobRow) -> None:
        """POST the job as JSON; raises `WebhookError` on an error status or network failure after retries.

        A read timeout is not retried: the receiver may have processed the POST already.
        """
        try:
            request_json(
                self._client, "POST", self._url, json=job_payload(row), retry_on_timeout=False, sleep=self._sleep
            )
        except httpx.HTTPStatusError as exc:
            # Webhook URLs often embed a secret, so never pass httpx's message (it includes the URL).
            raise WebhookError(f"The webhook returned HTTP {exc.response.status_code}.") from None
        except httpx.TimeoutException:
            raise WebhookError(TIMEOUT_MESSAGE) from None
        except httpx.TransportError:
            raise WebhookError(UNREACHABLE_MESSAGE) from None
        except (httpx.InvalidURL, httpx.HTTPError):
            raise WebhookError(FAILED_MESSAGE) from None
