"""Portfolio job boards of venture capital firms, on Getro or Consider.

Each board is read through the JSON search its own web page uses (checked live on 2026-11-12):

- Getro: the board page embeds `__NEXT_DATA__` with the network id. Search with
  `POST https://api.getro.com/api/v2/collections/{id}/search/jobs` and the body
  `{"hitsPerPage": 20, "page": 0, "filters": {"searchable_locations": [...]}, "query": "..."}`.
  It needs `Accept`, `Origin` and `Referer` headers (406 without them), `filters` must be an object,
  pages hold 20 jobs whatever is asked, `page` counts from 0 and `results.count` is the total.
- Consider: the board page embeds `window.serverInitialData` with a `csrfToken` and the board id,
  and sets a session cookie. Search with `POST https://{board}/api-boards/search-jobs`, the cookie,
  the header `x-csrf-token` and the body
  `{"meta": {"size": 100}, "board": {...}, "query": {"titlePrefix": "...", "locations": [...]}, "grouped": false}`.
  The next page comes from passing back the response's `meta.sequence`. An empty `locations` list
  matches nothing, so a filter with no value is left out.

Neither list API returns a job's full text, so the description is put together from the fields
it does return; each job's own page is never fetched.
"""

import functools
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import httpx

from jobscout.errors import SearchError
from jobscout.http import RETRY_STATUSES
from jobscout.models import Job
from jobscout.settings import VcBoardsSettings
from jobscout.sources import (
    DESCRIPTION_LIMIT,
    USER_AGENT,
    AreaResult,
    SearchArea,
    as_str,
    fetch_json,
    get_public,
    html_to_text,
    posted_date,
    salary_text,
    web_url,
    within_window,
)

PAUSE_SECONDS = 1.0  # between two requests: the boards are read the way a person would page through them
GETRO_PAGE_SIZE = 20  # Getro ignores any other size
CONSIDER_PAGE_SIZE = 100
_GETRO_API = "https://api.getro.com/api/v2/collections/{id}/search/jobs"
_NEXT_DATA = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_CONSIDER_DATA = re.compile(r"window\.serverInitialData\s*=\s*")
_NETWORK_ID = re.compile(r"\d{1,12}")
_BOARD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_UNKNOWN_PLATFORM = "This board isn't on Getro or Consider, so it can't be read."
_SKILLS_SHOWN = 12
_TAGS_SHOWN = 8
_PLACES_SHOWN = 6  # some postings list twenty cities; the rest is summed up as "and N more"


# --- presets -------------------------------------------------------------------------------------


@functools.cache
def _preset_data() -> tuple[dict[str, str], ...]:
    raw = json.loads(files("jobscout.data").joinpath("vc_boards.json").read_text(encoding="utf-8"))
    boards = raw.get("boards") if isinstance(raw, dict) else None
    presets = []
    for item in boards if isinstance(boards, list) else []:
        if not isinstance(item, dict):
            continue
        preset = {key: as_str(item.get(key)).strip() for key in ("name", "url", "platform", "country")}
        if preset["name"] and web_url(preset["url"]):
            presets.append(preset)
    return tuple(presets)


def load_presets() -> list[dict[str, str]]:
    """The VC boards known to work, each as {"name", "url", "platform", "country"}."""
    return [dict(preset) for preset in _preset_data()]


def _host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def board_label(url: str) -> str:
    """A readable name for a board: the preset's name when the address is a known board, else its host."""
    host = _host(url)
    for preset in _preset_data():
        if _host(preset["url"]) == host:
            return preset["name"]
    return host or url


# --- parsing -------------------------------------------------------------------------------------


def _labels(value: object) -> list[str]:
    """Readable names from a list of strings or of {"label"/"name": ...} objects."""
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        name = as_str(item.get("label") or item.get("name")) if isinstance(item, dict) else as_str(item)
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return names


def _humanize(value: object) -> str:
    """`series_g` -> `series g`; `mid_senior` -> `mid senior`."""
    return as_str(value).replace("_", " ").strip()


def _positive(value: object) -> int:
    """A whole number above zero, else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _cents(value: object) -> float | None:
    return value / 100 if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _place(locations: list[str]) -> str:
    shown = "; ".join(locations[:_PLACES_SHOWN])
    extra = len(locations) - _PLACES_SHOWN
    return f"{shown} and {extra} more" if extra > 0 else shown


def _compose(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"{name}: {value}" for name, value in lines if value)[:DESCRIPTION_LIMIT]


def _description(item: dict, composed: list[tuple[str, str]]) -> str:
    """The job's own text when the list returns it, else a short summary of the fields it does return."""
    text = html_to_text(item.get("description") or item.get("descriptionHtml"))
    return text[:DESCRIPTION_LIMIT] if text else _compose(composed)


