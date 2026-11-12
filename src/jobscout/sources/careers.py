"""Companies' own job boards: Ashby, Greenhouse, Lever, Recruitee, Workable, Personio, Teamtailor.

Each careers page in the settings is one search area. The job system behind a page is told from its
address (jobs.ashbyhq.com/…, boards.greenhouse.io/…) or, for a company's own careers page, from the
job-board addresses its HTML embeds. Then the system's public feed is read in one request; no key is
needed and nothing is billed. Endpoint shapes were checked against live public boards on 2026-11-12.
"""

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

from jobscout.errors import SearchError
from jobscout.http import RETRY_STATUSES
from jobscout.models import Job
from jobscout.normalize import fold
from jobscout.settings import CareerPagesSettings
from jobscout.sources import (
    DESCRIPTION_LIMIT,
    USER_AGENT,
    AreaResult,
    SearchArea,
    as_str,
    html_to_text,
    posted_date,
    salary_text,
    web_url,
    within_window,
)
from jobscout.sources.indeed import title_matches

PAUSE_SECONDS = 1.0  # between two requests of this source
_ATTEMPTS = 3

UNKNOWN_SYSTEM = (
    "Couldn't tell which job system this careers page uses. "
    "Paste the address of the job board itself (e.g. jobs.ashbyhq.com/…)."
)


@dataclass(frozen=True)
class Board:
    """A job board on one system: `ats` names the system, `token` the company's board on it."""

    ats: str  # ashby, greenhouse, lever, recruitee, workable, personio, teamtailor
    token: str
    host: str = ""  # lever: "api.lever.co" or "api.eu.lever.co"; personio and teamtailor: the board's host

    @property
    def feed_url(self) -> str:
        t = quote(self.token, safe="")
        if self.ats == "ashby":
            return f"https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true"
        if self.ats == "greenhouse":
            # The list endpoint returns pay ranges too when asked (checked live), so no request per job.
            return f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true&pay_transparency=true"
        if self.ats == "lever":
            return f"https://{self.host}/v0/postings/{t}?mode=json"
        if self.ats == "recruitee":
            return f"https://{t}.recruitee.com/api/offers/"
        if self.ats == "workable":
            return f"https://apply.workable.com/api/v1/widget/accounts/{t}?details=true"
        if self.ats == "personio":
            return f"https://{self.host}/xml"
        return f"https://{self.host}/jobs.rss"  # teamtailor

    @property
    def key(self) -> str:
        """The board's part of a job id: stable across runs and unique across systems."""
        return fold(self.host if self.ats == "teamtailor" else self.token)


# --- telling the system from an address or a page -------------------------------------------------

_SLUG = r"[A-Za-z0-9][\w%-]*"
_SUB = r"[A-Za-z0-9][A-Za-z0-9-]*"
_ASHBY = re.compile(rf"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?({_SLUG})", re.IGNORECASE)
_GREENHOUSE = (
    re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s<>]*?&(?:amp;)?)?for=([\w-]+)",
        re.IGNORECASE,
    ),
    re.compile(r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([\w-]+)", re.IGNORECASE),
    re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)", re.IGNORECASE),
)
_LEVER = re.compile(r"(?:jobs|api)(\.eu)?\.lever\.co/(?:v0/postings/)?([\w-]+(?:\.[\w-]+)*)", re.IGNORECASE)
_RECRUITEE = re.compile(rf"(?<![\w.-])({_SUB})\.recruitee\.com", re.IGNORECASE)
_WORKABLE = (
    re.compile(r"apply\.workable\.com/(?:api/v\d+/(?:widget/)?accounts/)?([\w-]+)", re.IGNORECASE),
    re.compile(rf"(?<![\w.-])({_SUB})\.workable\.com", re.IGNORECASE),
)
_PERSONIO = re.compile(rf"(?<![\w.-])({_SUB})\.jobs\.personio\.(de|com)", re.IGNORECASE)
_TEAMTAILOR = re.compile(rf"(?<![\w.-])({_SUB})\.teamtailor\.com", re.IGNORECASE)
_TEAMTAILOR_MARK = re.compile(r"teamtailor", re.IGNORECASE)

# Path parts and subdomains that are the system's own pages, never a company's board.
_NOT_A_BOARD = frozenset({
    "api", "app", "apply", "assets", "blog", "careers", "cdn", "docs", "embed", "help", "images", "j", "jobs",
    "partner", "posting-api", "resources", "static", "status", "support", "v0", "v1", "www",
})  # fmt: skip


