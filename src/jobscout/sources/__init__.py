"""Job sources: what they search and what they return, plus helpers the sources share."""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from jobscout.errors import SearchError
from jobscout.http import request_json
from jobscout.models import Job

DESCRIPTION_LIMIT = 6000
# Sent to the free sources: names the app, never the person running it.
USER_AGENT = "ai-job-scout/1.0 (open-source job search app)"
_MILLISECOND_EPOCH = 1e11  # epoch values above this are milliseconds, not seconds


@dataclass
class SearchArea:
    """One unit of searching with its own time window, e.g. a LinkedIn region."""

    key: str
    label: str
    source: str
    searches: int
    params: dict = field(default_factory=dict)


@dataclass
class AreaResult:
    jobs: list[Job]
    billed: int
    complete: bool
    error: SearchError | None = None


def as_str(value: object) -> str:
    """Text from an actor field: strings as they are, numbers as text, anything else (None, lists, dicts) empty."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def web_url(value: object) -> str:
    """A link from an actor field, kept only if it is an http or https address; anything else is empty."""
    text = as_str(value).strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return ""
    return text if scheme in ("http", "https") else ""


def posted_date(value: object) -> str:
    """An ISO date prefix (YYYY-MM-DD) or an epoch (seconds or milliseconds) as a UTC date; otherwise empty."""
    if isinstance(value, str):
        prefix = value.strip()[:10]
        try:
            date.fromisoformat(prefix)
        except ValueError:
            return ""
        return prefix
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        seconds = value / 1000 if value > _MILLISECOND_EPOCH else value
        try:
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


def within_window(posted: str, window_seconds: int, today: date) -> bool:
    """True unless the job was surely posted before the window: a missing or odd date keeps it.

    Free sources return every open job, not only new ones; this keeps old postings out of a run.
    The window is counted in whole days, so a job posted on the window's first day is kept.
    """
    try:
        day = date.fromisoformat(posted)
    except ValueError:
        return True
    days = max(1, -(-window_seconds // 86400))  # round up
    return day >= today - timedelta(days=days)


def _amount(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
        return number if number > 0 else None
    return None


_PERIODS = {
    "year": "a year", "yearly": "a year", "annual": "a year", "annually": "a year", "y": "a year",
    "month": "a month", "monthly": "a month", "m": "a month",
    "week": "a week", "weekly": "a week",
    "day": "a day", "daily": "a day",
    "hour": "an hour", "hourly": "an hour", "h": "an hour",
}  # fmt: skip


def salary_text(low: object, high: object, currency: object = "", period: object = "") -> str:
    """A readable pay range such as "EUR 38,000–45,000 a year", or "" when no amount is given."""
    lo, hi = _amount(low), _amount(high)
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    fmt = "{:,.0f}".format
    if lo is not None and hi is not None and lo != hi:
        amount = f"{fmt(lo)}–{fmt(hi)}"
    elif lo is not None and hi is not None:
        amount = fmt(lo)
    elif lo is not None:
        amount = f"from {fmt(lo)}"
    else:
        amount = f"up to {fmt(hi)}"
    code = as_str(currency).strip().upper()
    per = _PERIODS.get(as_str(period).strip().lower().removeprefix("per ").removeprefix("per_"), "")
    return " ".join(x for x in (code, amount, per) if x)


class _TextParser(HTMLParser):
    _BREAKS = frozenset({"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BREAKS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: object) -> str:
    """Plain text from an HTML fragment: tags dropped, entities decoded, blank lines collapsed."""
    text = as_str(markup)
    if not text:
        return ""
    if "<" not in text:
        return html.unescape(text).strip()
    parser = _TextParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup: fall back to stripping tags crudely
        return re.sub(r"<[^>]+>", " ", html.unescape(text)).strip()
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch_json(
    client: httpx.Client,
    method: str,
    url: str,
    label: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """`request_json` for a free source: any failure becomes a SearchError naming `label`, never the URL."""
    kwargs: dict[str, Any] = {"json": json, "headers": headers, "timeout": 30}
    if sleep is not None:
        kwargs["sleep"] = sleep
    try:
        return request_json(client, method, url, **kwargs)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise SearchError(f"{label} wasn't found (HTTP 404). Check the address.") from None
        if status == 429:
            raise SearchError(f"{label} asked us to slow down. It will be retried next run.") from None
        raise SearchError(f"{label} answered with an error (HTTP {status}).") from None
    except httpx.TimeoutException:
        raise SearchError(f"{label} took too long to answer. It will be retried next run.") from None
    except httpx.HTTPError:
        raise SearchError(f"{label} couldn't be reached. Check your internet connection.") from None


class Source(Protocol):
    name: str

    def areas(self) -> list[SearchArea]: ...

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """Search one area. `progress(done_searches, total_searches, postings_so_far)`."""
        ...
