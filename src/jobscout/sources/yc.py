"""Y Combinator's Work at a Startup through an Apify actor.

Built for `nomad-agent~ycombinator-was-scraper`; input and output fields from its input schema and README
(build 0.1.58, checked 2026-11-12). A location is a city, region or country name, or a two-letter country code
("Milan", "Italy", "IT"); "Europe" and "EU" cover every European country; "Remote" asks for remote jobs.
"""

from jobscout.models import Job
from jobscout.settings import YcSettings
from jobscout.sources import DESCRIPTION_LIMIT, html_to_text, posted_date, web_url
from jobscout.sources._apify_board import (
    ApifyBoardSource,
    first_text,
    is_remote,
    text_list,
    window_days,
    with_work_policy,
)
from jobscout.sources.indeed import query_terms, title_matches

SCHEMA_VERSION = "nomad-agent-simple-inventory-search-v2"
MAX_ITEMS = 200  # the actor returns at most 200 jobs a run
_MAX_DAYS = 36500
_WORK_TYPES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def request_payload(settings: YcSettings, query: str, location: str, window_seconds: int) -> dict:
    """The actor input for one search."""
    remote = is_remote(location)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "queries": [query] if query.strip() else [],
        "locations": [location.strip()] if location.strip() and not remote else [],
        "locationTypes": ["remote"] if remote else [],
        "employmentTypes": [],
        "postedWithin": f"{min(window_days(window_seconds), _MAX_DAYS)}d",
        "maxItems": min(settings.max_results_per_search, MAX_ITEMS),
        # The actor would otherwise skip jobs it delivered to this account before, even when this app never
        # saved them (a run that failed later); the app keeps its own record of jobs it has seen.
        "dedupe": {"enabled": False, "key": ""},
    }


def _location(item: dict) -> str:
    place = ", ".join(text_list(item.get("locations"))) or ", ".join(text_list(item.get("applicantLocations")))
    return with_work_policy(place, _WORK_TYPES.get(first_text(item, "workType").lower(), ""))


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = first_text(item, "id")
    if not job_id:
        return None
    description = html_to_text(item.get("descriptionHtml")) or html_to_text(item.get("description"))
    return Job(
        source="yc",
        job_id=job_id,
        title=first_text(item, "title"),
        company=first_text(item, "company"),
        location=_location(item),
        url=web_url(item.get("url")),
        description=description[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("postedAt")),
        keyword=query,
        area=area,
        employment_type=", ".join(text_list(item.get("employmentTypes"), limit=4)),
        salary=first_text(item, "salary"),  # the board's own text; most postings state none
    )


class YcSource(ApifyBoardSource):
    name = "yc"
    _settings: YcSettings

    def payload(self, query: str, location: str, window_seconds: int) -> dict:
        return request_payload(self._settings, query, location, window_seconds)

    def parse(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)

    def keep(self, job: Job, query: str) -> bool:
        # The actor also matches words anywhere in the description, so a "software engineer" search returns
        # postings that only mention working with engineers. Keep titles that name a query term.
        terms = query_terms(query)
        return not terms or title_matches(job.title, terms)
