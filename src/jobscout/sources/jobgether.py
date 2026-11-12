"""Jobgether's public job API (remote-first). Free, no key.

Checked 2026-11-12 against https://jobgether.com/astroapi/ai/jobs/docs: `POST /api/v1/jobs` takes a
JSON body with `keyword`, `locations`, `experience`, `sort`, `page` (capped at 10) and `limit`
(capped at 25), and answers `{"jobs": [...], "pagination": {"page", "limit", "hasMore"}}`. POST keeps
the filters out of the address: robots.txt disallows query strings on this path. Results are sorted
newest first, so paging stops once a whole page is older than the run's window. Jobs carry no
description, so one is built from the structured fields for the scoring AI.
"""

import re
import threading
import time
from collections.abc import Callable
from datetime import date

import httpx

from jobscout.errors import SearchError
from jobscout.models import Job
from jobscout.settings import JobgetherSettings
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

SEARCH_URL = "https://jobgether.com/api/v1/jobs"
LABEL = "Jobgether"
PER_PAGE = 25  # the API's cap
MAX_PAGES = 10  # the API's cap
PAUSE_SECONDS = 2.0  # robots.txt asks for Crawl-delay: 2
_SALARY = re.compile(r"\s*([\d.,]+)\s*(?:-|–)\s*([\d.,]+)\s*([A-Za-z]{3})?\s*|\s*([\d.,]+)\s*([A-Za-z]{3})?\s*")


def request_body(settings: JobgetherSettings, query: str, page: int, limit: int) -> dict:
    """The JSON body for one page of one query. Empty filters are left out (anywhere, any level)."""
    body: dict = {"keyword": query.strip(), "sort": "date", "page": page, "limit": limit}
    locations = [x.strip() for x in settings.locations if x.strip()]
    experience = [x.strip() for x in settings.experience if x.strip()]
    if locations:
        body["locations"] = locations
    if experience:
        body["experience"] = experience
    return body


def parse_salary(value: object) -> str:
    """A range such as "60000-80000 EUR" as readable text; text that doesn't parse is kept as it is."""
    text = as_str(value).strip()
    if not text:
        return ""
    match = _SALARY.fullmatch(text)
    if match:
        if match.group(1):
            parsed = salary_text(match.group(1), match.group(2), match.group(3) or "")
        else:
            parsed = salary_text(match.group(4), match.group(4), match.group(5) or "")
        if parsed:
            return parsed
    return text


def _list_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(x for x in (as_str(v).strip() for v in value) if x)


def _description(item: dict, salary: str) -> str:
    """A short plain-text summary of the structured fields, since the API returns no description."""
    lines = [
        f"Job functions: {functions}" if (functions := _list_text(item.get("jobFunctions"))) else "",
        f"Location: {place}" if (place := as_str(item.get("location")).strip()) else "",
        f"Remote: {remote}" if (remote := as_str(item.get("remote")).strip()) else "",
        f"Contract: {contract}" if (contract := as_str(item.get("contractType")).strip()) else "",
        f"Experience: {level}" if (level := as_str(item.get("experience")).strip()) else "",
        f"Salary: {salary}" if salary else "",
    ]
    return "\n".join(x for x in lines if x)[:DESCRIPTION_LIMIT]


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one API job into a Job, or None if it has no usable id."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    place = as_str(item.get("location")).strip()
    remote = as_str(item.get("remote")).strip()
    location = f"{place} ({remote})" if place and remote else place or remote
    salary = parse_salary(item.get("salaryRange"))
    return Job(
        source="jobgether",
        job_id=job_id,
        title=as_str(item.get("title")).strip(),
        company=as_str(item.get("company")).strip(),
        location=location,
        url=web_url(item.get("url")),
        description=_description(item, salary),
        posted=posted_date(item.get("postedAt")),
        keyword=query,
        area=area,
        employment_type=as_str(item.get("contractType")).strip(),
        salary=salary,
    )


class JobgetherSource:
    name = "jobgether"

    def __init__(
        self,
        settings: JobgetherSettings,
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
        return [SearchArea("jobgether:all", "Jobgether", self.name, len(self._settings.queries), {})]

    def _pause(self) -> None:
        """Wait between requests; the first request of a run goes at once."""
        if self._requested:
            self._sleep(PAUSE_SECONDS)

    def _fetch_page(self, query: str, page: int, limit: int) -> tuple[list, bool]:
        self._requested = True
        body = request_body(self._settings, query, page, limit)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            data = fetch_json(self._client, "POST", SEARCH_URL, LABEL, json=body, headers=headers, sleep=self._sleep)
        except SearchError as exc:
            if "(HTTP 400)" in str(exc):  # the API names a bad filter value this way
                raise SearchError(
                    f"{LABEL} didn't accept a location or experience setting. Check them in Settings."
                ) from None
            raise
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SearchError(f"{LABEL} sent an answer we couldn't read.")
        pagination = data.get("pagination")
        has_more = pagination.get("hasMore") is True if isinstance(pagination, dict) else False
        return data["jobs"], has_more

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Run every query with the location and experience filters. Stops at the first error or when cancelled."""
        s = self._settings
        today = self._today()
        limit = min(PER_PAGE, s.max_results_per_query)
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
                    items, has_more = self._fetch_page(query, page, limit)
                except SearchError as exc:
                    error, complete = exc, False
                    break
                parsed = in_window = 0
                for item in items:
                    if not isinstance(item, dict) or len(found) >= s.max_results_per_query:
                        continue
                    try:
                        job = parse_item(item, query, area.label)
                    except Exception:  # noqa: BLE001 - one malformed item must not lose the others
                        continue
                    if job is None:
                        continue
                    parsed += 1
                    if not within_window(job.posted, window_seconds, today):
                        continue
                    in_window += 1
                    if job.job_id not in found:
                        found.add(job.job_id)
                        jobs.setdefault(job.job_id, job)
                # Newest first: a page wholly older than the window means every later page is too.
                older_only = parsed > 0 and in_window == 0
                if not has_more or not items or older_only or len(found) >= s.max_results_per_query:
                    break
            if not complete:
                break
            progress(done + 1, total, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=0, complete=complete, error=error)
