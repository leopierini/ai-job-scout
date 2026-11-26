"""Remote OK's public feed of remote jobs. Free, no key.

Checked 2026-11-26: `GET https://remoteok.com/api` answers a JSON list whose first element is a legal notice
and the rest are the newest jobs (about 100, the last few days), each with a free-text `location` ("US Only",
"Worldwide", "Europe", a city). Its terms ask to link back to the job on Remote OK and name Remote OK as the
source, or API access is suspended: the app shows the source and opens Remote OK's own link. There is no
keyword search, so the feed is read once a run and matched to the searches here.
"""

from jobscout.models import Job
from jobscout.sources import DESCRIPTION_LIMIT, as_str, html_to_text, posted_date, salary_text, web_url
from jobscout.sources.remote import FeedSource, remote_location

FEED_URL = "https://remoteok.com/api"
LABEL = "Remote OK"


def parse_item(item: dict, query: str, area: str) -> Job | None:
    """Turn one Remote OK job into a Job, or None if it has no id (the legal notice has none)."""
    job_id = as_str(item.get("id")).strip()
    if not job_id:
        return None
    restriction = as_str(item.get("location")).strip()
    salary = salary_text(item.get("salary_min"), item.get("salary_max"), "USD", "year")
    tags = item.get("tags")
    tag_text = ", ".join(t for t in (as_str(x).strip() for x in tags) if t) if isinstance(tags, list) else ""
    header = [
        f"Open to people in: {restriction or 'anywhere'}",
        f"Tags: {tag_text}" if tag_text else "",
        f"Salary: {salary}" if salary else "",
    ]
    text = "\n".join(x for x in header if x) + "\n\n" + html_to_text(item.get("description"))
    return Job(
        source="remoteok",
        job_id=job_id,
        title=as_str(item.get("position")).strip(),
        company=as_str(item.get("company")).strip(),
        location=remote_location(restriction),
        url=web_url(item.get("url")) or web_url(item.get("apply_url")),
        description=text.strip()[:DESCRIPTION_LIMIT],
        posted=posted_date(item.get("date")) or posted_date(item.get("epoch")),
        keyword=query,
        area=area,
        salary=salary,
    )


class RemoteOkSource(FeedSource):
    name = "remoteok"
    label = LABEL
    url = FEED_URL

    def items(self, data: object) -> list | None:
        return data if isinstance(data, list) else None

    def title(self, item: dict) -> str:
        return as_str(item.get("position"))

    def restriction(self, item: dict) -> str:
        return as_str(item.get("location"))

    def parse_item(self, item: dict, query: str, area: str) -> Job | None:
        return parse_item(item, query, area)
