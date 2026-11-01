"""Connection tests for the setup wizard. Each returns a ConnectionResult and never raises."""

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from jobscout.costs import apify_credit
from jobscout.errors import WebhookError
from jobscout.models import JobRow, JobState, Status
from jobscout.outputs.notion import NotionClient, parse_page_id
from jobscout.outputs.webhook import WebhookOutput
from jobscout.paths import app_dir

APIFY_ME_URL = "https://api.apify.com/v2/users/me"
APIFY_REJECTED = "Apify rejected the token. Copy it again from Apify → Settings → API & Integrations."
APIFY_UNREACHABLE = "Apify couldn't be reached. Check your internet connection and try again."
APIFY_TIMEOUT = "Apify took too long to answer. Try again."
WEBHOOK_BAD_URL = "Enter a full web address that starts with https:// (or http://)."


@dataclass
class ConnectionResult:
    ok: bool
    message: str
    credit_usd: float | None = None


def test_apify(token: str, client: httpx.Client) -> ConnectionResult:
    """Check the token with Apify's "who am I" call, then read the remaining credit."""
    try:
        response = client.get(APIFY_ME_URL, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        return ConnectionResult(False, APIFY_TIMEOUT)
    except httpx.HTTPError:  # its text can carry the URL; the message here is fixed
        return ConnectionResult(False, APIFY_UNREACHABLE)
    if response.status_code in (401, 403):
        return ConnectionResult(False, APIFY_REJECTED)
    if not response.is_success:
        return ConnectionResult(False, f"Apify returned an error (HTTP {response.status_code}). Try again soon.")
    return ConnectionResult(True, "Connected.", apify_credit(token, client))


def _test_row() -> JobRow:
    """A made-up job, so the receiver sees the real payload shape."""
    return JobRow(
        source="test", job_id="0", title="Test job from AI Job Scout", company="Example Company",
        location="", url="", description="", posted="", keyword="", area="",
        state=JobState.KEPT, status=Status.TO_REVIEW, score=80, reason="", flags=[], start_date="", deadline="",
        archived_reason=None, dedup_key="test:0", first_seen_at="", status_updated_at="",
    )  # fmt: skip


def test_webhook(url: str, client: httpx.Client) -> ConnectionResult:
    """POST a made-up job. Messages never include the URL: it can hold a secret."""
    url = url.strip()
    if not url.lower().startswith(("https://", "http://")):
        return ConnectionResult(False, WEBHOOK_BAD_URL)
    try:
        WebhookOutput(url, client).deliver(_test_row())
    except WebhookError as exc:
        return ConnectionResult(False, str(exc))
    return ConnectionResult(True, "Sent a test job.")


def test_csv_path(path: str) -> ConnectionResult:
    """Check that the file's folder exists and can be written to. Writes nothing. Empty means the default file."""
    target = Path(path.strip()).expanduser() if path.strip() else app_dir() / "jobs.csv"
    if target.is_dir():
        return ConnectionResult(False, "That path is a folder. Add a file name, for example jobs.csv.")
    folder = target.parent
    if not folder.is_dir():
        return ConnectionResult(False, f"The folder {folder} doesn't exist. Create it first or pick another path.")
    if not os.access(folder, os.W_OK | os.X_OK):
        return ConnectionResult(False, f"The app can't write to {folder}. Pick a folder you can save files in.")
    return ConnectionResult(True, f"Jobs will be saved to {target}.")


def connect_notion(token: str, parent_page: str, client: httpx.Client) -> str:
    """Create the tracker database under the page and return its id.

    Raises ConfigError for a link that isn't a Notion page and NotionError for a failed call; both
    messages are plain and never include the token.
    """
    parent_page_id = parse_page_id(parent_page)
    return NotionClient(token, client).create_database(parent_page_id)


# pytest would otherwise collect these when a test module imports them.
for _fn in (test_apify, test_webhook, test_csv_path):
    _fn.__test__ = False  # type: ignore[attr-defined]