def parse_getro_job(item: dict, *, board_url: str, keyword: str, area: str) -> Job | None:
    """Turn one Getro search result into a Job, or None if it has no id."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    org = item.get("organization") if isinstance(item.get("organization"), dict) else {}
    company = as_str(org.get("name")).strip()
    locations = _labels(item.get("locations")) or _labels(item.get("location_details"))
    url = web_url(item.get("url"))
    org_slug, job_slug = as_str(org.get("slug")).strip(), as_str(item.get("slug")).strip()
    if not url and org_slug and job_slug:
        url = f"{board_url}/companies/{org_slug}/jobs/{job_slug}"
    salary = ""
    if item.get("compensation_public") is not False:
        salary = salary_text(
            _cents(item.get("compensation_amount_min_cents")),
            _cents(item.get("compensation_amount_max_cents")),
            item.get("compensation_currency"),
            item.get("compensation_period"),
        )
    stage = _humanize(org.get("stage"))
    tags = ", ".join(_labels(org.get("industry_tags"))[:_TAGS_SHOWN])
    company_facts = ", ".join(x for x in (stage if stage != "other" else "", tags) if x)
    composed = [
        ("Company", f"{company} ({company_facts})" if company and company_facts else company),
        ("Seniority", _humanize(item.get("seniority"))),
        ("Work mode", _humanize(item.get("work_mode"))),
        ("Skills", ", ".join(_labels(item.get("skills"))[:_SKILLS_SHOWN])),
    ]
    return Job(
        source="vcboards",
        job_id=f"getro:{job_id}",
        title=as_str(item.get("title")).strip(),
        company=company,
        location=_place(locations),
        url=url or board_url,
        description=_description(item, composed),
        posted=posted_date(item.get("created_at")),
        keyword=keyword,
        area=area,
        salary=salary,
    )


def parse_consider_job(item: dict, *, board_url: str, keyword: str, area: str) -> Job | None:
    """Turn one Consider search result into a Job, or None if it has no id."""
    job_id = as_str(item.get("jobId")).strip()
    if not job_id:
        return None
    locations = _labels(item.get("locations")) or _labels(item.get("normalizedLocations"))
    if item.get("remote") is True and "Remote" not in locations:
        locations.append("Remote")
    salary = ""
    pay = item.get("salary")
    if isinstance(pay, dict):
        currency, period = pay.get("currency"), pay.get("period")
        salary = salary_text(
            pay.get("minValue"),
            pay.get("maxValue"),
            currency.get("value") if isinstance(currency, dict) else currency,
            period.get("value") if isinstance(period, dict) else period,
        )
    company = as_str(item.get("companyName")).strip()
    staff, years = _positive(item.get("companyStaffCount")), _positive(item.get("minYearsExp"))
    markets = ", ".join(_labels(item.get("markets"))[:_TAGS_SHOWN])
    company_facts = ", ".join(x for x in (f"{staff} people" if staff else "", markets) if x)
    composed = [
        ("Company", f"{company} ({company_facts})" if company and company_facts else company),
        ("Function", ", ".join(_labels(item.get("jobFunctions")) + _labels(item.get("jobTypes")))),
        ("Seniority", ", ".join(_labels(item.get("jobSeniorities")))),
        ("Experience", f"at least {years} year{'s' if years != 1 else ''}" if years else ""),
        ("Work mode", "remote" if item.get("remote") is True else "hybrid" if item.get("hybrid") is True else ""),
        ("Skills", ", ".join(_labels(item.get("skills"))[:_SKILLS_SHOWN])),
    ]
    return Job(
        source="vcboards",
        job_id=f"consider:{job_id}",
        title=as_str(item.get("title")).strip(),
        company=company,
        location=_place(locations),
        url=web_url(item.get("applyUrl")) or web_url(item.get("url")) or board_url,
        description=_description(item, composed),
        posted=posted_date(item.get("timeStamp")),
        keyword=keyword,
        area=area,
        salary=salary,
    )


# --- platform detection --------------------------------------------------------------------------


@dataclass
class _Board:
    """What one board's page tells us, read once per run."""

    platform: str  # "getro" or "consider"
    base: str  # scheme://host of the board, after any redirect
    board_id: str  # Getro network id or Consider board id
    is_parent: bool = True  # Consider only
    csrf: str = ""  # Consider only
    cookie: str = ""  # Consider only: the session cookie the page set