def _first(pattern: re.Pattern, text: str, group: int = 1) -> re.Match | None:
    return next((m for m in pattern.finditer(text) if m.group(group).lower() not in _NOT_A_BOARD), None)


def find_board(text: str, page_url: str = "") -> Board | None:
    """The first job board named in an address or a page's HTML; None when there is none.

    With `page_url`, a page built on Teamtailor (its assets give it away) is that site's own board.
    """
    text = text.replace("\\/", "/")  # addresses inside JSON in a page's scripts
    if m := _first(_ASHBY, text):
        return Board("ashby", unquote(m.group(1)))
    for pattern in _GREENHOUSE:
        if m := _first(pattern, text):
            return Board("greenhouse", m.group(1).lower())
    if m := _first(_LEVER, text, 2):
        return Board("lever", m.group(2), "api.eu.lever.co" if m.group(1) else "api.lever.co")
    if m := _first(_RECRUITEE, text):
        return Board("recruitee", m.group(1).lower())
    for pattern in _WORKABLE:
        if m := _first(pattern, text):
            return Board("workable", m.group(1).lower())
    if m := _first(_PERSONIO, text):
        slug, tld = m.group(1).lower(), m.group(2).lower()
        return Board("personio", slug, f"{slug}.jobs.personio.{tld}")
    if m := _first(_TEAMTAILOR, text):
        slug = m.group(1).lower()
        return Board("teamtailor", slug, f"{slug}.teamtailor.com")
    if page_url and _TEAMTAILOR_MARK.search(text):
        host = urlsplit(page_url).netloc.lower()
        return Board("teamtailor", host, host) if host else None
    return None


def board_from_url(url: str) -> Board | None:
    """The board a careers address points at, told without any request; None for any other page."""
    parts = urlsplit(url.strip())
    if parts.path.lower().rstrip("/").endswith(("/jobs.rss", "/jobs.json")) and parts.netloc:
        host = parts.netloc.lower()
        return Board("teamtailor", host, host)
    return find_board(f"{parts.netloc}{parts.path}?{parts.query}")


def normalise_url(url: str) -> str:
    """A careers address with a lower-case scheme and host and no trailing slash or fragment."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


_HOST_PREFIXES = ("www.", "careers.", "career.", "jobs.", "job.", "karriere.", "work.", "join.", "apply.")


def _pretty(slug: str) -> str:
    words = [w for w in re.split(r"[-_.\s]+", unquote(slug)) if w]
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in words)


def company_label(url: str) -> str:
    """A company name read from a careers address: "jobs.ashbyhq.com/example-labs" → "Example Labs"."""
    board = board_from_url(url)
    if board is not None and board.ats != "teamtailor":
        return _pretty(board.token)
    host = urlsplit(url.strip()).hostname or ""
    if board is not None and host.endswith(".teamtailor.com"):
        return _pretty(board.token)
    while host.startswith(_HOST_PREFIXES):
        host = host.split(".", 1)[1]
    return _pretty(host.split(".", 1)[0]) or url.strip()


# --- reading the feeds -----------------------------------------------------------------------------


@dataclass
class Posting:
    """One job as a feed gives it, before the filters."""

    id: str
    title: str
    company: str = ""
    location: str = ""
    remote: bool = False
    url: str = ""
    posted: str = ""
    employment_type: str = ""
    salary: str = ""
    description: str = ""


@dataclass
class Feed:
    """A board's feed split into its items, with what every item shares."""

    items: list
    company: str = ""
    host: str = ""


def _text(value: object) -> str:
    return as_str(value).strip()


def _join(*parts: object, sep: str = ", ") -> str:
    """The non-empty parts, each once (ignoring case and accents), joined by `sep`."""
    kept: list[str] = []
    for part in parts:
        text = _text(part)
        if text and fold(text) not in {fold(k) for k in kept}:
            kept.append(text)
    return sep.join(kept)


def _description(*parts: str) -> str:
    return "\n\n".join(p for p in (x.strip() for x in parts) if p)[:DESCRIPTION_LIMIT]


