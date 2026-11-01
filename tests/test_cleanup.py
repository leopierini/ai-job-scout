from datetime import date

import pytest
from conftest import make_job, make_row

from jobscout.cleanup import run_cleanup, why_stale
from jobscout.models import ScoreResult, Status
from jobscout.normalize import PlaceMatcher
from jobscout.settings import CleanupSettings, Settings

TODAY = date(2026, 11, 1)


def _cfg(**kw):
    return CleanupSettings(earliest_start=date(2027, 5, 1), **kw)


PLACES = PlaceMatcher(["Austria"], [])


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(status=Status.APPLIED, deadline="2020-01-01"), None),
        (dict(status=Status.NOT_INTERESTED, deadline="2020-01-01"), None),
        (dict(status=Status.REJECTED, deadline="2020-01-01"), None),
        (dict(archived_reason="old", deadline="2020-01-01"), None),
        (dict(deadline="2026-10-31"), "deadline passed (2026-10-31)"),
        (dict(deadline="2026-11-01"), None),
        (dict(deadline="not a date"), None),
        (dict(start_date="2026-12-01"), "starts 2026-12-01, before 2027-05-01"),
        (dict(start_date="2027-05-01"), None),
        (dict(posted="2026-08-01"), "posted 2026-08-01, over 60 days ago"),
        (dict(posted="2026-09-02"), None),
        (dict(posted="2026-08-01", status=Status.INTERESTED), None),
        (dict(location="Vienna, Austria"), "excluded place (Austria)"),
        (dict(posted="", first_seen_at="2026-08-01T10:00:00"), "posted 2026-08-01, over 60 days ago"),
        (dict(posted="", first_seen_at=""), None),
    ],
)
def test_why_stale(kw, expected):
    assert why_stale(make_row(**kw), TODAY, _cfg(), PLACES) == expected


def test_start_before_today_uses_today_when_no_earliest_start():
    cfg = CleanupSettings()
    assert why_stale(make_row(start_date="2026-10-01"), TODAY, cfg, PLACES) == "starts 2026-10-01, before 2026-11-01"
    assert why_stale(make_row(start_date="2026-11-01"), TODAY, cfg, PLACES) is None


def test_restored_job_is_exempt_from_date_rules_but_not_excluded_places():
    for kw in (dict(deadline="2020-01-01"), dict(start_date="2026-12-01"), dict(posted="2026-08-01")):
        assert why_stale(make_row(restored=True, **kw), TODAY, _cfg(), PLACES) is None
    assert why_stale(make_row(restored=True, location="Vienna, Austria"), TODAY, _cfg(), PLACES) is not None


def test_rules_can_be_switched_off():
    row = make_row(deadline="2020-01-01")
    assert why_stale(row, TODAY, _cfg(deadline_passed=False), PLACES) is None
    row = make_row(start_date="2026-12-01")
    assert why_stale(row, TODAY, _cfg(start_too_early=False), PLACES) is None
    row = make_row(posted="2026-08-01")
    assert why_stale(row, TODAY, _cfg(too_old=False), PLACES) is None
    row = make_row(location="Vienna, Austria")
    assert why_stale(row, TODAY, _cfg(excluded_place=False), PLACES) is None


def test_interested_age_exemption_can_be_switched_off():
    row = make_row(posted="2026-08-01", status=Status.INTERESTED)
    cfg = _cfg(interested_exempt_from_age=False)
    assert why_stale(row, TODAY, cfg, PLACES) == "posted 2026-08-01, over 60 days ago"


def _stored(db, job_id, deadline="2020-01-01", **job_kw):
    j = make_job(job_id=job_id, **job_kw)
    db.insert_scraped([j], {j.pk: f"k{job_id}"})
    db.save_score(ScoreResult(j.pk, 80, "r", [], "", deadline), min_score=70)
    return j.pk


def test_run_cleanup_archives_stale_jobs(db):
    stale = _stored(db, "1")
    fresh = _stored(db, "2", deadline="")
    result = run_cleanup(db, Settings(), TODAY)
    assert result == [(stale, "deadline passed (2020-01-01)")]
    assert db.get(stale).archived_reason == "deadline passed (2020-01-01)"
    assert db.get(fresh).archived_reason is None


def test_run_cleanup_uses_excluded_places_from_filters(db):
    pk = _stored(db, "1", deadline="", location="Vienna, Austria")
    s = Settings()
    s.filters.excluded_countries = ["Austria"]
    assert run_cleanup(db, s, TODAY) == [(pk, "excluded place (Austria)")]


def test_dry_run_archives_nothing(db):
    pk = _stored(db, "1")
    assert run_cleanup(db, Settings(), TODAY, dry_run=True) == [(pk, "deadline passed (2020-01-01)")]
    assert db.get(pk).archived_reason is None


def test_cleanup_disabled(db):
    pk = _stored(db, "1")
    s = Settings()
    s.cleanup.enabled = False
    assert run_cleanup(db, s, TODAY) == []
    assert db.get(pk).archived_reason is None


def test_cleanup_reports_only_what_it_really_archived(db):
    """A status changed between reading and archiving (the app, a Notion read-back) wins."""
    pk = _stored(db, "1")

    class ChangedMeanwhile:
        def tracked(self):
            rows = db.tracked()
            db.set_status(pk, Status.APPLIED)
            return rows

        def archive(self, pk, reason):
            return db.archive(pk, reason)

    assert run_cleanup(ChangedMeanwhile(), Settings(), TODAY) == []
    assert db.get(pk).archived_reason is None
