"""Remotive's public API of remote jobs. Free, no key.

Checked 2026-11-26 against https://github.com/remotive-io/remote-jobs-api (where remotive.com/api-documentation
points): `GET https://remotive.com/api/remote-jobs` answers `{"jobs": [...]}` with every active job, each with
`candidate_required_location` ("Worldwide", "USA", "LATAM, Europe, USA"). Its terms ask to link back to the job
on Remotive and name Remotive as the source (the app shows the source and opens Remotive's own link), and to
call at most four times a day; more than two calls a minute are blocked. So the feed is read once a run, with
no retry, and matched to the searches here. The documented `search` and `category` filters were ignored on the
day it was checked (every answer held the same 16 jobs), which is one more reason not to send one request per
search. Jobs are shown 24 hours after Remotive publishes them.
"""

from jobscout.models import Job
from jobscout.sources import DESCRIPTION_LIMIT, as_str, html_to_text, posted_date, web_url
from jobscout.sources.remote import FeedSource, remote_location

FEED_URL = "https://remotive.com/api/remote-jobs"
LABEL = "Remotive"


def _job_type(value: object) -> str:
    """"full_time" → "Full time"."""
    text = as_str(value).strip().replace("_", " ")
    return text[:1].upper() + text[1:].lower()


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one Remotive job into a Job, or None if it has no id."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    restriction = as_str(item.get("candidate_required_location")).strip()
    salary = as_str(item.get("salary")).strip()
    job_type = _job_type(item.get("job_type"))
    header = [
        f"Open to people in: {restriction or 'anywhere'}",
        f"Category: {category}" if (category := as_str(item.get("category")).strip()) else "",
        f"Job type: {job_type}" if job_type else "",
        f"Salary: {salary}" if salary else "",
    ]
    text = "\n".join(x for x in header if x) + "\n\n" + html_to_text(item.get("description"))
    return Job(
        source="remotive",
        job_id=job_id,
        title=as_str(item.get("title")).strip(),
        company=as_str(item.get("company_name")).strip(),
        location=remote_location(restriction),
        url=web_url(item.get("url")),
        description=text.strip()[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("publication_date")),
        keyword=query,
        area=area,
        employment_type=job_type,
        salary=salary,
    )


class RemotiveSource(FeedSource):
    name = "remotive"
    label = LABEL
    url = FEED_URL
    retries = 1  # a repeat within a minute can get the app blocked
    publish_delay_days = 1  # jobs show up a day after their publication date
    min_hours_between_reads = 6  # its terms ask for at most four calls a day

    def items(self, data: object) -> list | None:
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return jobs if isinstance(jobs, list) else None

    def title(self, item: dict) -> str:
        return as_str(item.get("title"))

    def restriction(self, item: dict) -> str:
        return as_str(item.get("candidate_required_location"))

    def parse_item(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)
