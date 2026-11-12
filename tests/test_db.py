from datetime import datetime, timedelta

import pytest
from conftest import make_job

from jobscout.db import Database
from jobscout.errors import RunInProgress
from jobscout.models import (
    JobState,
    RunSummary,
    ScoreResult,
    Status,
    StepEvent,
)


def _scored(db, job_id, score=80, status=None, min_score=70):
    j = make_job(job_id=job_id)
    db.insert_scraped([j], {j.pk: f"k{job_id}"})
    db.save_score(ScoreResult(j.pk, score, "r", [], "", ""), min_score=min_score)
    if status:
        db.set_status(j.pk, status)
    return j.pk


def test_insert_is_idempotent(db):
    j = make_job(job_id="1")
    assert db.insert_scraped([j], {j.pk: "k1"}) == [j.pk]
    assert db.insert_scraped([j], {j.pk: "k1"}) == []
    assert db.get(j.pk).state == JobState.SCRAPED and db.get(j.pk).status == Status.TO_REVIEW


def test_insert_never_swallows_a_bad_row_and_needs_a_dedup_key(db):
    import sqlite3

    good, bad = make_job(job_id="1"), make_job(job_id="2", title=None)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_scraped([good, bad], {good.pk: "k1", bad.pk: "k2"})
    assert db.get(good.pk) is None  # all or nothing
    with pytest.raises(KeyError):
        db.insert_scraped([good], {})


def test_get_roundtrips_job_fields_and_missing_is_none(db):
    j = make_job(job_id="1", recruiter="Ada Example", employment_type="Internship")
    db.insert_scraped([j], {j.pk: "k1"})
    row = db.get(j.pk)
    assert (row.title, row.company, row.recruiter, row.employment_type) == (
        j.title, j.company, "Ada Example", "Internship",
    )
    assert row.dedup_key == "k1" and row.score is None and row.flags == []
    assert row.archived_reason is None and row.first_seen_at
    assert db.get("linkedin:nope") is None


def test_jobs_in_state_and_set_state_and_dedup_lookup(db):
    a, b = make_job(job_id="a"), make_job(job_id="b")
    db.insert_scraped([a, b], {a.pk: "same", b.pk: "same"})
    assert sorted(db.find_by_dedup_key("same")) == sorted([a.pk, b.pk])
    assert db.find_by_dedup_key("other") == []
    db.set_state(b.pk, JobState.DUPLICATE)
    assert [r.pk for r in db.jobs_in_state(JobState.SCRAPED)] == [a.pk]
    assert [r.pk for r in db.jobs_in_state(JobState.DUPLICATE)] == [b.pk]


def test_jobs_in_state_keeps_insertion_order_within_a_second(db):
    z, a = make_job(job_id="z"), make_job(job_id="a")
    db.insert_scraped([z, a], {z.pk: "kz", a.pk: "ka"})
    assert [r.pk for r in db.jobs_in_state(JobState.SCRAPED)] == [z.pk, a.pk]


def test_deliveries_needed_without_status_sync_sends_once(db):
    pk = _scored(db, "a")
    assert [r.pk for r in db.deliveries_needed("webhook", False, sync_status=False)] == [pk]
    db.mark_delivery(pk, "webhook", "delivered", pushed_status=Status.TO_REVIEW)
    db.set_status(pk, Status.INTERESTED)
    assert db.deliveries_needed("webhook", False, sync_status=False) == []
    assert [r.pk for r in db.deliveries_needed("webhook", False)] == [pk]
    db.mark_delivery(pk, "webhook", "pending")
    assert [r.pk for r in db.deliveries_needed("webhook", False, sync_status=False)] == [pk]


def test_deliveries_lists_one_output(db):
    db.mark_delivery("linkedin:2", "notion", "delivered", external_id="p2", pushed_status=Status.APPLIED)
    db.mark_delivery("linkedin:1", "notion", "failed", error="nope")
    db.mark_delivery("linkedin:1", "webhook", "delivered")
    rows = db.deliveries("notion")
    assert [(r["pk"], r["state"], r["external_id"], r["pushed_status"]) for r in rows] == [
        ("linkedin:1", "failed", None, None),
        ("linkedin:2", "delivered", "p2", "applied"),
    ]
    assert db.deliveries("csv") == []


