"""Built in Europe (builtineurope.com): Balderton's board of VC-backed European startups.

Its public search API (checked 2026-11-12) takes `POST /search` with `{"query", "page", "per_page"}`
and answers `{"total", "total_jobs", "page", "per_page", "results": [...]}`. The search is
AI-powered and reads plain language: the site's own country buttons add " in <Country>" to the
query ("all jobs in <Country>" when there is no query), so a country is searched the same way.
Results carry no description, so one is built from the structured fields for the scoring AI.
"""

import re
import threading
import time
from collections.abc import Callable
from datetime import date

import httpx

from jobscout.errors import SearchError
from jobscout.models import Job
from jobscout.settings import BuiltInEuropeSettings
from jobscout.sources import (
    DESCRIPTION_LIMIT,
    USER_AGENT,
    AreaResult,
    SearchArea,
    as_str,
    fetch_json,
    posted_date,
    salary_text,
    web_url,
    within_window,
)

SEARCH_URL = "https://api.builtineurope.com/search"
LABEL = "Built in Europe"
PER_PAGE = 50  # the site asks for 12; 50 and 100 both work
MAX_PAGES = 10
PAUSE_SECONDS = 1.0  # between requests, which are never sent in parallel
EUROPE = "europe"


def title_case(text: object) -> str:
    """Capitalise each word as the site does ("united kingdom" -> "United Kingdom")."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), as_str(text).strip())


def search_query(query: str, country: str) -> str:
    """The query the site sends for a search in one country (`country` empty: no country filter)."""
    query = query.strip()
    if not country:
        return query
    return f"{query} in {title_case(country)}" if query else f"all jobs in {title_case(country)}"


def _location(item: dict) -> str:
    loc = item.get("location")
    parts: list[str] = []
    if isinstance(loc, dict):
        place = title_case(loc.get("locality")) or title_case(loc.get("region"))
        parts = [x for x in (place, title_case(loc.get("country"))) if x]
    text = ", ".join(parts) or title_case(item.get("location_name"))
    policy = as_str(item.get("remote_work_policy")).strip().lower()
    if policy:
        text = f"{text} ({policy})" if text else policy.capitalize()
    return text


def _list_text(value: object, limit: int = 15) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(x for x in (as_str(v).strip() for v in value[:limit]) if x)


def _description(item: dict, company: str, salary: str) -> str:
    """A short plain-text summary of the structured fields, since the API returns no description."""
    industry = as_str(item.get("company_industry")).strip()
    size = as_str(item.get("company_size")).strip()
    about = ", ".join(x for x in (industry, f"{size} employees" if size else "") if x)
    role = " / ".join(x for x in (as_str(item.get("title_role")), as_str(item.get("title_sub_role"))) if x.strip())
    lines = [
        f"Company: {company}" + (f" ({about})" if about else "") if company else "",
        f"Company website: {site}" if (site := as_str(item.get("company_website")).strip()) else "",
        f"Role area: {role}" if role else "",
        f"Seniority: {seniority}" if (seniority := as_str(item.get("seniority")).strip()) else "",
        f"Work policy: {policy}" if (policy := as_str(item.get("remote_work_policy")).strip()) else "",
        f"Location: {place}" if (place := title_case(item.get("location_name"))) else "",
        f"Salary: {salary}" if salary else "",
        f"Skills: {skills}" if (skills := _list_text(item.get("skills"))) else "",
    ]
    return "\n".join(x for x in lines if x)[:DESCRIPTION_LIMIT]


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one search result into a Job, or None if it has no usable id."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    company = as_str(item.get("company_display_name")).strip() or title_case(item.get("company_name"))
    salary = salary_text(
        item.get("salary_min"), item.get("salary_max"), item.get("salary_currency"), item.get("salary_period")
    )
    return Job(
        source="builtineurope",
        job_id=job_id,
        title=as_str(item.get("title_raw")).strip() or title_case(item.get("title")),
        company=company,
        location=_location(item),
        url=web_url(item.get("posting_url")),
        description=_description(item, company, salary),
        posted=posted_date(item.get("first_seen")),
        keyword=query,
        area=area,
        salary=salary,
    )


class BuiltInEuropeSource:
    name = "builtineurope"

    def __init__(
        self,
        settings: BuiltInEuropeSettings,
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

    def areas(self) -> list[SearchArea]:
        s = self._settings
        searches = len(s.queries)
        countries: list[str] = []
        for raw in s.countries:
            country = raw.strip().lower()
            if country and country not in countries:
                countries.append(country)
        if not countries:
            return [SearchArea(f"builtineurope:{EUROPE}", "Europe", self.name, searches, {"country": ""})]
        return [
            SearchArea(f"builtineurope:{c}", title_case(c), self.name, searches, {"country": c}) for c in countries
        ]

    def _pause(self) -> None:
        """Wait between requests; the first request of a run goes at once."""
        if self._requested:
            self._sleep(PAUSE_SECONDS)

    def _fetch_page(self, query: str, page: int, per_page: int) -> tuple[list, int | None]:
        self._requested = True
        body = {"query": query, "page": page, "per_page": per_page}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = fetch_json(self._client, "POST", SEARCH_URL, LABEL, json=body, headers=headers, sleep=self._sleep)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchError(f"{LABEL} sent an answer we couldn't read.")
        total = data.get("total")
        return data["results"], total if isinstance(total, int) and not isinstance(total, bool) else None

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Run every query in this country. Stops at the first error or when cancelled."""
        s = self._settings
        country = area.params.get("country", "")
        today = self._today()
        per_page = min(PER_PAGE, s.max_results_per_query)
        total = len(s.queries)
        jobs: dict[str, Job] = {}
        error: SearchError | None = None
        complete = True
        progress(0, total, 0)
        for done, query in enumerate(s.queries):
            found: set[str] = set()
            for page in range(1, MAX_PAGES + 1):
                self._pause()
                if self._cancel.is_set():
                    complete = False
                    break
                try:
                    results, available = self._fetch_page(search_query(query, country), page, per_page)
                except SearchError as exc:
                    error, complete = exc, False
                    break
                for item in results:
                    if not isinstance(item, dict) or len(found) >= s.max_results_per_query:
                        continue
                    try:
                        job = parse_item(item, query, area.label)
                    except Exception:  # noqa: BLE001 - one malformed item must not lose the others
                        continue
                    if job is None or job.job_id in found or not within_window(job.posted, window_seconds, today):
                        continue
                    found.add(job.job_id)
                    jobs.setdefault(job.job_id, job)
                ran_out = len(results) < per_page or (available is not None and page * per_page >= available)
                if ran_out or len(found) >= s.max_results_per_query:
                    break
            if not complete:
                break
            progress(done + 1, total, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=0, complete=complete, error=error)
