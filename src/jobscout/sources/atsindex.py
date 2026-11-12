"""An index of many companies' own job boards, searched at once through an Apify actor.

Built for `jtpalms~ats-jobs-index` (Greenhouse, Ashby, Lever, Workable, Recruitee, Personio, Breezy, Teamtailor,
Gem and Pinpoint boards); input and output fields from its input schema and README (build 1.0.20, checked
2026-11-12). A location is a two-letter country code ("IT", sent as `countries`) or a place, region or work
policy as job boards write it ("Milan", "Italy", "Europe", "Remote", sent as `locations`).
"""

from jobscout.models import Job
from jobscout.settings import AtsIndexSettings
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
    text_list,
    window_days,
    with_work_policy,
)

# The actor's `timeRange` choices: jobs new to the index in that many days. A longer window searches every
# open job, kept to those the employer posted within the window.
_TIME_RANGES = ((1, "24h"), (7, "7d"), (14, "14d"))
_WORK_TYPES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def request_payload(settings: AtsIndexSettings, query: str, location: str, window_seconds: int) -> dict:
    """The actor input for one search."""
    days = window_days(window_seconds)
    time_range = next((name for limit, name in _TIME_RANGES if limit >= days), "all")
    payload: dict = {
        "keywords": [query.strip()] if query.strip() else [],  # matched against job titles, as whole words
        "timeRange": time_range,
        "includeDescription": True,
        "maxJobs": settings.max_results_per_search,
    }
    if time_range == "all":
        payload["postedWithinDays"] = days
    if code := country_code(location):
        payload["countries"] = [code]
    elif location.strip():
        payload["locations"] = [location.strip()]
    return payload


def _job_id(item: dict) -> str:
    index_id = first_text(item, "indexId")
    if index_id:
        return index_id
    job_id, ats = first_text(item, "jobId"), first_text(item, "ats", "source")
    return f"{ats}:{job_id}" if job_id and ats else job_id


def _location(item: dict) -> str:
    place = first_text(item, "location") or ", ".join(text_list(item.get("locations")))
    policy = _WORK_TYPES.get(first_text(item, "workplaceType").lower(), "")
    if not policy and item.get("isRemote") is True:
        policy = "Remote"
    return with_work_policy(place, policy)


def _salary(item: dict) -> str:
    pay = item.get("compensation")
    if not isinstance(pay, dict):
        return ""
    text = salary_text(pay.get("min"), pay.get("max"), pay.get("currency"), pay.get("interval"))
    return text or as_str(pay.get("summary")).strip()


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = _job_id(item)
    if not job_id:
        return None
    return Job(
        source="atsindex",
        job_id=job_id,
        title=first_text(item, "title"),
        company=first_text(item, "companyName", "company", "board"),
        location=_location(item),
        url=web_url(item.get("jobUrl")) or web_url(item.get("applyUrl")),
        description=html_to_text(item.get("descriptionText"))[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("postedAt") or item.get("firstSeenAt")),
        keyword=query,
        area=area,
        employment_type=humanize(item.get("employmentType")),
        salary=_salary(item),
    )


class AtsIndexSource(ApifyBoardSource):
    name = "atsindex"
    _settings: AtsIndexSettings

    def payload(self, query: str, location: str, window_seconds: int) -> dict:
        return request_payload(self._settings, query, location, window_seconds)

    def parse(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)