def test_save_score_threshold(db):
    a, b = make_job(job_id="a"), make_job(job_id="b")
    db.insert_scraped([a, b], {a.pk: "ka", b.pk: "kb"})
    db.save_score(ScoreResult(a.pk, 70, "ok", [], "", ""), min_score=70)
    db.save_score(ScoreResult(b.pk, 69, "meh", ["Asks for SQL"], "2026-12-01", "2026-11-20"), min_score=70)
    assert db.get(a.pk).state == JobState.KEPT
    row = db.get(b.pk)
    assert row.state == JobState.BELOW_THRESHOLD and row.flags == ["Asks for SQL"]
    assert (row.score, row.reason, row.start_date, row.deadline) == (69, "meh", "2026-12-01", "2026-11-20")


def test_min_score_zero_keeps_everything(db):
    j = make_job(job_id="z")
    db.insert_scraped([j], {j.pk: "kz"})
    db.save_score(ScoreResult(j.pk, 0, "no", [], "", ""), min_score=0)
    assert db.get(j.pk).state == JobState.KEPT


def test_set_status_updates_timestamp(db):
    pk = _scored(db, "1")
    before = db.get(pk).status_updated_at
    db._conn.execute("UPDATE jobs SET status_updated_at = '2000-01-01T00:00:00' WHERE pk = ?", (pk,))
    db.set_status(pk, Status.INTERESTED)
    row = db.get(pk)
    assert row.status == Status.INTERESTED
    assert row.status_updated_at > "2000-01-01T00:00:00" and before


def test_archive_restore_keeps_status(db):
    pk = _scored(db, "1", status=Status.INTERESTED)
    db.archive(pk, "deadline passed (2026-10-01)")
    assert db.get(pk).archived_reason == "deadline passed (2026-10-01)"
    db.restore(pk)
    row = db.get(pk)
    assert row.status == Status.INTERESTED and row.archived_reason is None


def test_restore_marks_the_job_restored_until_its_status_changes(db):
    pk = _scored(db, "1")
    assert db.get(pk).restored is False
    db.archive(pk, "old")
    db.restore(pk)
    assert db.get(pk).restored is True
    db.set_status(pk, Status.TO_REVIEW)  # same status: still restored
    assert db.get(pk).restored is True
    db.set_status(pk, Status.INTERESTED)
    assert db.get(pk).restored is False


def test_orphaned_pages_are_archived_or_dropped_jobs_with_a_live_page(db):
    archived = _scored(db, "arch")
    db.archive(archived, "old")
    dropped = _scored(db, "low", score=10)
    acted_low = _scored(db, "acted", score=10, status=Status.INTERESTED)
    live = _scored(db, "live")
    gone = _scored(db, "gone")
    db.archive(gone, "old")
    applied = _scored(db, "applied", status=Status.APPLIED)
    db._conn.execute("UPDATE jobs SET archived_reason = 'old' WHERE pk = ?", (applied,))
    for pk in (archived, dropped, acted_low, live, applied):
        db.mark_delivery(pk, "notion", "delivered", external_id=f"page-{pk}", pushed_status=Status.TO_REVIEW)
    db.mark_delivery(gone, "notion", "pending")  # its page is already archived
    assert db.orphaned_pages("notion", include_below=False) == sorted([archived, dropped])
    assert db.orphaned_pages("notion", include_below=True) == [archived]


def test_tracked_only_kept_and_hides_archived_by_default(db):
    kept = _scored(db, "kept")
    _scored(db, "low", score=10)
    archived = _scored(db, "arch")
    db.archive(archived, "old")
    assert [r.pk for r in db.tracked()] == [kept]
    assert {r.pk for r in db.tracked(include_archived=True)} == {kept, archived}


