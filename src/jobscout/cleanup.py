"""Rules that archive jobs which are no longer worth looking at."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta

from jobscout.closed import ClosedCheck, Progress
from jobscout.db import Database
from jobscout.models import ACTIVE_STATUSES, JobRow, JobState, Status
from jobscout.normalize import PlaceMatcher
from jobscout.settings import CleanupSettings, Settings


def _parse_day(value: str) -> date | None:
    """The date in the first 10 characters of an ISO string, or None if empty or invalid."""
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def why_stale(row: JobRow, today: date, cfg: CleanupSettings, places: PlaceMatcher) -> str | None:
    """The reason a job should be archived, or None to keep it."""
    if row.status in ACTIVE_STATUSES or row.status in (Status.NOT_INTERESTED, Status.REJECTED):
        return None
    if row.archived_reason is not None:
        return None

    if cfg.excluded_place and (place := places.match(row.location)):
        return f"excluded place ({place})"

    if row.restored:
        return None  # the user brought it back: dates no longer archive it until its status changes

    if cfg.deadline_passed and (deadline := _parse_day(row.deadline)) and deadline < today:
        return f"deadline passed ({deadline.isoformat()})"

    if cfg.start_too_early and (start := _parse_day(row.start_date)):
        earliest = max(cfg.earliest_start or today, today)
        if start < earliest:
            return f"starts {start.isoformat()}, before {earliest.isoformat()}"

    if cfg.too_old and not (row.status == Status.INTERESTED and cfg.interested_exempt_from_age):
        posted = _parse_day(row.posted) or _parse_day(row.first_seen_at)
        if posted and posted < today - timedelta(days=cfg.max_age_days):
            return f"posted {posted.isoformat()}, over {cfg.max_age_days} days ago"

    return None


def run_cleanup(
    db: Database,
    s: Settings,
    today: date,
    dry_run: bool = False,
    status_overrides: dict[str, Status] | None = None,
) -> list[tuple[str, str]]:
    """Archive stale tracked jobs and return (pk, reason) for each one really archived (all of them on a
    dry run, which archives nothing). `status_overrides` (pk -> status) replaces a row's status for the
    check: a dry run uses it to see statuses changed in Notion without saving them."""
    if not s.cleanup.enabled:
        return []
    places = PlaceMatcher(s.filters.excluded_countries, s.filters.excluded_cities)
    stale: list[tuple[str, str]] = []
    for row in db.tracked():
        if status_overrides and row.pk in status_overrides:
            row = replace(row, status=status_overrides[row.pk])
        reason = why_stale(row, today, s.cleanup, places)
        # db.archive re-checks the status: one changed since the read (e.g. to Applied) wins.
        if reason is not None and (dry_run or db.archive(row.pk, reason)):
            stale.append((row.pk, reason))
    return stale


# --- jobs that no longer take applications --------------------------------------------------------

ClosedChecker = Callable[[list[JobRow], Progress], ClosedCheck]  # rows, progress -> result


def closed_candidates(rows: list[JobRow], cfg: CleanupSettings) -> list[JobRow]:
    """The rows the closed-job rule applies to: kept, To review or Interested, not archived or restored."""
    if not (cfg.enabled and cfg.closed):
        return []
    return [
        r
        for r in rows
        if r.state == JobState.KEPT
        and r.status in (Status.TO_REVIEW, Status.INTERESTED)
        and r.archived_reason is None
        and not r.restored
    ]


def _quiet(_done: int, _total: int) -> None:
    pass


def archive_closed(
    db: Database, rows: list[JobRow], check: ClosedChecker, dry_run: bool = False, progress: Progress = _quiet
) -> tuple[list[tuple[str, str]], ClosedCheck]:
    """Check `rows` and archive the closed ones. Returns (pk, reason) for each one archived (all of them on a
    dry run, which archives nothing) and the check's result. No rows: no check, nothing billed."""
    if not rows:
        return [], ClosedCheck()
    result = check(rows, progress)
    # db.archive re-checks the status: one changed since the read (e.g. to Applied) wins.
    archived = [(pk, why) for pk, why in result.closed.items() if dry_run or db.archive(pk, why)]
    return archived, result


def kept_linkedin(db: Database, s: Settings) -> int:
    """How many LinkedIn jobs the next closed-job check would send to Apify (for the cost estimate)."""
    return sum(1 for r in closed_candidates(db.tracked(), s.cleanup) if r.source == "linkedin")
