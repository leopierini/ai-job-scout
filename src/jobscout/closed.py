"""Which kept jobs no longer take applications.

LinkedIn jobs are read again by an Apify actor (checked 2026-11-25 against LinkedIn's own pages: it was
the one that flags both "No longer accepting applications" and "Not currently accepting applications").
Jobs on a company's own job board (Ashby, Greenhouse, Lever, ...) are looked up in the board's public feed.
Only a clear "closed" answer counts: a failed request, a block or an unclear row keeps the job.
"""

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from jobscout.errors import ApifyError, SearchError
from jobscout.models import JobRow
from jobscout.sources import USER_AGENT, get_public
from jobscout.sources.apify import ApifyClient
from jobscout.sources.careers import Board, board_from_url, read_feed

CLOSED_ACTOR = "automation_craft~linkedin-job-details-scraper"
RUN_FEE_USD = 0.001  # the actor's start event, once per run
JOB_PRICE_USD = 0.0002  # per job row it returns; status rows (not found, blocked, ...) are free
# Jobs per actor run: the run must end inside the run-sync endpoint's 300 s wait. A 67-job run took 139 s
# (about 2 s a job with the exact posted date on, which the check turns off), so 50 leaves a wide margin.
BATCH_SIZE = 50
LINKEDIN_REASON = "closed on LinkedIn"
BOARD_REASON = "no longer on the company's job board"

_LINKEDIN_HOST = re.compile(r"(^|\.)linkedin\.com$", re.IGNORECASE)
_VIEW_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})/?$")
_BARE_ID = re.compile(r"(\d{6,})/?$")

Progress = Callable[[int, int], None]  # (rows checked so far, rows to check)


def _no_progress(_done: int, _total: int) -> None:
    pass


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
                # "Not currently accepting applications" shows as an apply button that is there but empty;
                # a row without the field at all says nothing (a partial row, or the actor changed its output).
                no_button = "applyButton" in item and not item["applyButton"]
                verdicts[job_id] = closed or no_button
        elif item.get("type") == "status" and item.get("status") == "not_found":
            job_id = _status_row_id(item)
            if job_id:
                verdicts[job_id] = True
    return verdicts


def _by_linkedin_id(rows: list[JobRow]) -> dict[str, list[JobRow]]:
    by_id: dict[str, list[JobRow]] = {}
    for row in rows:
        job_id = linkedin_job_id(row.url) or (row.job_id if row.job_id.isdigit() else None)
        if job_id:
            by_id.setdefault(job_id, []).append(row)
    return by_id


def _check_linkedin(
    by_id: dict[str, list[JobRow]], apify: ApifyClient, cancel: threading.Event, result: ClosedCheck, done: Progress
) -> None:
    """Read the jobs again in batches. A limit or a rejected token stops the check; any other failure skips
    only its batch (those jobs stay, and are checked next run)."""
    ids = list(by_id)
    unchecked = 0
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
            if exc.kind in ("limit", "auth"):
                result.note = str(exc)
                return
            result.actor_runs += 1  # the run may have started, and its start was billed
            unchecked += len(batch)
            done(sum(len(by_id[i]) for i in batch))
            continue
        result.actor_runs += 1
        result.linkedin_jobs += sum(1 for i in items if isinstance(i, dict) and i.get("type") == "job")
        for job_id, closed in linkedin_verdicts(items).items():
            if closed:
                for row in by_id.get(job_id, []):
                    result.closed[row.pk] = LINKEDIN_REASON
        done(sum(len(by_id[i]) for i in batch))
    if unchecked:
        result.note = f"Apify couldn't read {unchecked} LinkedIn jobs. They stay, and are checked again next run."


@dataclass(frozen=True)
class _FeedKeys:
    ids: frozenset[str]  # every job id the feed holds, listed or not
    slugs: frozenset[str]  # the last part of each job's address (Recruitee addresses carry a slug, not the id)


_AFTER = {"greenhouse": "jobs", "workable": "j", "personio": "job", "teamtailor": "jobs", "recruitee": "o"}
_QUERY_IDS = {"greenhouse": "gh_jid", "ashby": "ashby_jid"}
_LEADING_DIGITS = re.compile(r"^(\d+)")


