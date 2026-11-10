"""Indeed jobs through the Apify actor."""

import re
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime

from jobscout.errors import ApifyError
from jobscout.models import Job
from jobscout.normalize import fold
from jobscout.settings import IndeedSettings
from jobscout.sources import AreaResult, SearchArea, as_str, web_url
from jobscout.sources.apify import ApifyClient
from jobscout.windows import indeed_from_days

DESCRIPTION_LIMIT = 6000
_TOKEN = re.compile(r'"([^"]+)"|([^\s()"]+)')
_OPERATORS = frozenset({"or", "and", "not"})
_MILLISECOND_EPOCH = 1e11  # epoch values above this are milliseconds, not seconds

# kaix/indeed-scraper takes different input fields and returns nested items. When a date window is set it
# ignores the job type (its input schema, checked 2026-11-10), so it runs each query once, for every type.
KAIX = "kaix~indeed-scraper"


def searches(settings: IndeedSettings) -> list[tuple[str, str]]:
    """The (query, job type) pairs one country runs. An empty job type means every type."""
    if settings.actor == KAIX:
        return [(query, "") for query in settings.queries]
    return [(query, job_type) for query in settings.queries for job_type in settings.job_types]


def request_payload(settings: IndeedSettings, country: str, query: str, job_type: str, from_days: str) -> dict:
    """The actor input for one search."""
    if settings.actor == KAIX:
        return {
            "country": country.upper(),
            "keyword": query,
            "location": "",  # the actor's own default is a US city
            "fromDays": from_days,
            "sort": "date",
            "maxItems": settings.max_rows_per_query,
            "searchMode": "detailed",
        }
    return {
        "country": country,
        "query": query,
        "jobType": job_type,
        "fromDays": from_days,
        "maxRows": settings.max_rows_per_query,
        "enableUniqueJobs": True,
    }


def query_terms(query: str) -> list[str]:
    """Quoted phrases and bare words of a query, case-folded, without operators or `title:` prefixes."""
    terms: list[str] = []
    for phrase, word in _TOKEN.findall(query):
        term = fold(phrase) if phrase else fold(word).removeprefix("title:")
        if term and term not in _OPERATORS and term not in terms:
            terms.append(term)
    return terms


def title_matches(title: str, terms: list[str]) -> bool:
    """True when any term appears in the title as a whole word or phrase."""
    folded = fold(title)
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", folded) for term in terms)


def _first(item: dict, *keys: str) -> str:
    """The first of these fields that holds usable text."""
    return next((text for key in keys if (text := as_str(item.get(key)))), "")


def _location(value: object) -> str:
    if isinstance(value, dict):
        return (
            as_str(value.get("formattedAddressShort"))
            or as_str(value.get("formattedShort"))
            or ", ".join(x for x in (as_str(value.get("city")), as_str(value.get("country"))) if x)
            or as_str(value.get("formattedAddressLong"))
        )
    return as_str(value)


def _posted(value: object) -> str:
    """An ISO date prefix (YYYY-MM-DD) or an epoch converted to a UTC date; otherwise empty."""
    if isinstance(value, str):
        prefix = value[:10]
        try:
            date.fromisoformat(prefix)
        except ValueError:
            return ""
        return prefix
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        seconds = value / 1000 if value > _MILLISECOND_EPOCH else value
        try:
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


_EMAIL = re.compile(r"[^\s@<>?&,;:\"'()\[\]]+@[^\s@<>?&,;:\"'()\[\]]+\.[^\s@<>?&,;:\"'()\[\]]+")


def parse_item(item: dict, query: str, area: str, job_type: str) -> Job | None:
    """Turn one actor item into a Job, or None if it has no usable id."""
    job_id = _first(item, "jobKey", "jobkey", "id").strip()
    if not job_id:
        return None
    emails = item.get("emails")
    email = emails[0].strip() if isinstance(emails, list) and emails and isinstance(emails[0], str) else ""
    if not _EMAIL.fullmatch(email):
        email = ""  # a link is only made from a plain address, never from whatever text the actor returned
    item_type = as_str(item.get("jobType"))
    return Job(
        source="indeed",
        job_id=job_id,
        title=_first(item, "title", "positionName"),
        company=_first(item, "companyName", "company"),
        location=_location(item.get("location")),
        url=web_url(_first(item, "jobUrl", "url")),
        description=_first(item, "descriptionText", "description")[:DESCRIPTION_LIMIT],
        posted=_posted(item.get("datePublished") or item.get("postingDateParsed") or item.get("date")),
        keyword=query,
        area=area,
        recruiter_title="Email from Indeed" if email else "",
        recruiter_url=f"mailto:{email}" if email else "",
        employment_type=item_type or job_type,
    )


def _nested(item: dict, key: str, field: str) -> str:
    """Text of `item[key][field]` when `item[key]` is an object, else empty."""
    value = item.get(key)
    return as_str(value.get(field)) if isinstance(value, dict) else ""


def parse_kaix_item(item: dict, query: str, area: str, job_type: str) -> Job | None:
    """Turn one kaix/indeed-scraper item into a Job, or None if it has no usable id."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    classification = item.get("classification")
    types = classification.get("jobType") if isinstance(classification, dict) else None
    item_type = ", ".join(t for t in types if isinstance(t, str)) if isinstance(types, list) else ""
    return Job(
        source="indeed",
        job_id=job_id,
        title=_nested(item, "title", "text"),
        company=_nested(item, "company", "name"),
        location=_location(item.get("location")),
        url=web_url(_nested(item, "urls", "indeed")),
        description=_nested(item, "description", "text")[:DESCRIPTION_LIMIT],
        posted=_posted(_nested(item, "dates", "posted")),
        keyword=query,
        area=area,
        employment_type=item_type or job_type,
    )


class IndeedSource:
    name = "indeed"

    def __init__(self, settings: IndeedSettings, apify: ApifyClient, cancel: threading.Event) -> None:
        self._settings = settings
        self._apify = apify
        self._cancel = cancel

    def areas(self) -> list[SearchArea]:
        s = self._settings
        return [
            SearchArea(
                key=f"indeed:{country}",
                label=country.upper(),
                source=self.name,
                searches=len(searches(s)),
                params={"country": country},
            )
            for country in s.countries
        ]

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """Run every query and job type in this country. Stops at the first Apify error or when cancelled."""
        s = self._settings
        country = area.params["country"]
        from_days = str(indeed_from_days(window_seconds))
        pairs = searches(s)
        parse = parse_kaix_item if s.actor == KAIX else parse_item
        total = len(pairs)
        jobs: dict[str, Job] = {}
        billed = 0
        error: ApifyError | None = None
        complete = True
        progress(0, total, 0)
        for done, (query, job_type) in enumerate(pairs):
            if self._cancel.is_set():
                complete = False
                break
            try:
                items = self._apify.run_actor(s.actor, request_payload(s, country, query, job_type, from_days))
            except ApifyError as exc:
                error, complete = exc, False
                break
            billed += len(items)  # Apify bills every returned item, duplicates included
            terms = query_terms(query)
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = parse(item, query, country, job_type)
                except Exception:  # noqa: BLE001 - one malformed item must not lose the area's paid results
                    continue
                if job is None or job.job_id in jobs:
                    continue
                # Safety net: Indeed also returns loosely related postings; with no terms, keep everything.
                if terms and not title_matches(job.title, terms):
                    continue
                jobs[job.job_id] = job
            progress(done + 1, total, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=billed, complete=complete, error=error)
