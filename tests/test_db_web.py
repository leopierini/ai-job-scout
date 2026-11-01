"""Database reads, UI settings and pre-started runs that the web app relies on."""

from datetime import datetime, timedelta

import pytest
from conftest import make_row
from test_pipeline import sample_pipeline

from jobscout.errors import RunInProgress
from jobscout.models import JobState, RunSummary, Status, StepEvent
from jobscout.settings import Settings, load_settings, save_settings

NOW = datetime(2026, 11, 1, 12, 0, 0)


def store(db, **kw):
    """Insert a fictional job and force its state, status and archive flag directly."""
    row = make_row(**kw)
    db.insert_scraped([row], {row.pk: row.dedup_key})
    with db._tx() as c:
        c.execute(
            "UPDATE jobs SET state = ?, status = ?, score = ?, archived_reason = ?, first_seen_at = ? WHERE pk = ?",
            (row.state.value, row.status.value, row.score, row.archived_reason, row.first_seen_at, row.pk),
        )
    return row.pk


def test_active_run_ignores_stale_and_finished(db):
    assert db.active_run(NOW) is None
    fresh = db.start_run("app", NOW - timedelta(minutes=5))
    assert db.active_run(NOW) == fresh
    assert db.active_run(NOW + timedelta(minutes=30)) is None  # silent for 35 minutes: crashed
    db.finish_run(fresh, RunSummary(fresh, "done", {}, 0.0, None))
    assert db.active_run(NOW) is None


def set_heartbeat(db, run_id, at):
    with db._tx() as c:
        c.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (at.isoformat(timespec="seconds"), run_id))


def test_active_run_counts_a_recent_heartbeat_and_the_20_minute_boundary(db):
    rid = db.start_run("app", NOW - timedelta(hours=3))  # started long ago...
    assert db.active_run(NOW) is None
    set_heartbeat(db, rid, NOW - timedelta(minutes=2))  # ...but alive
    assert db.active_run(NOW) == rid
    set_heartbeat(db, rid, NOW - timedelta(minutes=19))
    assert db.active_run(NOW) == rid
    set_heartbeat(db, rid, NOW - timedelta(minutes=21))
    assert db.active_run(NOW) is None


def test_get_run_matches_the_runs_shape(db):
    rid = db.start_run("app", NOW)
    assert db.get_run(rid) == db.runs()[0]
    assert db.get_run(rid + 1) is None


def test_events_after_returns_only_newer_events(db):
    rid = db.start_run("app", NOW)
    for step in ("cleanup", "search", "filter"):
        db.add_event(rid, StepEvent(step, "running", step))
    assert [s for s, _ in db.events_after(rid, 1)] == [2, 3]
    assert [ev.step for _, ev in db.events_after(rid, 0)] == ["cleanup", "search", "filter"]
    assert db.events_after(rid, 3) == []


def test_view_counts_and_views_partition_jobs(db):
    pks = {s: store(db, job_id=s.value, status=s) for s in Status}
    archived = store(db, job_id="arch", status=Status.INTERESTED, archived_reason="Deadline passed")
    below = store(db, job_id="low", state=JobState.BELOW_THRESHOLD, score=40)
    store(db, job_id="gone", state=JobState.DUPLICATE)  # in no view
    dup_archived = store(db, job_id="dup-arch", state=JobState.BELOW_THRESHOLD, archived_reason="Old")  # any state

    counts = db.view_counts()
    assert set(counts) == {s.value for s in Status} | {"archived", "below"}
    assert counts["archived"] == 2 and counts["below"] == 1
    assert all(counts[s.value] == 1 for s in Status)
    for s in Status:
        assert [r.pk for r in db.jobs_view(s.value)] == [pks[s]]
    assert [r.pk for r in db.jobs_view("archived")] == [archived, dup_archived]
    assert [r.pk for r in db.jobs_view("below")] == [below]


def test_jobs_view_orders_by_score_then_newest_then_pk(db):
    a = store(db, job_id="a", score=70, first_seen_at="2026-10-01T10:00:00")
    b = store(db, job_id="b", score=90, first_seen_at="2026-10-01T10:00:00")
    c = store(db, job_id="c", score=70, first_seen_at="2026-10-02T10:00:00")
    assert [r.pk for r in db.jobs_view("to_review")] == [b, c, a]


