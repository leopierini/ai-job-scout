"""LinkedIn jobs through the Apify actor."""

import re
import threading
from collections.abc import Callable
from datetime import date
from urllib.parse import quote

from jobscout.errors import ApifyError
from jobscout.models import Job
from jobscout.settings import LinkedInSettings
from jobscout.sources import AreaResult, SearchArea, as_str, web_url
from jobscout.sources.apify import ApifyClient

DESCRIPTION_LIMIT = 6000
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VIEW_ID = re.compile(r"/jobs/view/(\d+)")

# The actors return the same item fields, but name their per-search results limit differently.
_LIMIT_FIELD = {"meka.im~linkedin-jobs-scraper": "limitPerSource", "curious_coder~linkedin-jobs-scraper": "count"}


def search_url(keyword: str, geo_id: str, seconds: int, sort_by: str) -> str:
    """LinkedIn search URL for a keyword in a region. Never filters by experience level."""
    url = f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_TPR=r{seconds}&geoId={geo_id}"
    return f"{url}&sortBy={sort_by}" if sort_by else url


def request_payload(settings: LinkedInSettings, url: str) -> dict:
    """The actor input for one search. An actor not listed above gets `count`, the original field."""
    limit_field = _LIMIT_FIELD.get(settings.actor, "count")
    return {"urls": [url], "scrapeCompany": settings.scrape_company, limit_field: settings.cap_per_keyword}


def _job_id(item: dict) -> str:
    raw = as_str(item.get("id")).strip()
    if raw:
        return raw
    match = _VIEW_ID.search(as_str(item.get("link")))
    return match.group(1) if match else ""


def _posted(value: object) -> str:
    """Keep only an ISO YYYY-MM-DD date; relative text such as '2 days ago' is dropped."""
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return ""
        return value
    return ""


def parse_item(item: dict, keyword: str, area: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = _job_id(item)
    if not job_id:
        return None
    return Job(
        source="linkedin",
        job_id=job_id,
        title=as_str(item.get("title")),
        company=as_str(item.get("companyName")),
        location=as_str(item.get("location")),
        url=web_url(item.get("link")),
        description=as_str(item.get("descriptionText"))[:DESCRIPTION_LIMIT],
        posted=_posted(item.get("postedAt")),
        keyword=keyword,
        area=area,
        recruiter=as_str(item.get("jobPosterName")),
        recruiter_title=as_str(item.get("jobPosterTitle")),
        recruiter_url=web_url(item.get("jobPosterProfileUrl")),
        employment_type=as_str(item.get("employmentType")),
    )


class LinkedInSource:
    name = "linkedin"

    def __init__(self, settings: LinkedInSettings, apify: ApifyClient, cancel: threading.Event) -> None:
        self._settings = settings
        self._apify = apify
        self._cancel = cancel

    def areas(self) -> list[SearchArea]:
        s = self._settings
        return [
            SearchArea(
                key=f"linkedin:{region.name}",
                label=region.name,
                source=self.name,
                searches=len(s.keywords),
                params={"region": region.name, "geo_id": region.geo_id},
            )
            for region in s.regions
        ]

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """Run every keyword in this region. Stops at the first Apify error or when cancelled."""
        s = self._settings
        total = len(s.keywords)
        jobs: dict[str, Job] = {}
        billed = 0
        error: ApifyError | None = None
        complete = True
        progress(0, total, 0)
        for done, keyword in enumerate(s.keywords):
            if self._cancel.is_set():
                complete = False
                break
            url = search_url(keyword, area.params["geo_id"], window_seconds, s.sort_by)
            try:
                items = self._apify.run_actor(s.actor, request_payload(s, url))
            except ApifyError as exc:
                error, complete = exc, False
                break
            billed += len(items)  # Apify bills every returned item, duplicates included
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = parse_item(item, keyword, area.params["region"])
                except Exception:  # noqa: BLE001 - one malformed item must not lose the area's paid results
                    continue
                if job is not None:
                    jobs.setdefault(job.job_id, job)
            progress(done + 1, total, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=billed, complete=complete, error=error)