def _dicts(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _field(data: object, key: str) -> object:
    return data.get(key) if isinstance(data, dict) else None


def _period(interval: object) -> str:
    """'1 YEAR' (Ashby), 'per-year-salary' (Lever) or 'year' → 'year'; anything else as it is."""
    text = _text(interval).lower()
    match = re.search(r"year|month|week|day|hour", text)
    return match.group(0) if match else text


def _cents(value: object) -> float | None:
    return value / 100 if isinstance(value, (int, float)) and not isinstance(value, bool) else None


_ASHBY_TYPES = {
    "fulltime": "Full-time", "parttime": "Part-time", "intern": "Internship",
    "contract": "Contract", "temporary": "Temporary",
}  # fmt: skip


def _ashby(job: dict, feed: Feed) -> Posting | None:
    if job.get("isListed") is False:
        return None
    secondary = [x.get("location") for x in _dicts(job.get("secondaryLocations"))]
    comp = job.get("compensation") if isinstance(job.get("compensation"), dict) else {}
    pay = next((c for c in _dicts(comp.get("summaryComponents")) if c.get("compensationType") == "Salary"), {})
    salary = salary_text(
        pay.get("minValue"), pay.get("maxValue"), pay.get("currencyCode"), _period(pay.get("interval"))
    )
    kind = _text(job.get("employmentType"))
    return Posting(
        id=_text(job.get("id")),
        title=_text(job.get("title")),
        location=_join(job.get("location"), *secondary, sep="; "),
        remote=job.get("isRemote") is True or fold(_text(job.get("workplaceType"))) == "remote",
        url=web_url(job.get("jobUrl")),
        posted=posted_date(job.get("publishedAt")),
        employment_type=_ASHBY_TYPES.get(kind.lower(), kind),
        salary=salary
        or _text(comp.get("scrapeableCompensationSalarySummary"))
        or _text(comp.get("compensationTierSummary")),
        description=_description(html_to_text(job.get("descriptionHtml")) or _text(job.get("descriptionPlain"))),
    )


def _greenhouse(job: dict, feed: Feed) -> Posting:
    location = job.get("location")
    ranges = [
        salary_text(_cents(r.get("min_cents")), _cents(r.get("max_cents")), r.get("currency_type"))
        for r in _dicts(job.get("pay_input_ranges"))
    ]
    return Posting(
        id=_text(job.get("id")),
        title=_text(job.get("title")),
        company=_text(job.get("company_name")),
        location=_text(location.get("name")) if isinstance(location, dict) else "",
        url=web_url(job.get("absolute_url")),
        posted=posted_date(job.get("first_published") or job.get("updated_at")),
        salary=_join(*ranges, sep="; "),
        # The content is HTML escaped once more: unescape it before reading the tags.
        description=_description(html_to_text(html.unescape(as_str(job.get("content"))))),
    )


def _lever(job: dict, feed: Feed) -> Posting:
    cats = job.get("categories") if isinstance(job.get("categories"), dict) else {}
    everywhere = cats.get("allLocations") if isinstance(cats.get("allLocations"), list) else []
    pay = job.get("salaryRange") if isinstance(job.get("salaryRange"), dict) else {}
    salary = salary_text(pay.get("min"), pay.get("max"), pay.get("currency"), _period(pay.get("interval")))
    sections = [f"{_text(x.get('text'))}\n{html_to_text(x.get('content'))}" for x in _dicts(job.get("lists"))]
    return Posting(
        id=_text(job.get("id")),
        title=_text(job.get("text")),
        location=_join(cats.get("location"), *everywhere, sep="; "),
        remote=fold(_text(job.get("workplaceType"))) == "remote",
        url=web_url(job.get("hostedUrl")),
        posted=posted_date(job.get("createdAt")),
        employment_type=_text(cats.get("commitment")),
        salary=salary or _text(job.get("salaryDescriptionPlain"))[:200],
        description=_description(
            _text(job.get("descriptionPlain")) or html_to_text(job.get("description")),
            *sections,
            _text(job.get("additionalPlain")) or html_to_text(job.get("additional")),
        ),
    )


def _recruitee(job: dict, feed: Feed) -> Posting:
    places = [_join(x.get("city") or x.get("name"), x.get("country")) for x in _dicts(job.get("locations"))]
    location = _join(*places, sep="; ") or _join(job.get("city"), job.get("country"))
    if not location and job.get("remote") is not True:
        location = _text(job.get("location"))
    pay = job.get("salary") if isinstance(job.get("salary"), dict) else {}
    return Posting(
        id=_text(job.get("id")),
        title=_text(job.get("title")),
        company=_text(job.get("company_name")),
        location=location,
        remote=job.get("remote") is True,
        url=web_url(job.get("careers_url")),
        posted=posted_date(job.get("published_at") or job.get("created_at")),
        employment_type=_text(job.get("employment_type_code")).replace("_", " ").capitalize(),
        salary=salary_text(pay.get("min"), pay.get("max"), pay.get("currency"), pay.get("period")),
        description=_description(html_to_text(job.get("description")), html_to_text(job.get("requirements"))),
    )


def _workable(job: dict, feed: Feed) -> Posting:
    places = [_join(x.get("city"), x.get("country")) for x in _dicts(job.get("locations")) if not x.get("hidden")]
    return Posting(
        id=_text(job.get("shortcode")) or _text(job.get("code")),
        title=_text(job.get("title")),
        company=feed.company,
        location=_join(*places, sep="; ") or _join(job.get("city"), job.get("country")),
        remote=job.get("telecommuting") is True,
        url=web_url(job.get("url")) or web_url(job.get("shortlink")),
        posted=posted_date(job.get("published_on") or job.get("created_at")),
        employment_type=_text(job.get("employment_type")),
        description=_description(html_to_text(job.get("description"))),
    )


def _xml(content: bytes, name: str) -> ET.Element:
    """Parse a feed's XML, refusing any DOCTYPE or entity declaration (they can expand into gigabytes)."""
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SearchError(f"{name} sent a feed we won't read (it declares a document type).")
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        raise SearchError(f"{name} sent something we couldn't read.") from None


def _child(node: ET.Element | None, tag: str) -> str:
    found = node.find(tag) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def _personio(job: ET.Element, feed: Feed) -> Posting:
    sections = [f"{_child(d, 'name')}\n{html_to_text(_child(d, 'value'))}" for d in job.iter("jobDescription")]
    job_id = _child(job, "id")
    return Posting(
        id=job_id,
        title=_child(job, "name"),
        company=_child(job, "subcompany"),
        location=_join(*(o.text for o in job.iter("office")), sep="; "),
        url=f"https://{feed.host}/job/{quote(job_id, safe='')}" if job_id else "",
        posted=posted_date(_child(job, "createdAt")),
        employment_type=_join(_child(job, "schedule").capitalize(), _child(job, "employmentType")),
        description=_description(*sections),
    )


_TT = "{https://teamtailor.com/locations}"
_TT_ID = re.compile(r"/jobs/(\d+)")


def _rss_date(value: str) -> str:
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return posted_date(value)
    return (moment.astimezone(UTC) if moment.tzinfo else moment).date().isoformat()


def _teamtailor(item: ET.Element, feed: Feed) -> Posting:
    link = web_url(_child(item, "link"))
    match = _TT_ID.search(link)
    places = [
        _join(_child(x, f"{_TT}city") or _child(x, f"{_TT}name"), _child(x, f"{_TT}country"))
        for x in item.iter(f"{_TT}location")
    ]
    return Posting(
        id=match.group(1) if match else _child(item, "guid") or link,
        title=_child(item, "title"),
        company=feed.company,
        location=_join(*places, sep="; "),
        remote=fold(_child(item, "remoteStatus")) in ("fully", "remote"),
        url=link,
        posted=_rss_date(_child(item, "pubDate")),
        description=_description(html_to_text(_child(item, "description"))),
    )


def read_feed(board: Board, response: httpx.Response, name: str) -> list[Posting]:
    """Every posting in a board's feed. A malformed item is skipped; the others are kept."""
    parse: Callable[[Any, Feed], Posting | None]
    if board.ats in ("personio", "teamtailor"):
        root = _xml(response.content, name)
        if board.ats == "personio":
            feed, parse = Feed(list(root.iter("position")), host=board.host), _personio
        else:
            feed, parse = Feed(list(root.iter("item")), company=_child(root.find("channel"), "title")), _teamtailor
    else:
        try:
            data: Any = response.json()
        except ValueError:
            raise SearchError(f"{name} sent something we couldn't read.") from None
        if board.ats == "lever":
            feed, parse = Feed(_dicts(data)), _lever
        elif board.ats == "workable":
            feed, parse = Feed(_dicts(_field(data, "jobs")), company=_text(_field(data, "name"))), _workable
        elif board.ats == "recruitee":
            feed, parse = Feed(_dicts(_field(data, "offers"))), _recruitee
        else:
            feed, parse = Feed(_dicts(_field(data, "jobs"))), _ashby if board.ats == "ashby" else _greenhouse
    postings: list[Posting] = []
    for item in feed.items:
        try:
            posting = parse(item, feed)
        except Exception:  # noqa: BLE001 - one malformed item must not lose the others
            continue
        if posting is not None:
            postings.append(posting)
    return postings


# --- the source ------------------------------------------------------------------------------------


class CareerPagesSource:
    name = "careers"

    def __init__(
        self,
        settings: CareerPagesSettings,
        client: httpx.Client,
        cancel: threading.Event,
        *,
        today: Callable[[], date] = date.today,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = client
        self._cancel = cancel
        self._today = today
        self._sleep = sleep
        self._requests = 0

    def areas(self) -> list[SearchArea]:
        areas: dict[str, SearchArea] = {}
        for page in self._settings.pages:
            key = f"careers:{normalise_url(page)}"
            if key not in areas:
                areas[key] = SearchArea(
                    key=key, label=company_label(page), source=self.name, searches=1, params={"url": page.strip()}
                )
        return list(areas.values())

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Read one company's job board and keep the jobs that pass the filters and the time window."""
        progress(0, 1, 0)
        try:
            found = self._postings(area)
        except SearchError as exc:
            return AreaResult(jobs=[], billed=0, complete=False, error=exc)
        if found is None:  # cancelled
            return AreaResult(jobs=[], billed=0, complete=False)
        jobs = self._keep(*found, area, window_seconds)
        progress(1, 1, len(jobs))
        return AreaResult(jobs=jobs, billed=0, complete=True)

    def _postings(self, area: SearchArea) -> tuple[Board, list[Posting]] | None:
        """The board behind the area's page and its postings; None when cancelled before a request."""
        url = area.params["url"]
        board = board_from_url(url)
        if board is None:
            if self._cancel.is_set():
                return None
            page = self._get(url, f"The careers page of {area.label}")
            board = find_board(page.text, str(page.url))
            if board is None:
                raise SearchError(UNKNOWN_SYSTEM)
        if self._cancel.is_set():
            return None
        name = f"The job board of {area.label}"
        return board, read_feed(board, self._get(board.feed_url, name), name)

    def _keep(self, board: Board, postings: list[Posting], area: SearchArea, window_seconds: int) -> list[Job]:
        s = self._settings
        words = [(w, fold(w)) for w in s.title_words if fold(w)]
        places = [fold(p) for p in s.locations if fold(p)]
        today = self._today()
        jobs: dict[str, Job] = {}
        for p in postings:
            if not p.id or not p.title:
                continue
            keyword = next((w for w, term in words if title_matches(p.title, [term])), "")
            if words and not keyword:
                continue
            location = p.location
            if p.remote and "remote" not in fold(location):
                location = f"{location} (remote)" if location else "Remote"
            if places and p.location and not (title_matches(location, places) or (p.remote and "remote" in places)):
                continue
            if not within_window(p.posted, window_seconds, today):
                continue
            job_id = f"{board.ats}:{board.key}:{p.id}"
            jobs.setdefault(
                job_id,
                Job(
                    source=self.name,
                    job_id=job_id,
                    title=p.title,
                    company=p.company or area.label,
                    location=location,
                    url=p.url,
                    description=p.description,
                    posted=p.posted,
                    keyword=keyword,
                    area=area.label,
                    employment_type=p.employment_type,
                    salary=p.salary,
                ),
            )
        return list(jobs.values())

    def _get(self, url: str, name: str) -> httpx.Response:
        """GET with a pause between requests and retries for passing failures.

        Any failure becomes a SearchError naming `name`, never the URL.
        """
        if self._requests:
            self._sleep(PAUSE_SECONDS)
        self._requests += 1
        headers = {"User-Agent": USER_AGENT}
        for attempt in range(_ATTEMPTS):
            last = attempt == _ATTEMPTS - 1
            try:
                response = self._client.get(url, headers=headers, timeout=30, follow_redirects=True)
            except httpx.TimeoutException:
                if last:
                    raise SearchError(f"{name} took too long to answer. It will be retried next run.") from None
                self._sleep(2**attempt)
                continue
            except httpx.HTTPError:
                if last:
                    raise SearchError(f"{name} couldn't be reached. Check your internet connection.") from None
                self._sleep(2**attempt)
                continue
            status = response.status_code
            if status in RETRY_STATUSES and not last:
                self._sleep(2**attempt)
                continue
            if status == 404:
                raise SearchError(f"{name} wasn't found (HTTP 404). Check the address.")
            if status == 429:
                raise SearchError(f"{name} asked us to slow down. It will be retried next run.")
            if status >= 400:
                raise SearchError(f"{name} answered with an error (HTTP {status}).")
            return response
        raise AssertionError("unreachable")  # pragma: no cover