def _json_after(text: str, pattern: re.Pattern[str]) -> Any:
    match = pattern.search(text)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text, match.end())
    except ValueError:
        return None
    return value


def detect_board(page: str, base: str, cookie: str = "") -> _Board | None:
    """Which platform a board page runs on, with what its search needs; None for anything else."""
    match = _NEXT_DATA.search(page)
    if match:
        try:
            data = json.loads(match.group(1))
        except ValueError:
            data = None
        props = data.get("props") if isinstance(data, dict) else None
        page_props = props.get("pageProps") if isinstance(props, dict) else None
        network = page_props.get("network") if isinstance(page_props, dict) else None
        network_id = as_str(network.get("id")).strip() if isinstance(network, dict) else ""
        if _NETWORK_ID.fullmatch(network_id):
            return _Board("getro", base, network_id)
    data = _json_after(page, _CONSIDER_DATA)
    if isinstance(data, dict):
        board = data.get("board")
        board_id = as_str(board.get("id")).strip() if isinstance(board, dict) else ""
        csrf = as_str(data.get("csrfToken")).strip()
        if _BOARD_ID.fullmatch(board_id) and csrf:
            is_parent = board.get("isParent") is not False
            return _Board("consider", base, board_id, is_parent=is_parent, csrf=csrf, cookie=cookie)
    return None


# --- the source ----------------------------------------------------------------------------------


def _page_url(url: str) -> str:
    """The board's job list page: the address as given, or `/jobs` when only the site was given."""
    parts = urlsplit(url)
    if parts.path in ("", "/"):
        return f"{parts.scheme}://{parts.netloc}/jobs"
    return url


def _base(url: httpx.URL) -> str:
    return f"{url.scheme}://{url.netloc.decode('ascii', 'replace')}"