def _job_key(row: JobRow, board: Board) -> tuple[str, str] | None:
    """What identifies the job on its board: ("id", …) or ("slug", …) for Recruitee; None when the address
    names no job (the board itself, a tracking link): then the job is not judged."""
    if row.source == "careers":  # stored as "ats:board:id" by the careers source
        ats, _, rest = row.job_id.partition(":")
        job_id = rest.partition(":")[2]
        return ("id", job_id) if ats == board.ats and job_id else None
    parts = urlsplit(row.url.strip())
    # The board must be told from the job's own host and path, never from a link inside its query.
    if board_from_url(f"{parts.scheme}://{parts.netloc}{parts.path}") != board:
        return None
    query = parse_qs(parts.query)
    if (name := _QUERY_IDS.get(board.ats)) and query.get(name, [""])[0].strip():
        return "id", query[name][0].strip()
    segments = [s for s in parts.path.split("/") if s]
    if board.ats in ("ashby", "lever"):  # /<board>/<job id>[/application]
        index = next((i for i, s in enumerate(segments) if s.lower() == board.token.lower()), None)
        found = segments[index + 1] if index is not None and index + 1 < len(segments) else ""
        return ("id", found) if found else None
    after = _AFTER[board.ats]
    index = next((i for i, s in enumerate(segments) if s.lower() == after), None)
    if index is None or index + 1 >= len(segments):
        return None
    found = segments[index + 1]
    if board.ats == "recruitee":
        return "slug", found
    if board.ats == "teamtailor":  # /jobs/<id>-<title>
        match = _LEADING_DIGITS.match(found)
        return ("id", match.group(1)) if match else None
    return "id", found


def _raw_keys(board: Board, response: httpx.Response) -> tuple[set[str], set[str]]:
    """Ids and slugs the parsed postings leave out: Ashby's unlisted jobs, Recruitee's slugs."""
    if board.ats not in ("ashby", "recruitee"):
        return set(), set()
    try:
        data: Any = response.json()
    except ValueError:
        return set(), set()
    items = data.get("jobs" if board.ats == "ashby" else "offers") if isinstance(data, dict) else None
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    ids = {str(i.get("id")).strip() for i in items if i.get("id") is not None}
    slugs = {str(i.get("slug")).strip() for i in items if i.get("slug")}
    return ids, slugs


def _feed(board: Board, client: httpx.Client) -> _FeedKeys | None:
    """What the board's feed holds, or None when it couldn't be read or held nothing (no clear answer)."""
    name = "A company's job board"
    try:
        response = get_public(client, board.feed_url, name, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            return None
        postings = read_feed(board, response, name)
    except (httpx.HTTPError, SearchError):
        return None
    raw_ids, raw_slugs = _raw_keys(board, response)
    ids = {p.id for p in postings if p.id} | raw_ids
    slugs = {s for p in postings if p.url and (s := urlsplit(p.url).path.rstrip("/").rsplit("/", 1)[-1])} | raw_slugs
    return _FeedKeys(frozenset(ids), frozenset(slugs)) if ids or slugs else None


def _check_boards(
    by_board: dict[Board, list[tuple[JobRow, tuple[str, str]]]],
    client: httpx.Client,
    cancel: threading.Event,
    result: ClosedCheck,
    done: Progress,
) -> None:
    for board, board_rows in by_board.items():
        if cancel.is_set():
            return
        feed = _feed(board, client)
        if feed is not None:
            for row, (kind, value) in board_rows:
                if value not in (feed.slugs if kind == "slug" else feed.ids):
                    result.closed[row.pk] = BOARD_REASON
        done(len(board_rows))


def check_closed(
    rows: list[JobRow],
    apify: ApifyClient | None,
    client: httpx.Client | None,
    cancel: threading.Event,
    progress: Progress = _no_progress,
) -> ClosedCheck:
    """Which of `rows` no longer take applications. Never raises for a failed check: it keeps the jobs.

    `progress` is told how many of the rows that can be checked are done, after each batch and each board.
    """
    result = ClosedCheck()
    linkedin = _by_linkedin_id([r for r in rows if r.source == "linkedin"]) if apify is not None else {}
    by_board: dict[Board, list[tuple[JobRow, tuple[str, str]]]] = {}
    if client is not None:
        for row in rows:
            if row.source != "linkedin" and (board := board_from_url(row.url)) is not None:
                if (key := _job_key(row, board)) is not None:
                    by_board.setdefault(board, []).append((row, key))
    total = sum(map(len, linkedin.values())) + sum(map(len, by_board.values()))
    checked = 0

    def done(count: int) -> None:
        nonlocal checked
        checked += count
        progress(checked, total)

    if linkedin and apify is not None:
        _check_linkedin(linkedin, apify, cancel, result, done)
    if by_board and client is not None:
        _check_boards(by_board, client, cancel, result, done)
    return result
