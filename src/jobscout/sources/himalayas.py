"""Himalayas' public search API of remote jobs. Free, no key.

Checked 2026-11-26 against https://himalayas.app/docs/remote-jobs-api and its OpenAPI file:
`GET https://himalayas.app/jobs/api/search` takes `q`, `country` (one per request, ISO alpha-2; it includes the
jobs open worldwide unless `exclude_worldwide` is set), `sort=recent` and a 1-based `page` of 20, and answers
`{"jobs": [...], "totalCount"}`. Each job has `locationRestrictions` (country names; empty means worldwide),
`pubDate` (when it was last updated, Unix seconds) and `applicationLink`. Requests without a key share one
public allowance, and answers are cached for up to an hour.

Its API terms (himalayas.app/terms, 113) allow showing the jobs in your own app, on conditions the app meets:
every Apply goes through `applicationLink` (the job's link here), and Himalayas is named as the source. The
search matches descriptions too ("Sales Manager" for "product manager"), so a job is kept only when its title
has every word of the search, and only when it is open to people in Europe.
"""

import threading
import time
from collections.abc import Callable
from datetime import date
from urllib.parse import urlencode, urlsplit

import httpx

from jobscout.errors import SearchError
from jobscout.models import Job
from jobscout.settings import HimalayasSettings
from jobscout.sources import (
    DESCRIPTION_LIMIT,
    USER_AGENT,
    AreaResult,
    SearchArea,
    as_str,
    fetch_json,
    html_to_text,
    posted_date,
    salary_text,
    web_url,
    within_window,
)
from jobscout.sources.remote import matching_query, open_to_europe, remote_location

SEARCH_URL = "https://himalayas.app/jobs/api/search"
LABEL = "Himalayas"
MAX_PAGES = 5  # of 20 jobs each, the API's page size
PAUSE_SECONDS = 1.0  # between requests, which are never sent in parallel


def search_url(query: str, country: str, page: int) -> str:
    """The address for one page of one search, newest first; no country means anywhere."""
    params = {"q": query.strip(), "sort": "recent", "page": page}
    if country:
        params["country"] = country
    return f"{SEARCH_URL}?{urlencode(params)}"


def _job_id(item: dict) -> str:
    """"company/job" from the job's address on Himalayas (its guid), or the guid itself."""
    guid = as_str(item.get("guid")).strip()
    try:
        parts = [p for p in urlsplit(guid).path.split("/") if p]
    except ValueError:
        return guid
    if len(parts) == 4 and parts[0] == "companies" and parts[2] == "jobs":
        return f"{parts[1]}/{parts[3]}"
    return guid


def _list_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(x for x in (as_str(v).strip() for v in value) if x)


def _places(value: object) -> list[str]:
    return [x for x in (as_str(v).strip() for v in value) if x] if isinstance(value, list) else []


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one Himalayas job into a Job, or None if it has no id."""
    job_id = _job_id(item)
    if not job_id:
        return None
    places = _places(item.get("locationRestrictions"))
    salary = salary_text(item.get("minSalary"), item.get("maxSalary"), item.get("currency"), item.get("salaryPeriod"))
    seniority = _list_text(item.get("seniority"))
    employment = as_str(item.get("employmentType")).strip()
    open_to = ", ".join(places) if len(places) <= 10 else f"{len(places)} countries, including {', '.join(places[:10])}"
    header = [
        f"Open to people in: {open_to or 'anywhere'}",
        f"Seniority: {seniority}" if seniority else "",
        f"Employment: {employment}" if employment else "",
        f"Categories: {categories}" if (categories := _list_text(item.get("categories"))) else "",
        f"Salary: {salary}" if salary else "",
    ]
    body = html_to_text(item.get("description")) or as_str(item.get("excerpt")).strip()
    text = "\n".join(x for x in header if x) + "\n\n" + body
    return Job(
        source="himalayas",
        job_id=job_id,
        title=as_str(item.get("title")).strip(),
        company=as_str(item.get("companyName")).strip(),
        location=remote_location(places),
        url=web_url(item.get("applicationLink")) or web_url(item.get("guid")),
        description=text.strip()[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("pubDate")),
        keyword=query,
        area=area,
        employment_type=employment,
        salary=salary,
    )


class HimalayasSource:
    name = "himalayas"

    def __init__(
        self,
        settings: HimalayasSettings,
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
        self._requested = False

    def _searches(self) -> list[tuple[str, str]]:
        """(query, country) pairs: each query in each country, or once with no country."""
        countries = self._settings.countries or [""]
        return [(q, c) for q in self._settings.queries for c in countries]

    def areas(self) -> list[SearchArea]:
        return [SearchArea("himalayas:all", LABEL, self.name, len(self._searches()), {})]

    def _pause(self) -> None:
        """Wait between requests; the first request of a run goes at once."""
        if self._requested:
            self._sleep(PAUSE_SECONDS)

    def _fetch_page(self, query: str, country: str, page: int) -> list:
        self._requested = True
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        url = search_url(query, country, page)
        try:
            data = fetch_json(self._client, "GET", url, LABEL, headers=headers, sleep=self._sleep)
        except SearchError as exc:
            if "(HTTP 400)" in str(exc):  # the API's answer to a country it doesn't know
                raise SearchError(f"{LABEL} didn't accept the country {country}. Check it in Settings.") from None
            raise
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SearchError(f"{LABEL} sent an answer we couldn't read.")
        return data["jobs"]

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Run each search, newest first, until a page is older than the window. Stops at an error or a cancel."""
        s = self._settings
        today = self._today()
        searches = self._searches()
        jobs: dict[str, Job] = {}
        error: SearchError | None = None
        complete = True
        progress(0, len(searches), 0)
        for done, (query, country) in enumerate(searches):
            found: set[str] = set()
            for page in range(1, MAX_PAGES + 1):
                self._pause()
                if self._cancel.is_set():
                    complete = False
                    break
                try:
                    items = self._fetch_page(query, country, page)
                except SearchError as exc:
                    error, complete = exc, False
                    break
                dated = in_window = 0
                for item in items:
                    if not isinstance(item, dict) or len(found) >= s.max_results_per_query:
                        continue
                    try:
                        job = parse_item(item, query, area.label)
                    except Exception:  # noqa: BLE001 - one malformed item must not lose the others
                        continue
                    if job is None:
                        continue
                    dated += 1
                    if not within_window(job.posted, window_seconds, today):
                        continue
                    in_window += 1
                    if matching_query(job.title, [query]) is None:
                        continue
                    if not open_to_europe(_places(item.get("locationRestrictions"))):
                        continue
                    if job.job_id not in found:
                        found.add(job.job_id)
                        jobs.setdefault(job.job_id, job)
                # Newest first: a page wholly older than the window means every later page is too. A page can
                # hold fewer than 20 jobs before the end (the API's docs), so only an empty one ends the search.
                older_only = dated > 0 and in_window == 0
                if not items or older_only or len(found) >= s.max_results_per_query:
                    break
            if not complete:
                break
            progress(done + 1, len(searches), len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=0, complete=complete, error=error)
