"""Engine hardening: a failed Notion read-back blocks pushes, the threshold applies after the read-back,
long Notion loops show a sign of life, and `cleanup --dry-run` writes nothing."""

import json

import httpx
from conftest import make_job
from test_cli import _notion_on
from test_pipeline import (
    DB_ID,
    NOTION_API,
    _notion_page,
    _notion_settings,
    final_events,
    make_pipeline,
    no_sleep,
    scored_row,
)

from jobscout import paths
from jobscout._output_sync import archive_orphaned_pages, mark_rescored, read_back_notion_statuses
from jobscout.cli import main
from jobscout.db import Database
from jobscout.models import JobState, ScoreResult, Status
from jobscout.outputs.notion import NotionClient
from jobscout.scoring.providers import FakeProvider

QUERY = f"{NOTION_API}/databases/{DB_ID}/query"


class RecordingNotion:
    """A Notion client that only records which pages were archived."""

    def __init__(self):
        self.archived = []

    def archive_page(self, page_id):
        self.archived.append(page_id)


def _notion_client():
    return NotionClient("tok", httpx.Client(), sleep=no_sleep)


def _patches(respx_mock):
    return [c for c in respx_mock.calls if c.request.method == "PATCH"]


def _kept_below_threshold(db, page_id):
    """A kept job scoring 65 (the minimum is 70) that Notion shows as Applied; locally still To review."""
    pk = scored_row(db, make_job(job_id="1"), score=65)
    db.set_state(pk, JobState.KEPT)
    db.mark_delivery(pk, "notion", "delivered", external_id=page_id, pushed_status=Status.TO_REVIEW)
    return pk


# --- a failed read-back blocks Notion for the whole run ---


def test_failed_notion_read_back_blocks_pushes_for_the_run(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"))
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.set_status(pk, Status.INTERESTED)  # deliveries_needed would push this
    respx_mock.post(QUERY).respond(500)
    events = []
    s = make_pipeline(
        db, [], settings=_notion_settings(), notion=_notion_client(), on_event=events.append
    ).run()  # fmt: skip

    assert not _patches(respx_mock)  # nothing pushed to Notion
    assert "couldn't read statuses from Notion" in final_events(events)["cleanup"].message
    assert s.status == "done" and s.counts["delivered"] == 0
    assert db.get_delivery(pk, "notion")["pushed_status"] == "to_review"  # still owed, pushed after a good read-back


# --- heartbeat ---


def _archived_jobs_with_pages(db):
    pks = []
    for n in (1, 2):
        pk = scored_row(db, make_job(job_id=str(n)))
        db.mark_delivery(pk, "notion", "delivered", external_id=f"page-{n}", pushed_status=Status.TO_REVIEW)
        db.archive(pk, "deadline passed (2026-01-15)")
        pks.append(pk)
    return pks


def test_heartbeat_called_per_archived_page(db):
    _archived_jobs_with_pages(db)
    notion, beats = RecordingNotion(), []
    failed = archive_orphaned_pages(db, notion, include_below=False, beat=lambda: beats.append(1))
    assert failed == 0 and len(beats) == 2
    assert notion.archived == ["page-1", "page-2"]


def test_mark_rescored_beats_per_archived_page(db):
    pks = _archived_jobs_with_pages(db)
    results = [ScoreResult(pk, 10, "Weak match.", [], "", "") for pk in pks]
    notion, beats = RecordingNotion(), []
    failed = mark_rescored(db, notion, results, 70, False, beat=lambda: beats.append(1))
    assert failed == 0 and len(beats) == 2 and len(notion.archived) == 2


def test_pipeline_cleanup_beats_the_run_heartbeat_per_archived_page(db, respx_mock):
    _archived_jobs_with_pages(db)
    respx_mock.post(QUERY).respond(200, json={"results": [], "has_more": False})
    respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    respx_mock.patch(f"{NOTION_API}/pages/page-2").respond(200, json={"id": "page-2"})
    beats = []
    original = db.heartbeat
    db.heartbeat = lambda run_id: (beats.append(run_id), original(run_id))[1]
    make_pipeline(db, [], settings=_notion_settings(), notion=_notion_client()).run()
    assert len(beats) == 2


# --- the threshold applies after the read-back ---


def test_threshold_applies_after_notion_read_back(db, respx_mock):
    pk = _kept_below_threshold(db, "page-1")
    respx_mock.post(QUERY).respond(200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False})
    make_pipeline(db, [], settings=_notion_settings(), notion=_notion_client()).run()

    row = db.get(pk)
    assert row.status == Status.APPLIED and row.state == JobState.KEPT
    assert not _patches(respx_mock)  # its page was not archived


