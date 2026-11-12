"""Wellfound (formerly AngelList Talent) through an Apify actor.

Built for `blackfalcondata~wellfound-scraper`; input and output fields from its input schema and README
(build 0.1.201, checked 2026-11-12). A location is a Wellfound location as in wellfound.com/location/<slug>:
"Berlin", "London", "New York" or "Italy" become the slugs "berlin", "london", "new-york", "italy";
"Remote" asks for remote jobs.
"""

import math
import re

from jobscout.models import Job
from jobscout.settings import WellfoundSettings
from jobscout.sources import (
    DESCRIPTION_LIMIT,
    as_str,
    html_to_text,
    posted_date,
    salary_text,
    web_url,
)
from jobscout.sources._apify_board import (
    ApifyBoardSource,
    first_text,
    humanize,
    is_remote,
    text_list,
    with_work_policy,
)

_JOB_ID = re.compile(r"/jobs/(\d+)")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def location_slug(location: str) -> str:
    """Wellfound's slug for a place: lower case, words joined by hyphens ("New York" -> "new-york")."""
    return _NOT_SLUG.sub("-", location.strip().lower()).strip("-")


def request_payload(settings: WellfoundSettings, query: str, location: str, window_seconds: int) -> dict:
    """The actor input for one search."""
    payload: dict = {
        "query": query,
        "maxResults": settings.max_results_per_search,
        "maxAgeMinutes": max(1, math.ceil(window_seconds / 60)),
        "enrichDetail": False,  # opening each job's page adds run time, billed as platform usage
    }
    if is_remote(location):
        payload["remote"] = True
    elif slug := location_slug(location):
        payload["location"] = [slug]
    return payload


def _job_id(item: dict) -> str:
    job_id = first_text(item, "id", "jobId")
    if job_id:
        return job_id
    match = _JOB_ID.search(first_text(item, "portalUrl", "detailUrl", "url"))
    return match.group(1) if match else ""


def _location(item: dict) -> str:
    place = ", ".join(text_list(item.get("locationNames")))
    if item.get("remote") is True:
        policy = "Remote"
    else:
        kind = as_str(item.get("remoteKind")).strip().lower()
        policy = "Hybrid" if kind == "hybrid" else "Remote" if kind == "remote" else ""
    return with_work_policy(place, policy)


def _salary(item: dict) -> str:
    """Wellfound's own pay text (it names the currency and adds equity) unless the currency is stated on its own."""
    low, high, currency = item.get("salaryMin"), item.get("salaryMax"), item.get("salaryCurrency")
    if as_str(currency).strip():
        return salary_text(low, high, currency, item.get("salaryPeriod"))
    return as_str(item.get("compensation")).strip() or salary_text(low, high)


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = _job_id(item)
    if not job_id:
        return None
    description = html_to_text(item.get("descriptionHtml")) or html_to_text(item.get("description"))
    return Job(
        source="wellfound",
        job_id=job_id,
        title=first_text(item, "title"),
        company=first_text(item, "companyName"),
        location=_location(item),
        url=web_url(item.get("portalUrl")) or web_url(item.get("detailUrl")),
        description=description[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("postedAt") or item.get("datePosted")),
        keyword=query,
        area=area,
        employment_type=humanize(item.get("jobType")),
        salary=_salary(item),
    )


class WellfoundSource(ApifyBoardSource):
    name = "wellfound"
    _settings: WellfoundSettings

    def payload(self, query: str, location: str, window_seconds: int) -> dict:
        return request_payload(self._settings, query, location, window_seconds)

    def parse(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)
