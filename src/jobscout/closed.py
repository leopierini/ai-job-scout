"""Which kept jobs no longer take applications.

LinkedIn jobs are read again by an Apify actor (checked 2026-11-25 against LinkedIn's own pages: it was
the one that flags both "No longer accepting applications" and "Not currently accepting applications").
Jobs on a company's own job board (Ashby, Greenhouse, Lever, ...) are looked up in the board's public feed.
Only a clear "closed" answer counts: a failed request, a block or an unclear row keeps the job.
"""

import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from jobscout.errors import ApifyError, SearchError
from jobscout.models import JobRow
from jobscout.sources import USER_AGENT, get_public
from jobscout.sources.apify import ApifyClient
from jobscout.sources.careers import Board, Posting, board_from_url, normalise_url, read_feed

CLOSED_ACTOR = "automation_craft~linkedin-job-details-scraper"
RUN_FEE_USD = 0.001  # the actor's start event, once per run
JOB_PRICE_USD = 0.0002  # per job row it returns; status rows (not found, blocked, ...) are free
# Jobs per actor run: the run must end inside the run-sync endpoint's 300 s wait (about 1 s per job at 10 at once).
BATCH_SIZE = 100
LINKEDIN_REASON = "closed on LinkedIn"
BOARD_REASON = "no longer on the company's job board"

_LINKEDIN_HOST = re.compile(r"(^|\.)linkedin\.com$", re.IGNORECASE)
_VIEW_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})/?$")
_BARE_ID = re.compile(r"(\d{6,})/?$")


@dataclass
class ClosedCheck:
    closed: dict[str, str] = field(default_factory=dict)  # pk -> reason
    linkedin_jobs: int = 0  # job rows the actor billed
    actor_runs: int = 0
    note: str = ""  # why a part of the check was skipped, or ""


def linkedin_job_id(url: str) -> str | None:
    """The job id in a LinkedIn job address (…/jobs/view/<title>-<id>), or None for any other address."""
    parts = urlsplit(url.strip())
    if not _LINKEDIN_HOST.search(parts.hostname or ""):
        return None
    match = _VIEW_ID.search(parts.path)
    return match.group(1) if match else None


def _status_row_id(item: dict) -> str | None:
    """The job id a status row is about: its input is the address or the bare id we sent."""
    value = str(item.get("input") or "").strip()
    if "/" in value:
        return linkedin_job_id(value)
    match = _BARE_ID.fullmatch(value)
    return match.group(1) if match else None


def linkedin_verdicts(items: list[dict]) -> dict[str, bool]:
    """Job id -> True (closed) or False (open), from the actor's rows. Ids with no clear answer are left out.

    Closed: the posting says so ("No longer accepting applications"), it has no apply button ("Not
    currently accepting applications"), or LinkedIn no longer has it (a `not_found` status row).
    """
    verdicts: dict[str, bool] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "job":
            job_id = str(item.get("jobId") or "").strip()
            if job_id:
                closed = item.get("isClosed") is True or item.get("jobState") == "closed"
                verdicts[job_id] = closed or not item.get("applyButton")
        elif item.get("type") == "status" and item.get("status") == "not_found":
            job_id = _status_row_id(item)
            if job_id:
                verdicts[job_id] = True
    return verdicts


def _check_linkedin(rows: list[JobRow], apify: ApifyClient, cancel: threading.Event, result: ClosedCheck) -> None:
    by_id: dict[str, list[JobRow]] = {}
    for row in rows:
        job_id = linkedin_job_id(row.url) or (row.job_id if row.job_id.isdigit() else None)
        if job_id:
            by_id.setdefault(job_id, []).append(row)
    ids = list(by_id)
    for start in range(0, len(ids), BATCH_SIZE):
        if cancel.is_set():
            return
        batch = ids[start : start + BATCH_SIZE]
        payload = {
            "jobUrls": [f"https://www.linkedin.com/jobs/view/{job_id}" for job_id in batch],
            "exactPostedDate": False,
            "includeDescriptionHtml": False,
            "includeDescriptionMarkdown": False,
            "maxConcurrency": 10,
        }
        try:
            items = apify.run_actor(CLOSED_ACTOR, payload)
        except ApifyError as exc:
            result.note = str(exc)
            return
        result.actor_runs += 1
        result.linkedin_jobs += sum(1 for i in items if isinstance(i, dict) and i.get("type") == "job")
        for job_id, closed in linkedin_verdicts(items).items():
            if closed:
                for row in by_id.get(job_id, []):
                    result.closed[row.pk] = LINKEDIN_REASON


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def _listed(row: JobRow, postings: list[Posting]) -> bool:
    """Whether the board's feed still lists the job: by id in its address, or by the whole address."""
    segments = _segments(row.url)
    address = normalise_url(row.url.split("?", 1)[0])
    for p in postings:
        if p.id and any(s == p.id or s.startswith(f"{p.id}-") for s in segments):
            return True
        if p.url and normalise_url(p.url.split("?", 1)[0]) == address:
            return True
    return False


def _feed(board: Board, client: httpx.Client) -> list[Posting] | None:
    """The board's postings, or None when the feed couldn't be read or listed nothing (no clear answer)."""
    name = "A company's job board"
    try:
        response = get_public(client, board.feed_url, name, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            return None
        postings = read_feed(board, response, name)
    except (httpx.HTTPError, SearchError):
        return None
    return postings or None


def _check_boards(rows: list[JobRow], client: httpx.Client, cancel: threading.Event, result: ClosedCheck) -> None:
    by_board: dict[Board, list[JobRow]] = {}
    for row in rows:
        board = board_from_url(row.url)
        if board is not None:
            by_board.setdefault(board, []).append(row)
    for board, board_rows in by_board.items():
        if cancel.is_set():
            return
        postings = _feed(board, client)
        if postings is None:
            continue
        for row in board_rows:
            if not _listed(row, postings):
                result.closed[row.pk] = BOARD_REASON


def check_closed(
    rows: list[JobRow], apify: ApifyClient | None, client: httpx.Client | None, cancel: threading.Event
) -> ClosedCheck:
    """Which of `rows` no longer take applications. Never raises for a failed check: it keeps the jobs."""
    result = ClosedCheck()
    linkedin = [r for r in rows if r.source == "linkedin"]
    if linkedin and apify is not None:
        _check_linkedin(linkedin, apify, cancel, result)
    others = [r for r in rows if r.source != "linkedin"]
    if others and client is not None:
        _check_boards(others, client, cancel, result)
    return result