def test_threshold_still_applies_when_the_read_back_fails(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"), score=65)
    db.set_state(pk, JobState.KEPT)  # a lowered-then-raised minimum left it kept under the threshold
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    respx_mock.post(QUERY).respond(500)
    make_pipeline(db, [], settings=_notion_settings(), notion=_notion_client()).run()
    assert db.get(pk).state == JobState.BELOW_THRESHOLD
    assert not _patches(respx_mock)  # and nothing was archived or pushed in Notion


def test_rescore_applies_threshold_after_notion_read_back(db, respx_mock):
    pk = _kept_below_threshold(db, "page-1")
    respx_mock.post(QUERY).respond(200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False})
    s = make_pipeline(
        db, [], FakeProvider({pk: 90}), settings=_notion_settings(), notion=_notion_client()
    ).rescore()  # fmt: skip

    row = db.get(pk)
    assert s.status == "done" and s.counts["scored"] == 0
    assert row.status == Status.APPLIED and row.state == JobState.KEPT
    assert not _patches(respx_mock)


# --- cleanup --dry-run writes nothing ---


def _job_past_deadline(db, job_id, title):
    job = make_job(job_id=job_id, title=title)
    db.insert_scraped([job], {job.pk: f"key-{job_id}"})
    db.save_score(ScoreResult(job.pk, 80, "Good match.", [], "", "2026-01-15"), min_score=70)
    db.mark_delivery(job.pk, "notion", "delivered", external_id=f"page-{job_id}", pushed_status=Status.TO_REVIEW)
    return job.pk


def test_cleanup_dry_run_writes_nothing(capsys, respx_mock):
    db = Database(paths.db_path())
    first = _job_past_deadline(db, "1", "Applied Elsewhere Role")
    second = _job_past_deadline(db, "2", "Stale Role")
    db.close()
    _notion_on()
    respx_mock.post("https://api.notion.com/v1/databases/db-1/query").respond(
        200, json={"results": [_notion_page("page-1", "Applied"), _notion_page("page-2", "To review")],
                   "has_more": False},
    )  # fmt: skip

    assert main(["cleanup", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would archive" in out and "Stale Role" in out and "Applied Elsewhere Role" not in out

    db = Database(paths.db_path())
    assert db.get(first).status == Status.TO_REVIEW  # the read-back was not applied locally
    assert db.get_delivery(first, "notion")["pushed_status"] == "to_review"
    assert all(db.get(pk).archived_reason is None for pk in (first, second))
    db.close()
    assert not _patches(respx_mock)


# --- a job demoted while Notion was unreadable is repaired by the next read-back ---


def test_applied_job_demoted_by_an_unreadable_notion_is_kept_again_next_run(db, respx_mock):
    pk = _kept_below_threshold(db, "page-1")
    respx_mock.post(QUERY).respond(500)
    make_pipeline(db, [], settings=_notion_settings(), notion=_notion_client()).run()
    assert db.get(pk).state == JobState.BELOW_THRESHOLD  # run 1: Notion unreadable, demoted

    respx_mock.reset()
    respx_mock.post(QUERY).respond(200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False})
    make_pipeline(db, [], settings=_notion_settings(), notion=_notion_client()).run()  # run 2
    row = db.get(pk)
    assert row.status == Status.APPLIED and row.state == JobState.KEPT
    assert pk in [r.pk for r in db.tracked()]
    assert not _patches(respx_mock)


def test_read_back_restores_the_kept_state_of_a_below_threshold_job(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"), score=65)  # below the minimum: BELOW_THRESHOLD
    other = scored_row(db, make_job(job_id="2", title="Other role"), score=65)
    for n, p in ((1, pk), (2, other)):
        db.mark_delivery(p, "notion", "delivered", external_id=f"page-{n}", pushed_status=Status.TO_REVIEW)
    assert db.get(pk).state == JobState.BELOW_THRESHOLD
    respx_mock.post(QUERY).respond(
        200, json={"results": [_notion_page("page-1", "Interested"), _notion_page("page-2", "To review")],
                   "has_more": False},
    )  # fmt: skip
    assert read_back_notion_statuses(db, _notion_client(), DB_ID) == 1
    assert db.get(pk).status == Status.INTERESTED and db.get(pk).state == JobState.KEPT
    assert db.get(other).state == JobState.BELOW_THRESHOLD  # unchanged status: left alone


def test_no_server_message_points_into_settings():
    """The wizard shows server messages too, and Settings can't be reached from it."""
    from pathlib import Path

    import jobscout

    root = Path(jobscout.__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "Settings →" in line and "Apify → Settings" not in line  # Apify's own settings page is fine
    ]
    assert offenders == []
