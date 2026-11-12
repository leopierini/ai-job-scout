"""Welcome to the Jungle (which now includes Otta) through an Apify actor.

Built for `clearpath~welcome-to-the-jungle-jobs-api`; input and output fields from its input schema and README
(build 0.0.43, checked 2026-11-12). A location is either a two-letter country code ("IT", sent as `countryCode`)
or a city, region or country name ("Milan", "Italy", sent as `location`); "Remote" asks for fully remote jobs.
"""

import re

from jobscout.models import Job
from jobscout.settings import WttjSettings
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
    country_code,
    first_text,
    humanize,
    is_remote,
    window_days,
    with_work_policy,
)

# The actor's `datePosted` choices, in days; a longer window asks for "any" and is filtered here.
_DATE_POSTED_DAYS = (1, 3, 7, 14, 30)
_REMOTE_LABELS = {"fulltime": "Remote", "partial": "Partly remote", "punctual": "Occasionally remote"}
_SLUG = re.compile(r"/jobs/([^/?#]+)")


def date_posted(window_seconds: int) -> str:
    """Smallest `datePosted` value that covers the window, or "any" when none does."""
    days = window_days(window_seconds)
    return next((str(d) for d in _DATE_POSTED_DAYS if d >= days), "any")


def request_payload(settings: WttjSettings, query: str, location: str, window_seconds: int) -> dict:
    """The actor input for one search."""
    payload: dict = {
        "query": query,
        "searchOnlyInJobTitle": True,  # matching anywhere in the posting returns (and bills) unrelated jobs
        "sortBy": "date",
        "datePosted": date_posted(window_seconds),
        "includeDetails": True,  # the description and the full company record
        "maxItems": settings.max_results_per_search,
    }
    if is_remote(location):
        payload["remote"] = ["fulltime"]
    elif code := country_code(location):
        payload["countryCode"] = code
    elif location.strip():
        payload["location"] = location.strip()
    return payload


def _job_id(item: dict) -> str:
    job_id = first_text(item, "id", "reference", "jobId")
    if job_id:
        return job_id
    match = _SLUG.search(as_str(item.get("url")))
    return match.group(1) if match else ""


def _location(item: dict) -> str:
    places: list[str] = []
    offices = item.get("offices")
    for office in offices if isinstance(offices, list) else []:
        if not isinstance(office, dict):
            continue
        city = as_str(office.get("city")).strip()
        country = as_str(office.get("country_code")).strip().upper()
        place = ", ".join(x for x in (city, country) if x) or as_str(office.get("address")).strip()
        if place and place not in places:
            places.append(place)
    remote = _REMOTE_LABELS.get(as_str(item.get("remote")).strip().lower(), "")
    return with_work_policy("; ".join(places[:3]), remote)


def _description(item: dict) -> str:
    parts = [html_to_text(item.get("description")), html_to_text(item.get("profile"))]
    text = "\n\n".join(p for p in parts if p) or html_to_text(item.get("summary"))
    return text[:DESCRIPTION_LIMIT]


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = _job_id(item)
    if not job_id:
        return None
    return Job(
        source="wttj",
        job_id=job_id,
        title=first_text(item, "name", "title"),
        company=first_text(item, "organizationName", "companyName"),
        location=_location(item),
        url=web_url(item.get("url")) or web_url(item.get("applyUrl")),
        description=_description(item),
        posted=posted_date(item.get("publishedAt") or item.get("postedDate")),
        keyword=query,
        area=area,
        employment_type=humanize(item.get("contractType")),
        salary=salary_text(
            item.get("salaryMin"), item.get("salaryMax"), item.get("salaryCurrency"), item.get("salaryPeriod")
        ),
    )


class WttjSource(ApifyBoardSource):
    name = "wttj"
    _settings: WttjSettings

    def payload(self, query: str, location: str, window_seconds: int) -> dict:
        return request_payload(self._settings, query, location, window_seconds)

    def parse(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)

    def window_filtered(self, window_seconds: int) -> bool:
        return date_posted(window_seconds) != "any"
