"""Pipeline helpers that drop excluded places and duplicates. Private to jobscout.pipeline."""

from dataclasses import dataclass

from jobscout.db import Database
from jobscout.language import other_language
from jobscout.models import JobRow, JobState, Status
from jobscout.normalize import PlaceMatcher, TermMatcher

_SEEN = (JobState.KEPT, JobState.BELOW_THRESHOLD)


@dataclass
class FilterCounts:
    checked: int
    new: int  # found by this run and passed the filter
    waiting: int  # passed the filter but found by an earlier run (cancelled, or over the scoring limit)
    duplicates: int
    excluded: int  # every job dropped before scoring: the three counts below added up
    places: int = 0
    titles: int = 0
    languages: int = 0


def filter_scraped(
    db: Database,
    places: PlaceMatcher,
    found_now: set[str],
    titles: TermMatcher | None = None,
    languages: frozenset[str] = frozenset(),
) -> FilterCounts:
    """Mark scraped jobs in excluded places, with an excluded title word or clearly written in a language not in
    `languages` EXCLUDED, and repeats DUPLICATE; the first seen stays.

    `found_now`: the pks this run inserted, to tell new jobs from ones waiting since an earlier run.
    """
    rows = db.jobs_in_state(JobState.SCRAPED)  # oldest first, in insertion order
    survivors: set[str] = set()
    dups = by_place = by_title = by_language = 0
    for row in rows:
        if places.match(row.location):
            db.set_state(row.pk, JobState.EXCLUDED)
            by_place += 1
        elif titles is not None and titles.match(row.title):
            db.set_state(row.pk, JobState.EXCLUDED)
            by_title += 1
        elif other_language(f"{row.title}\n{row.description}", languages):
            db.set_state(row.pk, JobState.EXCLUDED)
            by_language += 1
        elif _is_duplicate(db, row, survivors):
            db.set_state(row.pk, JobState.DUPLICATE)
            dups += 1
        else:
            survivors.add(row.pk)
    new = len(survivors & found_now)
    excluded = by_place + by_title + by_language
    return FilterCounts(len(rows), new, len(survivors) - new, dups, excluded, by_place, by_title, by_language)


def _is_duplicate(db: Database, row: JobRow, survivors: set[str]) -> bool:
    """Same title, company and city as a job already scored, or one kept earlier in this pass."""
    if not row.dedup_key.strip("|"):
        return False  # no title, company or city: nothing to compare
    for pk in db.find_by_dedup_key(row.dedup_key):
        if pk == row.pk:
            continue
        if pk in survivors:
            return True
        other = db.get(pk)
        if other is not None and other.state in _SEEN:
            return True
    return False


def drop_kept_duplicates(db: Database) -> int:
    """Safety net before saving: of kept jobs with the same dedup key, one stays.

    A job the user acted on, or one an output already has, is never demoted: the others are. With none
    of those, the first seen stays.
    """
    groups: dict[str, list[JobRow]] = {}
    for row in db.jobs_in_state(JobState.KEPT):  # oldest first
        if row.dedup_key.strip("|"):
            groups.setdefault(row.dedup_key, []).append(row)
    delivered = db.delivered_pks()
    dropped = 0
    for rows in groups.values():
        protected = [r for r in rows if r.status != Status.TO_REVIEW or r.pk in delivered]
        keep = {r.pk for r in (protected or rows[:1])}
        for row in rows:
            if row.pk not in keep:
                db.set_state(row.pk, JobState.DUPLICATE)
                dropped += 1
    return dropped
