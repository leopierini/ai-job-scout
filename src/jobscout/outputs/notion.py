"""Notion output: a tracker database that mirrors the jobs table."""

import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx

from jobscout.errors import ConfigError, NotionError
from jobscout.http import request_json
from jobscout.models import STATUS_LABELS, JobRow, Status

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
PAUSE_S = 0.35  # stay under Notion's rate limit (about 3 requests a second)
MAX_TEXT = 2000  # Notion's limit for one rich-text chunk
MAX_OPTION = 100  # Notion's limit for a select option name

_RICH_TEXT_FIELDS = ["Company", "Location", "Reason", "Keyword", "Recruiter", "Recruiter Title", "Job ID"]

PROPERTIES: dict = {
    "Title": {"title": {}},
    **{name: {"rich_text": {}} for name in _RICH_TEXT_FIELDS},
    "Fit Score": {"number": {"format": "number"}},
    "Flags": {"multi_select": {"options": []}},
    "Status": {"select": {"options": [{"name": label} for label in STATUS_LABELS.values()]}},
    "Source": {"select": {"options": [{"name": "LinkedIn"}, {"name": "Indeed"}]}},
    "Posted": {"date": {}},
    "Starts": {"date": {}},
    "Deadline": {"date": {}},
    "Job URL": {"url": {}},
    "Recruiter Contact": {"url": {}},
}

_SOURCE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed"}
_STATUS_BY_LABEL = {label: status for status, label in STATUS_LABELS.items()}
_ID_IN_SEGMENT = re.compile(r"(?:^|-)([0-9a-f]{32})$", re.IGNORECASE)
_RAW_ID = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_page_id(url_or_id: str) -> str:
    """Return the dashed UUID of a Notion page or database, from its URL or its raw 32-hex id."""
    text = url_or_id.strip()
    if _RAW_ID.fullmatch(text):
        hex_id = text.replace("-", "").lower()
    else:
        # The id is the end of the last path segment; a database link's `?v=` view id is not it.
        segment = urlparse(text).path.rstrip("/").rsplit("/", 1)[-1]
        match = _ID_IN_SEGMENT.search(segment)
        if not match:
            raise ConfigError("That doesn't look like a Notion page link or id.")
        hex_id = match.group(1).lower()
    return f"{hex_id[:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"


def _text(value: str) -> str:
    return value[:MAX_TEXT]


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": _text(value)}}] if value else []}


def _iso_date(value: str) -> bool:
    if not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _flag_names(flags: list[str]) -> list[str]:
    """Notion rejects commas in option names and long names; keep order, drop blanks and repeats."""
    names: list[str] = []
    for flag in flags:
        name = flag.replace(",", "").strip()[:MAX_OPTION].strip()
        if name and name not in names:
            names.append(name)
    return names


def _page_properties(row: JobRow) -> dict:
    props: dict = {
        "Title": {"title": [{"text": {"content": _text(row.title or "Untitled")}}]},
        "Company": _rich_text(row.company),
        "Location": _rich_text(row.location),
        "Reason": _rich_text(row.reason),
        "Keyword": _rich_text(row.keyword),
        "Recruiter": _rich_text(row.recruiter),
        "Recruiter Title": _rich_text(row.recruiter_title),
        "Job ID": _rich_text(row.pk),
        "Flags": {"multi_select": [{"name": name} for name in _flag_names(row.flags)]},
        "Status": {"select": {"name": STATUS_LABELS[row.status]}},
        "Source": {"select": {"name": _SOURCE_NAMES.get(row.source, row.source.capitalize())}},
    }
    if row.score is not None:
        props["Fit Score"] = {"number": row.score}
    for name, value in (("Posted", row.posted), ("Starts", row.start_date), ("Deadline", row.deadline)):
        if _iso_date(value):
            props[name] = {"date": {"start": value}}
    if row.url:
        props["Job URL"] = {"url": row.url}
    if row.recruiter_url:
        props["Recruiter Contact"] = {"url": row.recruiter_url}
    return props


def _parse_edited(value: str) -> datetime:
    """Notion sends e.g. 2026-11-01T10:15:00.000Z; return it as naive UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _error_message(exc: Exception) -> str:
    # Never pass httpx's own message on: it can carry the request URL and headers.
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return "Notion rejected the token. Check the token and try again."
        if code == 404:
            return "Notion can't see that page or database. Share it with your integration."
        return f"Notion request failed (HTTP {code})."
    if isinstance(exc, httpx.TimeoutException):
        return "Notion took too long to answer. Try again."
    if isinstance(exc, httpx.TransportError):
        return "Notion couldn't be reached. Check your internet connection and try again."
    return "Notion request failed. Try again."


class NotionClient:
    def __init__(
        self, token: str, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
        self._client = client
        self._sleep = sleep
        self._used = False

    def _call(self, method: str, path: str, body: dict | None = None, *, retry_on_timeout: bool = True) -> dict:
        if self._used:
            self._sleep(PAUSE_S)
        self._used = True
        try:
            result = request_json(
                self._client,
                method,
                f"{API}{path}",
                json=body,
                headers=self._headers,
                retry_on_timeout=retry_on_timeout,
                sleep=self._sleep,
            )
        except (httpx.InvalidURL, httpx.HTTPError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            raise NotionError(_error_message(exc), status) from None
        return result if isinstance(result, dict) else {}

    def create_database(self, parent_page_id: str, title: str = "AI Job Scout") -> str:
        """Create the tracker database under a page and return its id."""
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": PROPERTIES,
        }
        return self._call("POST", "/databases", body)["id"]

    def create_page(self, database_id: str, row: JobRow) -> str:
        """Add a job as a page and return its id. Not retried on a timeout: it could create a duplicate."""
        body = {"parent": {"database_id": database_id}, "properties": _page_properties(row)}
        return self._call("POST", "/pages", body, retry_on_timeout=False)["id"]

    def update_status(self, page_id: str, status: Status) -> None:
        body = {"properties": {"Status": {"select": {"name": STATUS_LABELS[status]}}}}
        self._call("PATCH", f"/pages/{page_id}", body)

    def update_page(self, page_id: str, row: JobRow) -> None:
        """Push a new score (fit score, reason, flags, dates) and the status; values now empty are cleared."""
        props = _page_properties(row)
        body = {
            "properties": {
                "Fit Score": {"number": row.score},
                "Reason": props["Reason"],
                "Flags": props["Flags"],
                "Status": props["Status"],
                "Starts": props.get("Starts", {"date": None}),
                "Deadline": props.get("Deadline", {"date": None}),
            }
        }
        self._call("PATCH", f"/pages/{page_id}", body)

    def archive_page(self, page_id: str) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"archived": True})

    def read_statuses(self, database_id: str) -> dict[str, tuple[Status, datetime]]:
        """Map each page id to its status and last-edit time (naive UTC); pages without a known status are skipped."""
        statuses: dict[str, tuple[Status, datetime]] = {}
        cursor: str | None = None
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            result = self._call("POST", f"/databases/{database_id}/query", body)
            for page in result.get("results", []):
                select = ((page.get("properties") or {}).get("Status") or {}).get("select") or {}
                status = _STATUS_BY_LABEL.get(select.get("name") or "")
                if status is not None:
                    statuses[page["id"]] = (status, _parse_edited(page["last_edited_time"]))
            cursor = result.get("next_cursor")
            if not result.get("has_more") or not cursor:
                return statuses