def test_jobs_view_rejects_unknown_view(db):
    with pytest.raises(ValueError):
        db.jobs_view("everything")


def test_keep_promotes_below_and_survives_threshold(db):
    pk = store(db, state=JobState.BELOW_THRESHOLD, score=40)
    db.keep(pk)
    db.apply_threshold(70)
    row = db.get(pk)
    assert row.state == JobState.KEPT and row.status == Status.INTERESTED
    with pytest.raises(KeyError):
        db.keep("missing")


def test_reset_refuses_during_active_run(db):
    store(db)
    db.start_run("app", datetime.now())
    with pytest.raises(RunInProgress):
        db.reset()
    assert db.get(make_row().pk) is not None


def test_reset_ignores_a_crashed_run(db):
    store(db)
    db.start_run("app", datetime.now() - timedelta(minutes=30))  # running, silent for 30 minutes
    db.reset()
    assert db.runs() == [] and db.view_counts()["to_review"] == 0


def test_reset_empties_everything(db):
    pk = store(db)
    db.mark_delivery(pk, "csv", "delivered")
    db.commit_window("Norway", NOW)
    rid = db.start_run("app", NOW)
    db.add_event(rid, StepEvent("cleanup", "done", "ok"))
    db.finish_run(rid, RunSummary(rid, "done", {}, 0.0, None))
    db.reset()
    assert db.runs() == [] and db.events(rid) == []
    assert db.get(pk) is None and db.deliveries("csv") == []
    assert db.window_start("Norway") is None and db.view_counts()["to_review"] == 0


def test_ui_settings_defaults_and_roundtrip():
    s = Settings()
    assert s.ui.setup_complete is False and s.ui.default_sort == "score"
    assert s.ui.columns == ["score", "role", "why", "starts", "posted"]
    s.ui.columns = ["role", "score", "deadline"]
    s.ui.headline = "Data intern, Nordics"
    s.ui.setup_complete = True
    save_settings(s)
    loaded = load_settings()
    assert loaded.ui.columns == ["role", "score", "deadline"]
    assert loaded.ui.headline == "Data intern, Nordics" and loaded.ui.setup_complete


def test_run_with_prestarted_run_id(db):
    rid = db.start_run("app", datetime.now())
    summary = sample_pipeline(db).run("app", run_id=rid)
    assert summary.run_id == rid and len(db.runs()) == 1
    assert db.get_run(rid)["status"] == "done"


def test_rescore_with_prestarted_run_id(db):
    rid = db.start_run("app", datetime.now())
    summary = sample_pipeline(db).rescore("app", run_id=rid)
    assert summary.run_id == rid and len(db.runs()) == 1


def test_interrupt_runs_marks_only_running_runs_with_the_trigger_prefix(db):
    app_run = db.start_run("app", NOW)
    db.finish_run(app_run, RunSummary(app_run, "done", {}, 0.0, None))
    rescore = db.start_run("app:rescore", NOW + timedelta(minutes=30))
    assert db.interrupt_runs("app") == 1
    assert db.get_run(rescore)["status"] == "error" and db.get_run(rescore)["error"] == "Interrupted"
    assert db.get_run(rescore)["finished_at"] is not None
    assert db.get_run(app_run)["status"] == "done"  # an ended run is left alone
    cli = db.start_run("cli", NOW + timedelta(hours=1))
    assert db.interrupt_runs("app") == 0
    assert db.get_run(cli)["status"] == "running"  # a command-line run isn't the app's to end


def test_interrupt_run_ends_one_running_run(db):
    rid = db.start_run("app", NOW)
    assert db.interrupt_run(rid) is True
    assert db.get_run(rid)["status"] == "error" and db.get_run(rid)["error"] == "Interrupted"
    assert db.interrupt_run(rid) is False  # already ended
    assert db.active_run(NOW) is None


def test_clear_deliveries_removes_one_output_only(db):
    a, b = store(db, job_id="1"), store(db, job_id="2")
    db.mark_delivery(a, "notion", "delivered", external_id="page-a")
    db.mark_delivery(b, "notion", "failed", error="Notion was down.")
    db.mark_delivery(a, "webhook", "delivered")
    assert db.clear_deliveries("notion") == 2
    assert db.deliveries("notion") == [] and len(db.deliveries("webhook")) == 1