def test_rescore_candidates_are_only_jobs_to_review(db):
    active = _scored(db, "active", status=Status.APPLIED)
    review = _scored(db, "review")
    below = _scored(db, "below", score=10)
    archived = _scored(db, "arch")
    db.archive(archived, "old")
    fresh = make_job(job_id="fresh")
    db.insert_scraped([fresh], {fresh.pk: "kf"})
    acted = [_scored(db, s.value, status=s) for s in (Status.INTERESTED, Status.NOT_INTERESTED, Status.REJECTED)]
    got = set(db.rescore_candidates())
    assert got == {review, below}
    assert active not in got and archived not in got and not got & set(acted)


def test_deliveries_needed_tracks_status_changes(db):
    pk = _scored(db, "1")
    assert [r.pk for r in db.deliveries_needed("notion", False)] == [pk]
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    assert db.deliveries_needed("notion", False) == []
    db.set_status(pk, Status.APPLIED)
    assert [r.pk for r in db.deliveries_needed("notion", False)] == [pk]
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.APPLIED)
    assert db.deliveries_needed("notion", False) == []
    assert db.deliveries_needed("sheets", False) != []


def test_deliveries_needed_failed_pending_and_below(db):
    kept = _scored(db, "kept")
    below = _scored(db, "below", score=10)
    assert [r.pk for r in db.deliveries_needed("notion", False)] == [kept]
    assert {r.pk for r in db.deliveries_needed("notion", True)} == {kept, below}
    db.mark_delivery(kept, "notion", "failed", error="boom", pushed_status=Status.TO_REVIEW)
    assert [r.pk for r in db.deliveries_needed("notion", False)] == [kept]
    db.mark_delivery(kept, "notion", "pending")
    assert [r.pk for r in db.deliveries_needed("notion", False)] == [kept]
    db.archive(kept, "old")
    assert db.deliveries_needed("notion", False) == []


def test_delivery_roundtrip(db):
    pk = _scored(db, "1")
    assert db.get_delivery(pk, "notion") is None
    db.mark_delivery(pk, "notion", "failed", error="boom")
    d = db.get_delivery(pk, "notion")
    assert d["state"] == "failed" and d["error"] == "boom" and d["external_id"] is None
    db.mark_delivery(pk, "notion", "delivered", external_id="p1", pushed_status=Status.INTERESTED)
    d = db.get_delivery(pk, "notion")
    assert (d["state"], d["external_id"], d["error"], d["pushed_status"]) == (
        "delivered", "p1", None, "interested",
    )


def test_windows_roundtrip(db):
    assert db.window_start("Norway") is None
    t = datetime(2026, 10, 30, 8, 15, 0)
    db.commit_window("Norway", t)
    assert db.window_start("Norway") == t
    db.commit_window("Norway", t + timedelta(days=1))
    assert db.window_start("Norway") == t + timedelta(days=1)
    assert db.window_start("Italy") is None


def test_run_lock_and_stale_lock(db):
    now = datetime(2026, 11, 1, 12, 0)
    first = db.start_run("cli", now)
    with pytest.raises(RunInProgress):
        db.start_run("ui", now + timedelta(minutes=5))
    rid = db.start_run("ui", now + timedelta(minutes=21))  # no sign of life for 20 minutes: stale
    assert rid > first
    stale = [r for r in db.runs() if r["id"] == first][0]
    assert stale["status"] == "error" and "Interrupted" in stale["error"]


def test_events_keep_a_long_run_alive(db):
    rid = db.start_run("cli", datetime(2026, 11, 1, 12, 0))
    db.add_event(rid, StepEvent("search", "running", "Searching"))  # heartbeat at the real clock
    with pytest.raises(RunInProgress):
        db.start_run("ui", datetime.now() + timedelta(minutes=19))
    assert db.start_run("ui", datetime.now() + timedelta(minutes=21)) > rid


def test_heartbeat_keeps_a_run_alive(db):
    rid = db.start_run("cli", datetime(2026, 11, 1, 12, 0))
    db.heartbeat(rid)
    with pytest.raises(RunInProgress):
        db.start_run("ui", datetime.now() + timedelta(minutes=19))