class VcBoardsSource:
    name = "vcboards"

    def __init__(
        self,
        settings: VcBoardsSettings,
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
        self._boards: dict[str, _Board] = {}  # board address -> what its page said, for this run
        self._requested = False

    def areas(self) -> list[SearchArea]:
        areas: dict[str, SearchArea] = {}
        for url in self._settings.boards:
            host = _host(url)
            key = f"vcboards:{host or url}"
            if key in areas:
                continue
            areas[key] = SearchArea(
                key=key,
                label=board_label(url),
                source=self.name,
                searches=max(1, len(self._settings.queries)),
                params={"url": url},
            )
        return list(areas.values())

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Run every query on this board. Stops at the first error or when cancelled."""
        queries = self._settings.queries or [""]
        total = len(queries)
        jobs: dict[str, Job] = {}
        today = self._today()
        progress(0, total, 0)

        def keep(job: Job | None) -> None:
            if job is not None and job.job_id not in jobs and within_window(job.posted, window_seconds, today):
                jobs[job.job_id] = job

        try:
            if self._cancel.is_set():
                return AreaResult(jobs=[], billed=0, complete=False)
            board = self._board(area.params["url"], f"The {area.label} board")
            for done, query in enumerate(queries):
                tick = functools.partial(lambda n: progress(n, total, len(jobs)), done)
                read = self._search(board, query, area.label, keep, tick)
                if read is None:  # cancelled
                    return AreaResult(jobs=list(jobs.values()), billed=0, complete=False)
                progress(done + 1, total, len(jobs))
        except SearchError as exc:
            return AreaResult(jobs=list(jobs.values()), billed=0, complete=False, error=exc)
        return AreaResult(jobs=list(jobs.values()), billed=0, complete=True)

    # --- requests ---

    def _pause(self) -> None:
        if self._requested:
            self._sleep(PAUSE_SECONDS)
        self._requested = True

    def _board(self, url: str, label: str) -> _Board:
        cached = self._boards.get(url)
        if cached is not None:
            return cached
        response = self._get_page(_page_url(url), label)
        cookie = "; ".join(f"{c.name}={c.value}" for c in response.cookies.jar if c.value is not None)
        board = detect_board(response.text, _base(response.url), cookie)
        if board is None:
            raise SearchError(_UNKNOWN_PLATFORM)
        self._boards[url] = board
        return board

    def _get_page(self, url: str, label: str) -> httpx.Response:
        """GET a board's web page, retrying passing failures; any failure becomes a SearchError without the URL."""
        attempts = 3
        for attempt in range(attempts):
            self._pause()
            last = attempt == attempts - 1
            try:
                response = get_public(
                    self._client,
                    url,
                    label,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                )
            except httpx.TimeoutException:
                if last:
                    raise SearchError(f"{label} took too long to answer. It will be retried next run.") from None
                self._sleep(2**attempt)
                continue
            except httpx.HTTPError:
                if last:
                    raise SearchError(f"{label} couldn't be reached. Check your internet connection.") from None
                self._sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUSES and not last:
                self._sleep(2**attempt)
                continue
            status = response.status_code
            if status == 404:
                raise SearchError(f"{label} wasn't found (HTTP 404). Check the address.")
            if status == 429:
                raise SearchError(f"{label} asked us to slow down. It will be retried next run.")
            if status >= 400:
                raise SearchError(f"{label} answered with an error (HTTP {status}).")
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _post(self, url: str, label: str, body: dict, headers: dict[str, str]) -> dict:
        self._pause()
        data = fetch_json(
            self._client, "POST", url, label, json=body, headers={"User-Agent": USER_AGENT, **headers}, sleep=self._sleep
        )
        if not isinstance(data, dict):
            raise SearchError(f"{label} sent an answer that couldn't be read.")
        return data

    def _search(
        self, board: _Board, query: str, area: str, keep: Callable[[Job | None], None], tick: Callable[[], None]
    ) -> int | None:
        """Page through one query, handing each job to `keep`. Returns the postings read, or None if cancelled."""
        if board.platform == "getro":
            return self._search_getro(board, query, area, keep, tick)
        return self._search_consider(board, query, area, keep, tick)

    def _parse_all(self, items: object, parse: Callable[..., Job | None], **kwargs: str) -> list[Job | None]:
        parsed: list[Job | None] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(parse(item, **kwargs))
            except Exception:  # noqa: BLE001 - one malformed job must not lose the others
                continue
        return parsed

    def _search_getro(
        self, board: _Board, query: str, area: str, keep: Callable[[Job | None], None], tick: Callable[[], None]
    ) -> int | None:
        label = f"The {area} board"
        cap = self._settings.max_results_per_board
        filters: dict[str, Any] = {}
        if self._settings.locations:
            filters["searchable_locations"] = list(self._settings.locations)
        headers = {"Accept": "application/json", "Origin": board.base, "Referer": f"{board.base}/"}
        url = _GETRO_API.format(id=board.board_id)
        read, page = 0, 0
        while read < cap:
            if self._cancel.is_set():
                return None
            body = {"hitsPerPage": GETRO_PAGE_SIZE, "page": page, "filters": filters, "query": query}
            data = self._post(url, label, body, headers)
            results = data.get("results") if isinstance(data.get("results"), dict) else {}
            items = results.get("jobs") if isinstance(results.get("jobs"), list) else []
            items = items[: cap - read]
            for job in self._parse_all(items, parse_getro_job, board_url=board.base, keyword=query, area=area):
                keep(job)
            read += len(items)
            tick()
            count = results.get("count")
            more = (page + 1) * GETRO_PAGE_SIZE < count if isinstance(count, int) else len(items) >= GETRO_PAGE_SIZE
            if not items or not more:
                break
            page += 1
        return read

    def _search_consider(
        self, board: _Board, query: str, area: str, keep: Callable[[Job | None], None], tick: Callable[[], None]
    ) -> int | None:
        label = f"The {area} board"
        cap = self._settings.max_results_per_board
        filters: dict[str, Any] = {}
        if query:
            filters["titlePrefix"] = query
        if self._settings.locations:
            filters["locations"] = list(self._settings.locations)
        headers = {"Accept": "application/json", "x-csrf-token": board.csrf}
        if board.cookie:
            headers["Cookie"] = board.cookie
        url = f"{board.base}/api-boards/search-jobs"
        read, sequence, seen = 0, "", set()
        while read < cap:
            if self._cancel.is_set():
                return None
            meta: dict[str, Any] = {"size": min(CONSIDER_PAGE_SIZE, cap - read)}
            if sequence:
                meta["sequence"] = sequence
            body = {
                "meta": meta,
                "board": {"id": board.board_id, "isParent": board.is_parent},
                "query": filters,
                "grouped": False,
            }
            data = self._post(url, label, body, headers)
            items = data.get("jobs") if isinstance(data.get("jobs"), list) else []
            items = items[: cap - read]
            for job in self._parse_all(items, parse_consider_job, board_url=board.base, keyword=query, area=area):
                keep(job)
            read += len(items)
            tick()
            response_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            sequence = as_str(response_meta.get("sequence"))
            total = data.get("total")
            if not items or not sequence or sequence in seen or (isinstance(total, int) and read >= total):
                break
            seen.add(sequence)
        return read