def test_setters_refuse_an_unknown_job(db):
    result = ScoreResult("linkedin:nope", 80, "r", [], "", "")
    for call in (
        lambda: db.set_state("linkedin:nope", JobState.KEPT),
        lambda: db.save_score(result, min_score=70),
        lambda: db.set_status("linkedin:nope", Status.APPLIED),
        lambda: db.archive("linkedin:nope", "old"),
        lambda: db.restore("linkedin:nope"),
    ):
        with pytest.raises(KeyError):
            call()


def test_archive_never_touches_acted_on_or_archived_jobs(db):
    applied = _scored(db, "a", status=Status.APPLIED)
    review = _scored(db, "r")
    assert db.archive(applied, "old") is False and db.get(applied).archived_reason is None
    assert db.archive(review, "first") is True
    assert db.archive(review, "second") is False and db.get(review).archived_reason == "first"


def test_finish_run_releases_lock_and_lists_runs(db):
    now = datetime(2026, 11, 1, 12, 0)
    rid = db.start_run("cli", now)
    db.finish_run(rid, RunSummary(rid, "done", {"kept": 3}, 0.12))
    rid2 = db.start_run("cli", now + timedelta(minutes=1))
    runs = db.runs()
    assert [r["id"] for r in runs] == [rid2, rid]
    done = runs[1]
    assert done["status"] == "done" and done["counts"] == {"kept": 3}
    assert done["cost_usd"] == pytest.approx(0.12) and done["trigger"] == "cli"
    assert done["finished_at"] and done["error"] is None
    assert len(db.runs(limit=1)) == 1


def test_run_events_roundtrip_in_order(db):
    rid = db.start_run("cli", datetime(2026, 11, 1, 12, 0))
    e1 = StepEvent("search", "running", "Searching", done=1, total=4, elapsed_s=1.5, data={"n": 2})
    e2 = StepEvent("search", "done", "Searched")
    db.add_event(rid, e1)
    db.add_event(rid, e2)
    assert db.events(rid) == [e1, e2]
    assert db.events(rid + 99) == []


def test_database_persists_and_sets_pragmas(jobscout_home):
    path = jobscout_home / "x.db"
    d = Database(path)
    j = make_job(job_id="1")
    d.insert_scraped([j], {j.pk: "k"})
    assert d._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert d._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    d.close()
    d2 = Database(path)
    assert d2.get(j.pk) is not None
    d2.close()


def test_older_database_gains_new_columns(jobscout_home):
    import sqlite3

    path = jobscout_home / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (pk TEXT PRIMARY KEY, source TEXT NOT NULL, job_id TEXT NOT NULL,"
                " title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL, url TEXT NOT NULL,"
                " description TEXT NOT NULL, posted TEXT NOT NULL, keyword TEXT NOT NULL, area TEXT NOT NULL,"
                " recruiter TEXT NOT NULL DEFAULT '', recruiter_title TEXT NOT NULL DEFAULT '',"
                " recruiter_url TEXT NOT NULL DEFAULT '', employment_type TEXT NOT NULL DEFAULT '',"
                " state TEXT NOT NULL, status TEXT NOT NULL, score INTEGER, reason TEXT NOT NULL DEFAULT '',"
                " flags TEXT NOT NULL DEFAULT '[]', start_date TEXT NOT NULL DEFAULT '',"
                " deadline TEXT NOT NULL DEFAULT '', archived_reason TEXT, dedup_key TEXT NOT NULL DEFAULT '',"
                " first_seen_at TEXT NOT NULL, status_updated_at TEXT NOT NULL)")  # fmt: skip
    old.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT NOT NULL,"
                " started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,"
                " counts_json TEXT NOT NULL DEFAULT '{}', cost_usd REAL NOT NULL DEFAULT 0, error TEXT)")  # fmt: skip
    old.commit()
    old.close()
    d = Database(path)
    j = make_job(job_id="1", salary="EUR 30,000–36,000 a year")
    d.insert_scraped([j], {j.pk: "k"})
    row = d.get(j.pk)
    assert row.restored is False and row.salary == "EUR 30,000–36,000 a year"
    d.close()


def test_usable_from_another_thread(db):
    import threading

    j = make_job(job_id="t")
    errors = []

    def work():
        try:
            db.insert_scraped([j], {j.pk: "kt"})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert errors == [] and db.get(j.pk) is not None
