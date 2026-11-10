import inspect
import json
import re
import threading
import time
from datetime import date, datetime, timedelta

import httpx
import pytest
from conftest import make_job

from jobscout.errors import ApifyError, ConfigError, ProviderError
from jobscout.models import JobState, ScoreResult, Status
from jobscout.outputs.notion import NotionClient
from jobscout.outputs.webhook import WebhookOutput
from jobscout.pipeline import COUNT_KEYS, Pipeline, build_pipeline
from jobscout.scoring.providers import FakeProvider
from jobscout.settings import Secrets, Settings
from jobscout.sources import AreaResult, SearchArea
from jobscout.sources.apify import AUTH_MESSAGE, LIMIT_MESSAGE
from jobscout.sources.sample import _DATA as SAMPLE_DATA
from jobscout.sources.sample import SAMPLE_PROFILE, SampleSource, sample_answers, sample_settings

STEPS = ["cleanup", "search", "filter", "score", "save"]
NOTION_API = "https://api.notion.com/v1"
HOOK_URL = "https://hooks.example.com/jobs"
DB_ID = "db-1"
T1 = datetime(2026, 11, 1, 9, 0, 0)
SAMPLE_NEW = 14  # 16 sample postings minus one duplicate and one in an excluded place


def no_sleep(_seconds):
    pass


class FakeSource:
    """One search per area; each area returns a preset AreaResult or raises a preset exception."""

    def __init__(self, name, results):
        self.name = name
        self._results = results
        self.calls = []

    def areas(self):
        return [SearchArea(key=k, label=k.split(":", 1)[1], source=self.name, searches=1) for k in self._results]

    def search_area(self, area, window_seconds, progress):
        self.calls.append((area.key, window_seconds))
        progress(0, 1, 0)
        result = self._results[area.key]
        if isinstance(result, BaseException):
            raise result
        progress(1, 1, len(result.jobs))
        return result


class FailingProvider:
    def complete(self, prompt):
        raise ProviderError("The AI provider is down.")


def ok(*jobs, billed=None):
    return AreaResult(jobs=list(jobs), billed=len(jobs) if billed is None else billed, complete=True)


def make_pipeline(db, sources, provider=None, settings=None, **kw):
    return Pipeline(
        settings or Settings(), Secrets(), db, "A fictional profile.", sources,
        provider or FakeProvider(), **kw,
    )  # fmt: skip


def sample_pipeline(db, provider=None, settings=None, **kw):
    cancel = kw.pop("cancel", None) or threading.Event()
    settings = settings or sample_settings()
    return Pipeline(
        settings, Secrets(), db, SAMPLE_PROFILE, [SampleSource(cancel)], provider or FakeProvider(),
        cancel=cancel, **kw,
    )  # fmt: skip


def final_events(events):
    return {e.step: e for e in events if e.status != "running"}


def scored_row(db, job, score=80, status=None, deadline=""):
    db.insert_scraped([job], {job.pk: f"key-{job.pk}"})
    db.save_score(ScoreResult(job.pk, score, "Good match.", [], "", deadline), min_score=70)
    if status:
        db.set_status(job.pk, status)
    return job.pk


# --- sample mode ---


def test_paced_sample_run_pauses_between_steps(db, monkeypatch):
    monkeypatch.setattr("jobscout.sources.sample.PACE_SECONDS", 0.1)
    t0 = time.monotonic()
    summary = build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True, paced=True).run()
    elapsed = time.monotonic() - t0
    assert summary.status == "done" and summary.counts["scored"] == SAMPLE_NEW
    # 4 searches and 2 scoring batches wait 0.1 s each, 0.6 s in all. Losing the scoring pauses would give 0.4 s and
    # losing the search pauses 0.2 s, so 0.5 s catches either and leaves 0.1 s of slack for timers that return early.
    assert elapsed >= 0.5


def test_only_the_app_paces_the_sample(db, monkeypatch):
    pace = 0.5  # long enough that any pause shows
    monkeypatch.setattr("jobscout.sources.sample.PACE_SECONDS", pace)
    t0 = time.monotonic()
    build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True).run()  # what the command line runs
    # Six paced waits would take 6 x pace; five paces is below that and far above what an unpaced run needs.
    assert time.monotonic() - t0 < 5 * pace


def test_the_web_app_builds_paced_sample_runs():
    from jobscout.pipeline import build_app_pipeline
    from jobscout.server.app import create_app
    from jobscout.server.runner import RunManager

    for target in (create_app, RunManager.__init__):
        assert inspect.signature(target).parameters["pipeline_factory"].default is build_app_pipeline


def test_cancel_cuts_a_paced_sample_search_short():
    cancel = threading.Event()
    source = SampleSource(cancel, pace=30.0)
    area = source.areas()[0]
    threading.Timer(0.05, cancel.set).start()
    t0 = time.monotonic()
    result = source.search_area(area, 86400, lambda *_: None)
    assert time.monotonic() - t0 < 5 and not result.complete


def _jobs_prompt():
    return 'Jobs:\n<jobs>\n[{"id": "a", "title": "Engineer", "description": ""}]\n</jobs>'


def test_cancel_cuts_a_paced_scoring_pause_short():
    cancel = threading.Event()
    provider = FakeProvider(delay=30.0, cancel=cancel)
    threading.Timer(0.05, cancel.set).start()
    t0 = time.monotonic()
    out = provider.complete(_jobs_prompt())
    assert time.monotonic() - t0 < 5
    assert '"id": "a"' in out  # the batch still answers; the pipeline stops at its next cancel check


def test_the_sample_provider_gets_the_runs_cancel_event(db, monkeypatch):
    seen = {}

    class Recording(FakeProvider):
        def __init__(self, *args, **kw):
            seen.update(kw)
            super().__init__(*args, **kw)

    monkeypatch.setattr("jobscout.pipeline.FakeProvider", Recording)
    cancel = threading.Event()
    build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True, cancel=cancel)
    assert seen["cancel"] is cancel


def test_sample_run_end_to_end(db):
    events = []
    p = build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True, on_event=events.append)
    s = p.run()
    assert s.status == "done" and s.error is None
    assert [e.step for e in events if e.status == "done"] == ["cleanup", "search", "filter", "score", "save"]
    assert s.counts["duplicates"] == 1 and s.counts["excluded"] == 1
    assert s.counts["kept"] + s.counts["below"] == s.counts["scored"]
    assert set(s.counts) == set(COUNT_KEYS)
    assert s.counts["searched"] == 16 and s.counts["new"] == SAMPLE_NEW and s.counts["scored"] == SAMPLE_NEW
    assert s.cost_usd == 0
    # Every step starts with a running event, and every event is also stored.
    for step in STEPS:
        mine = [e for e in events if e.step == step]
        assert mine[0].status == "running" and mine[-1].status == "done"
    assert db.events(s.run_id) == events
    run = db.runs()[0]
    assert run["status"] == "done" and run["counts"] == s.counts and run["finished_at"]
    fin = final_events(events)
    assert fin["filter"].message == f"{SAMPLE_NEW} new · 1 duplicate · 1 excluded place"
    assert fin["score"].message == f"{SAMPLE_NEW} of {SAMPLE_NEW} scored"
    assert fin["save"].message == f"{s.counts['kept']} kept · 0 sent · 0 failed"
    assert fin["cleanup"].message == "0 archived"
    progress = [e.message for e in events if e.step == "search" and e.status == "running"]
    assert "Europe: 2 of 2 searches · 9 postings so far" in progress
    assert "UK: 2 of 2 searches · 16 postings so far" in progress


def _eu_08(source, today=None):
    area = next(a for a in source.areas() if a.label == "Europe")
    jobs = source.search_area(area, 86400, lambda *_: None).jobs
    return next(j for j in jobs if j.job_id == "eu-08")


def test_sample_deadline_is_counted_from_today():
    job = _eu_08(SampleSource(threading.Event(), today=date(2030, 3, 1)))
    assert job.description.endswith("\nDeadline: 2030-03-22")
    assert _eu_08(SampleSource(threading.Event())).description.endswith(
        f"\nDeadline: {(date.today() + timedelta(days=21)).isoformat()}"
    )


def test_sample_run_shows_the_deadline_about_three_weeks_out(db):
    build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True).run()
    row = db.get("sample:eu-08")
    assert row.deadline == (date.today() + timedelta(days=21)).isoformat()
    assert row.archived_reason is None


def test_sample_jobs_get_realistic_scores_and_reasons(db):
    build_pipeline(sample_settings(), Secrets(), db, SAMPLE_PROFILE, sample=True).run()
    scored = [r for r in db.jobs_in_state(JobState.KEPT) + db.jobs_in_state(JobState.BELOW_THRESHOLD)]
    assert len(scored) == SAMPLE_NEW
    assert all(r.reason and r.reason != "Sample score." for r in scored)
    assert any(r.flags for r in scored) and all(len(r.flags) <= 2 for r in scored)
    assert db.jobs_in_state(JobState.KEPT) and db.jobs_in_state(JobState.BELOW_THRESHOLD)
    first = db.get("sample:eu-01")
    assert first.start_date == (date.today() + timedelta(days=60)).isoformat() and first.score == 91


def test_every_scored_sample_posting_has_its_answer():
    postings = {p["id"]: p for p in json.loads(SAMPLE_DATA.joinpath("sample_jobs.json").read_text(encoding="utf-8"))}
    answers = sample_answers()
    unscored = {"eu-05", "uk-03"}  # in an excluded place, and a duplicate
    assert set(answers) == {f"sample:{i}" for i in postings if i not in unscored}
    for answer in answers.values():
        assert set(answer) == {"score", "reason", "flags", "start_date"}
        assert 0 <= answer["score"] <= 100 and answer["reason"].endswith(".")
        assert len(answer["flags"]) <= 2 and all(len(f) <= 30 for f in answer["flags"])
        assert answer["start_date"] == "" or datetime.strptime(answer["start_date"], "%Y-%m-%d")


def test_sample_duplicate_keeps_the_first_seen(db):
    sample_pipeline(db).run()
    assert db.get("sample:eu-02").state != JobState.DUPLICATE
    assert db.get("sample:uk-03").state == JobState.DUPLICATE
    assert db.get("sample:eu-05").state == JobState.EXCLUDED


def test_second_run_finds_nothing_new(db):
    first = sample_pipeline(db, now=lambda: T1).run()
    assert db.window_start("sample:Europe") == T1 and db.window_start("sample:UK") == T1
    second = sample_pipeline(db, now=lambda: T1 + timedelta(hours=2)).run()
    assert second.status == "done"
    assert second.counts["new"] == 0 and second.counts["scored"] == 0
    assert second.counts["searched"] == first.counts["searched"]  # same postings, all already known
    assert db.window_start("sample:Europe") == T1 + timedelta(hours=2)


def test_window_uses_time_since_last_complete_search(db):
    src = FakeSource("linkedin", {"linkedin:EEA": ok(make_job(job_id="1"))})
    make_pipeline(db, [src], now=lambda: T1).run()
    make_pipeline(db, [src], now=lambda: T1 + timedelta(hours=2)).run()
    make_pipeline(db, [src], now=lambda: T1 + timedelta(hours=3)).run(override_days=7)
    assert [seconds for _, seconds in src.calls] == [15 * 86400, 3 * 3600, 7 * 86400]


def test_short_override_never_commits_a_gap(db):
    t0 = datetime(2026, 10, 1, 9)
    db.commit_window("linkedin:A", t0)
    src = FakeSource("linkedin", {"linkedin:A": ok()})
    later = t0 + timedelta(days=10)
    make_pipeline(db, [src], now=lambda: later).run(override_days=1)
    assert db.window_start("linkedin:A") == t0  # days 1..9 still to search
    make_pipeline(db, [src], now=lambda: later).run(override_days=11)
    assert db.window_start("linkedin:A") == later  # covers the automatic window: committed


def test_short_override_on_a_new_area_does_not_commit(db):
    src = FakeSource("linkedin", {"linkedin:A": ok()})
    make_pipeline(db, [src], now=lambda: T1).run(override_days=1)
    assert db.window_start("linkedin:A") is None


# --- search errors ---


def test_apify_limit_mid_run_keeps_finished_areas(db):
    events = []
    a1, a2 = make_job(job_id="1", title="Product Analyst"), make_job(job_id="2", title="Product Owner")
    a3 = make_job(job_id="3", title="Associate Product Manager")
    limit = ApifyError(LIMIT_MESSAGE, kind="limit")
    linkedin = FakeSource("linkedin", {
        "linkedin:EEA": ok(a1, a2),
        "linkedin:UK": AreaResult(jobs=[a3], billed=1, complete=False, error=limit),
        "linkedin:CH": ok(make_job(job_id="9", title="Never searched")),
    })  # fmt: skip
    indeed = FakeSource("indeed", {"indeed:de": ok(make_job(source="indeed", job_id="x"))})
    s = make_pipeline(db, [linkedin, indeed], now=lambda: T1, on_event=events.append).run()

    assert db.window_start("linkedin:EEA") == T1
    assert db.window_start("linkedin:UK") is None
    assert [k for k, _ in linkedin.calls] == ["linkedin:EEA", "linkedin:UK"] and indeed.calls == []
    assert {db.get(j.pk).state for j in (a1, a2, a3)} <= {JobState.KEPT, JobState.BELOW_THRESHOLD}
    assert s.status == "error" and s.error == LIMIT_MESSAGE
    assert s.counts["billed_linkedin"] == 3 and s.counts["billed_indeed"] == 0
    assert s.cost_usd == pytest.approx(3 * 0.0003)
    fin = final_events(events)
    assert fin["search"].status == "error" and fin["search"].message == LIMIT_MESSAGE
    assert [fin[step].status for step in ["filter", "score", "save"]] == ["done", "done", "done"]
    assert db.runs()[0]["status"] == "error" and db.runs()[0]["error"] == LIMIT_MESSAGE


def test_other_apify_error_marks_area_incomplete_and_continues(db):
    events = []
    src = FakeSource("linkedin", {
        "linkedin:EEA": AreaResult(jobs=[], billed=0, complete=False, error=ApifyError("Apify request failed (HTTP 500).")),
        "linkedin:UK": ok(make_job(job_id="2")),
    })  # fmt: skip
    s = make_pipeline(db, [src], now=lambda: T1, on_event=events.append).run()
    assert db.window_start("linkedin:EEA") is None and db.window_start("linkedin:UK") == T1
    search = final_events(events)["search"]
    assert search.status == "done"
    assert "incomplete" in search.message and "EEA" in search.message
    assert s.status == "done" and s.counts["scored"] == 1


def test_rejected_apify_token_stops_every_area_and_fails_the_search(db):
    events = []
    auth = ApifyError(AUTH_MESSAGE, kind="auth")
    linkedin = FakeSource("linkedin", {
        "linkedin:A": AreaResult([], 0, False, auth),
        "linkedin:B": ok(make_job(job_id="1")),
    })  # fmt: skip
    indeed = FakeSource("indeed", {"indeed:de": ok(make_job(source="indeed", job_id="x"))})
    s = make_pipeline(db, [linkedin, indeed], on_event=events.append).run()
    assert [k for k, _ in linkedin.calls] == ["linkedin:A"] and indeed.calls == []
    assert s.status == "error" and s.error == AUTH_MESSAGE
    fin = final_events(events)
    assert fin["search"].status == "error" and fin["search"].message == AUTH_MESSAGE
    assert [fin[step].status for step in ["filter", "score", "save"]] == ["done"] * 3


def test_search_fails_when_no_area_completes(db):
    events = []
    down = ApifyError("Apify request failed (HTTP 500).")
    src = FakeSource("linkedin", {"linkedin:A": AreaResult([], 0, False, down), "linkedin:B": RuntimeError("x")})
    s = make_pipeline(db, [src], on_event=events.append).run()
    fin = final_events(events)
    assert fin["search"].status == "error" and "A (Apify request failed (HTTP 500).)" in fin["search"].message
    assert s.status == "error" and s.error.startswith("No search completed.")
    assert [fin[step].status for step in ["filter", "score", "save"]] == ["done"] * 3


def test_no_searches_configured_still_completes(db):
    events = []
    s = make_pipeline(db, [], on_event=events.append).run()
    search = final_events(events)["search"]
    assert search.status == "skipped" and search.message == "No searches configured"
    assert s.status == "done"
    assert [final_events(events)[step].status for step in ["filter", "score", "save"]] == ["done"] * 3


def test_unexpected_search_exception_marks_only_that_area_incomplete(db):
    events = []
    src = FakeSource("linkedin", {
        "linkedin:EEA": RuntimeError("Something broke"),
        "linkedin:UK": ok(make_job(job_id="2")),
    })  # fmt: skip
    s = make_pipeline(db, [src], on_event=events.append, now=lambda: T1).run()
    fin = final_events(events)
    assert fin["search"].status == "done"
    assert "incomplete" in fin["search"].message and "EEA (The search failed unexpectedly.)" in fin["search"].message
    assert "Something broke" not in fin["search"].message
    assert [fin[step].status for step in ["filter", "score", "save"]] == ["done"] * 3
    assert db.window_start("linkedin:EEA") is None and db.window_start("linkedin:UK") == T1
    assert s.status == "done" and s.counts["scored"] == 1


def test_malformed_actor_item_costs_only_itself(db, respx_mock):
    from jobscout.settings import LinkedInSettings, Region
    from jobscout.sources.apify import ApifyClient
    from jobscout.sources.linkedin import LinkedInSource

    respx_mock.post(url__regex=r".*run-sync.*").respond(200, json=[
        {"id": "1", "title": "PM", "companyName": "A", "location": "X", "link": "u"},
        {"id": "2", "title": {"text": "PM"}, "companyName": "B", "location": "Y", "link": "u"},
    ])  # fmt: skip
    li = LinkedInSettings(keywords=["pm"], regions=[Region(name="UK", geo_id="1")])
    src = LinkedInSource(li, ApifyClient("t", httpx.Client(), sleep=no_sleep), threading.Event())
    s = make_pipeline(db, [src], now=lambda: T1).run()
    assert s.status == "done" and s.counts["searched"] == 2 and s.counts["billed_linkedin"] == 2
    assert db.get("linkedin:1") is not None and db.get("linkedin:2").title == ""
    assert db.window_start("linkedin:UK") == T1  # paid for once, never again


# --- filter ---


def test_filter_drops_jobs_already_seen_and_keeps_first_in_run(db):
    seen = make_job(job_id="old", title="Product Analyst", company="Moorgate Labs", location="Leeds, UK")
    db.insert_scraped([seen], {seen.pk: "product analyst|moorgate labs|leeds"})
    db.save_score(ScoreResult(seen.pk, 20, "No.", [], "", ""), min_score=70)
    again = make_job(job_id="new", title="Product analyst", company="Moorgate Labs", location="Leeds, England")
    # Same posting twice in one run; "b" arrives first and must win although "a" sorts first.
    b = make_job(job_id="b", title="Product Owner", company="Cobble & Kite", location="Edinburgh")
    a = make_job(job_id="a", title="Product Owner", company="Cobble & Kite", location="Edinburgh, Scotland")
    src = FakeSource("linkedin", {"linkedin:UK": ok(again, b, a)})
    s = make_pipeline(db, [src]).run()
    assert db.get(again.pk).state == JobState.DUPLICATE
    assert db.get(a.pk).state == JobState.DUPLICATE and db.get(b.pk).state != JobState.DUPLICATE
    assert s.counts["duplicates"] == 2 and s.counts["new"] == 1


def test_run_log_messages_pluralise(db):
    seen = make_job(job_id="old", title="Product Analyst", company="Moorgate Labs", location="Leeds, UK")
    db.insert_scraped([seen], {seen.pk: "product analyst|moorgate labs|leeds"})
    db.save_score(ScoreResult(seen.pk, 20, "No.", [], "", ""), min_score=70)
    again = make_job(job_id="new", title="Product analyst", company="Moorgate Labs", location="Leeds, England")
    events = []
    make_pipeline(db, [FakeSource("linkedin", {"linkedin:UK": ok(again)})], on_event=events.append).run()
    fin = final_events(events)
    assert fin["search"].message == "1 of 1 search · 1 posting"
    assert fin["filter"].message == "0 new · 1 duplicate · 0 excluded places"
    for event in events:
        for wrong in ("1 duplicates", "1 postings", "1 searches", "1 jobs", "1 places", "1 new places"):
            assert wrong not in event.message


# --- score ---


def test_scoring_failure_leaves_jobs_for_next_run(db):
    events = []
    s1 = sample_pipeline(db, provider=FailingProvider(), on_event=events.append).run()
    assert s1.counts["scored"] == 0 and s1.counts["failed_scoring"] == SAMPLE_NEW
    assert s1.status == "error" and s1.error == "The AI provider is down."
    assert len(db.jobs_in_state(JobState.SCRAPED)) == SAMPLE_NEW
    score = final_events(events)["score"]
    assert score.message.startswith(f"0 of {SAMPLE_NEW} scored · {SAMPLE_NEW} will be retried next run")
    assert "The AI provider is down." in score.message
    # Next run scores them without searching again.
    s2 = make_pipeline(db, [], settings=sample_settings()).run()
    assert s2.counts["scored"] == SAMPLE_NEW and s2.counts["searched"] == 0
    assert db.jobs_in_state(JobState.SCRAPED) == []


def test_max_jobs_per_run_defers_rest(db):
    events = []
    settings = sample_settings()
    settings.scoring.max_jobs_per_run = 5
    s1 = sample_pipeline(db, settings=settings, on_event=events.append).run()
    assert s1.counts["scored"] == 5 and s1.counts["failed_scoring"] == 0
    assert len(db.jobs_in_state(JobState.SCRAPED)) == SAMPLE_NEW - 5
    assert final_events(events)["score"].message == f"5 of 5 scored · {SAMPLE_NEW - 5} will be retried next run"
    s2 = sample_pipeline(db, settings=settings).run()
    assert s2.counts["scored"] == 5
    assert len(db.jobs_in_state(JobState.SCRAPED)) == SAMPLE_NEW - 10


def test_min_score_zero_keeps_all(db):
    settings = sample_settings()
    settings.scoring.min_score = 0
    s = sample_pipeline(db, settings=settings).run()
    assert s.counts["kept"] == s.counts["scored"] == SAMPLE_NEW and s.counts["below"] == 0
    assert len(db.tracked()) == SAMPLE_NEW


def test_rescore_skips_applied(db):
    sample_pipeline(db, provider=FakeProvider(), settings=_keep_all()).run()
    applied, other = [r.pk for r in db.tracked()][:2]
    db.set_status(applied, Status.APPLIED)
    before = db.get(applied).score
    events = []
    rescorer = sample_pipeline(
        db, provider=FakeProvider(scores={applied: 1, other: 99}), settings=_keep_all(), on_event=events.append
    )
    s = rescorer.rescore()
    assert s.status == "done" and s.counts["scored"] == SAMPLE_NEW - 1
    assert db.get(applied).score == before and db.get(other).score == 99
    fin = final_events(events)
    assert [fin[step].status for step in STEPS] == ["skipped", "skipped", "skipped", "done", "done"]
    assert s.counts["searched"] == 0


def test_rescore_scores_only_its_candidates(db):
    """Jobs left unfiltered by a cancelled run are never scored by a re-score."""
    cancel = threading.Event()

    def on_event(ev):
        if ev.step == "search" and ev.status == "running" and ev.message.startswith("UK: 2 of 2"):
            cancel.set()

    assert sample_pipeline(db, settings=_keep_all(), cancel=cancel, on_event=on_event).run().status == "cancelled"
    assert len(db.jobs_in_state(JobState.SCRAPED)) == 16
    s = make_pipeline(db, [], settings=_keep_all()).rescore()
    assert s.status == "done" and s.counts["scored"] == 0
    assert db.get("sample:eu-05").state == JobState.SCRAPED  # excluded place: never scored
    assert db.get("sample:uk-03").state == JobState.SCRAPED  # duplicate: never scored
    # The next normal run filters them as usual.
    make_pipeline(db, [], settings=_keep_all()).run()
    assert db.get("sample:eu-05").state == JobState.EXCLUDED
    assert db.get("sample:uk-03").state == JobState.DUPLICATE


def test_failed_rescore_keeps_previous_scores(db):
    sample_pipeline(db).run()
    before = {r.pk: (r.state, r.score) for r in db.tracked()}
    s = make_pipeline(db, [], provider=FailingProvider(), settings=sample_settings()).rescore()
    assert s.status == "error" and s.error == "The AI provider is down."
    assert {r.pk: (r.state, r.score) for r in db.tracked()} == before
    assert db.jobs_in_state(JobState.SCRAPED) == []


def test_rescore_redelivers_kept_jobs_and_archives_dropped_pages(db, respx_mock):
    keep = scored_row(db, make_job(job_id="1"))
    drop = scored_row(db, make_job(job_id="2", title="Other role"))
    for pk, page in ((keep, "page-1"), (drop, "page-2")):
        db.mark_delivery(pk, "notion", "delivered", external_id=page, pushed_status=Status.TO_REVIEW)
        db.mark_delivery(pk, "webhook", "delivered", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "To review"), _notion_page("page-2", "To review")],
                   "has_more": False},
    )  # fmt: skip
    update = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    archive = respx_mock.patch(f"{NOTION_API}/pages/page-2").respond(200, json={"id": "page-2"})
    hook = respx_mock.post(HOOK_URL).respond(200)
    s = make_pipeline(
        db, [], provider=FakeProvider(scores={keep: 95, drop: 10}), settings=_notion_settings(),
        notion=NotionClient("tok", httpx.Client(), sleep=no_sleep),
        webhook=WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep),
    ).rescore()  # fmt: skip

    assert s.status == "done" and s.counts["delivered"] == 2
    props = json.loads(update.calls[0].request.content)["properties"]
    assert props["Fit Score"] == {"number": 95} and props["Status"]["select"]["name"] == "To review"
    assert json.loads(archive.calls[0].request.content) == {"archived": True}
    assert hook.call_count == 1 and json.loads(hook.calls[0].request.content)["id"] == keep
    assert db.get_delivery(keep, "notion")["state"] == "delivered"
    assert db.get_delivery(drop, "notion")["external_id"] is None  # a new page if it comes back
    assert db.get(drop).state == JobState.BELOW_THRESHOLD


def _keep_all():
    settings = sample_settings()
    settings.scoring.min_score = 0
    return settings


# --- cancel ---


def test_cancel_skips_remaining_steps(db):
    cancel = threading.Event()
    events = []

    def on_event(ev):
        events.append(ev)
        if ev.step == "search" and ev.status == "running" and "1 of 2" in ev.message:
            cancel.set()

    s = sample_pipeline(db, cancel=cancel, on_event=on_event, now=lambda: T1).run()
    assert s.status == "cancelled"
    fin = final_events(events)
    assert fin["cleanup"].status == "done"
    assert [fin[step].status for step in ["search", "filter", "score", "save"]] == ["skipped"] * 4
    assert fin["search"].message.startswith("Cancelled")
    assert db.window_start("sample:Europe") is None
    assert db.jobs_in_state(JobState.SCRAPED)  # what was found is kept for the next run
    assert db.runs()[0]["status"] == "cancelled"


def test_cancel_before_start_skips_everything(db):
    cancel = threading.Event()
    cancel.set()
    events = []
    s = sample_pipeline(db, cancel=cancel, on_event=events.append).run()
    assert s.status == "cancelled"
    assert [e.status for e in events] == ["skipped"] * 5


# --- outputs ---


def test_webhook_failure_retried_next_run(db, respx_mock):
    route = respx_mock.post(HOOK_URL)
    route.side_effect = [httpx.Response(500)] * 3 + [httpx.Response(200)]
    hook = WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep)
    src = FakeSource("linkedin", {"linkedin:EEA": ok(make_job(job_id="1"))})
    events = []
    s1 = make_pipeline(db, [src], provider=FakeProvider(scores={"linkedin:1": 90}), webhook=hook,
                       on_event=events.append).run()  # fmt: skip
    assert s1.status == "done"
    assert s1.counts["delivered"] == 0 and s1.counts["delivery_failed"] == 1
    assert final_events(events)["save"].message == "1 kept · 0 sent · 1 failed"
    failed = db.get_delivery("linkedin:1", "webhook")
    assert failed["state"] == "failed" and failed["error"] == "The webhook returned HTTP 500."

    s2 = make_pipeline(db, [], webhook=hook).run()
    assert s2.counts["delivered"] == 1 and s2.counts["delivery_failed"] == 0
    assert route.call_count == 4
    assert json.loads(route.calls[-1].request.content)["id"] == "linkedin:1"
    delivered = db.get_delivery("linkedin:1", "webhook")
    assert delivered["state"] == "delivered" and delivered["pushed_status"] == "to_review"
    # Delivered once: a third run sends nothing.
    assert make_pipeline(db, [], webhook=hook).run().counts["delivered"] == 0


def test_webhook_gets_each_job_once(db, respx_mock):
    route = respx_mock.post(HOOK_URL).respond(200)
    hook = WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep)
    pk = scored_row(db, make_job(job_id="1"))
    assert make_pipeline(db, [], webhook=hook).run().counts["delivered"] == 1
    db.set_status(pk, Status.INTERESTED)
    assert make_pipeline(db, [], webhook=hook).run().counts["delivered"] == 0
    assert route.call_count == 1


def test_save_time_dedup_safety_net(db, respx_mock):
    route = respx_mock.post(HOOK_URL).respond(200)
    first, second = make_job(job_id="1"), make_job(job_id="2")
    db.insert_scraped([first, second], {first.pk: "same|key|city", second.pk: "same|key|city"})
    for job in (first, second):
        db.save_score(ScoreResult(job.pk, 90, "Good.", [], "", ""), min_score=70)
    s = make_pipeline(db, [], webhook=WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep)).run()
    assert db.get(first.pk).state == JobState.KEPT and db.get(second.pk).state == JobState.DUPLICATE
    assert s.counts["delivered"] == 1 and route.call_count == 1
    assert s.counts["duplicates"] == 1


def _notion_settings():
    settings = Settings()
    settings.outputs.notion.enabled = True
    settings.outputs.notion.database_id = DB_ID
    return settings


def _notion_page(page_id, label):
    return {
        "id": page_id,
        "last_edited_time": "2026-11-01T08:00:00.000Z",
        "properties": {"Status": {"select": {"name": label}}},
    }


def test_notion_status_readback_wins_and_prevents_archive(db, respx_mock):
    past = "2026-01-15"
    applied = scored_row(db, make_job(job_id="1"), deadline=past)
    stale = scored_row(db, make_job(job_id="2", title="Other role"), deadline=past)
    db.mark_delivery(applied, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.mark_delivery(stale, "notion", "delivered", external_id="page-2", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "Applied"), _notion_page("page-2", "To review")],
                   "has_more": False},
    )  # fmt: skip
    archive = respx_mock.patch(f"{NOTION_API}/pages/page-2").respond(200, json={"id": "page-2"})
    events = []
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    s = make_pipeline(db, [], settings=_notion_settings(), notion=notion, on_event=events.append,
                      now=lambda: T1).run()  # fmt: skip

    row = db.get(applied)
    assert row.status == Status.APPLIED and row.archived_reason is None
    assert db.get_delivery(applied, "notion")["pushed_status"] == "applied"
    assert db.get(stale).archived_reason.startswith("deadline passed")
    assert json.loads(archive.calls[0].request.content) == {"archived": True}
    assert s.counts["archived"] == 1
    assert final_events(events)["cleanup"].message == "1 archived · 1 status change from Notion"
    assert s.counts["delivered"] == 0  # Notion already shows the status: nothing to push


def test_readback_keeps_a_failed_redelivery_pending(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"), score=95)  # re-scored; the push of the new score failed
    db.mark_delivery(pk, "notion", "failed", external_id="page-1", error="Notion is down.",
                     pushed_status=Status.TO_REVIEW)  # fmt: skip
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "Interested")], "has_more": False}
    )
    update = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    s = make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()

    assert db.get(pk).status == Status.INTERESTED  # Notion's edit still wins
    assert s.counts["delivered"] == 1 and update.call_count == 1  # and the new score is pushed anyway
    d = db.get_delivery(pk, "notion")
    assert d["state"] == "delivered" and d["external_id"] == "page-1" and d["pushed_status"] == "interested"


def test_readback_keeps_pending_state_and_page_id(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"))
    db.mark_delivery(pk, "notion", "pending", external_id="page-1", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False}
    )
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    from jobscout import _output_sync as sync

    assert sync.read_back_notion_statuses(db, notion, DB_ID) == 1
    d = db.get_delivery(pk, "notion")
    assert d["state"] == "pending" and d["external_id"] == "page-1" and d["pushed_status"] == "applied"
    assert db.get(pk).status == Status.APPLIED


def test_notion_save_creates_then_updates_pages(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"))
    query = respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query")
    query.side_effect = [
        httpx.Response(200, json={"results": [], "has_more": False}),
        httpx.Response(200, json={"results": [_notion_page("page-1", "To review")], "has_more": False}),
    ]
    create = respx_mock.post(f"{NOTION_API}/pages").respond(200, json={"id": "page-1"})
    update = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)

    s1 = make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    assert s1.counts["delivered"] == 1 and create.call_count == 1
    assert db.get_delivery(pk, "notion")["external_id"] == "page-1"

    db.set_status(pk, Status.INTERESTED)  # changed in the app: pushed to Notion
    s2 = make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    assert s2.counts["delivered"] == 1 and create.call_count == 1 and update.call_count == 1
    sent = json.loads(update.calls[0].request.content)
    assert sent["properties"]["Status"]["select"]["name"] == "Interested"
    assert db.get(pk).status == Status.INTERESTED
    assert db.get_delivery(pk, "notion")["pushed_status"] == "interested"


def test_notion_failure_keeps_page_id_and_never_stops_the_step(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"))
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.set_status(pk, Status.INTERESTED)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "To review")], "has_more": False}
    )
    respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(404)
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    s = make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    assert s.status == "done" and s.counts["delivery_failed"] == 1
    d = db.get_delivery(pk, "notion")
    assert d["state"] == "failed" and d["external_id"] == "page-1" and d["pushed_status"] == "to_review"
    assert "Notion" in d["error"]
    assert db.get(pk).status == Status.INTERESTED  # the app's change is not undone by the read-back


def test_csv_auto_export(db, jobscout_home):
    settings = sample_settings()
    settings.outputs.csv.enabled = True
    sample_pipeline(db, settings=settings).run()
    text = (jobscout_home / "jobs.csv").read_text(encoding="utf-8-sig")
    assert text.startswith("Fit score,Title")
    assert len(text.strip().splitlines()) == 1 + len(db.tracked())


# --- robustness ---


def test_raising_listener_never_loses_paid_jobs(db):
    def on_event(ev):
        if ev.step == "search" and ev.status == "running":
            raise RuntimeError("listener broke")

    s = sample_pipeline(db, on_event=on_event, now=lambda: T1).run()
    assert s.status == "done" and s.counts["searched"] == 16
    assert db.window_start("sample:Europe") == T1 and db.window_start("sample:UK") == T1
    assert any(e.step == "search" and e.status == "running" for e in db.events(s.run_id))


class Escape(BaseException):
    """Not an Exception: escapes the per-step handler, like KeyboardInterrupt."""


def test_escaping_exception_still_closes_every_step(db):
    events = []
    src = FakeSource("linkedin", {"linkedin:EEA": Escape()})
    with pytest.raises(Escape):
        make_pipeline(db, [src], on_event=events.append).run()
    fin = final_events(events)
    assert fin["cleanup"].status == "done"
    assert fin["search"].status == "error" and fin["search"].message == "The run stopped unexpectedly."
    assert [fin[step].status for step in ["filter", "score", "save"]] == ["skipped"] * 3
    run = db.runs()[0]
    assert run["status"] == "error" and run["finished_at"]


def test_sample_passed_deadline_is_archived_next_run(db):
    first_day = datetime.now()  # SampleSource counts its deadlines from the real today
    deadline = (first_day + timedelta(days=21)).date().isoformat()
    sample_pipeline(db, settings=_keep_all(), now=lambda: first_day).run()
    assert db.get("sample:eu-08").deadline == deadline
    assert db.get("sample:eu-08").archived_reason is None
    later = first_day + timedelta(days=22)
    s = sample_pipeline(db, settings=_keep_all(), now=lambda: later).run()
    assert db.get("sample:eu-08").archived_reason == f"deadline passed ({deadline})"
    assert s.counts["archived"] == 1


# --- build_pipeline ---


def test_missing_key_fails_before_spending(db, respx_mock):
    s = sample_settings()
    s.scoring.provider = "openai"
    with pytest.raises(ConfigError, match="OpenAI"):
        build_pipeline(s, Secrets(apify_token="t"), db, "p", sample=False)
    assert not respx_mock.calls and db.runs() == []


def test_build_pipeline_validates_keys_and_outputs(db):
    s = sample_settings()
    s.scoring.provider = "ollama"
    with pytest.raises(ConfigError, match="Apify"):
        build_pipeline(s, Secrets(), db, "p")
    s.scoring.provider = "openai"
    with pytest.raises(ConfigError, match="OpenAI"):
        build_pipeline(s, Secrets(apify_token="t"), db, "p")
    s.scoring.provider = "ollama"
    s.outputs.notion.enabled = True
    with pytest.raises(ConfigError, match="Notion"):
        build_pipeline(s, Secrets(apify_token="t", notion_token="n"), db, "p")
    s.outputs.notion.enabled = False
    s.outputs.webhook.enabled = True
    with pytest.raises(ConfigError, match="webhook"):
        build_pipeline(s, Secrets(apify_token="t"), db, "p")
    assert db.runs() == []


def test_fake_ai_is_only_for_sample_mode(db):
    s = sample_settings()  # provider "fake"
    with pytest.raises(ConfigError, match=r"Pick an AI provider first\."):
        build_pipeline(s, Secrets(apify_token="t"), db, "p")
    with build_pipeline(s, Secrets(), db, SAMPLE_PROFILE, sample=True) as p:
        assert p.run().status == "done"


def test_rescore_pipeline_needs_no_apify_token(db):
    s = sample_settings()
    s.scoring.provider = "ollama"
    with pytest.raises(ConfigError, match="Apify"):
        build_pipeline(s, Secrets(), db, "p")
    with build_pipeline(s, Secrets(), db, "p", for_rescore=True) as p:
        assert p._sources == []


def test_enabled_source_with_nothing_to_search_is_skipped(db):
    s = Settings()  # LinkedIn on by default, with no keywords or regions
    s.scoring.provider = "ollama"  # needs no key; nothing is scored
    events = []
    with build_pipeline(s, Secrets(), db, "p", on_event=events.append) as p:
        summary = p.run()
    assert summary.status == "done"
    assert final_events(events)["search"].message == "No searches configured"


def test_sample_mode_needs_no_keys_and_sends_nowhere(db):
    s = sample_settings()
    s.scoring.provider = "openai"
    s.outputs.webhook.enabled = True
    s.outputs.webhook.url = HOOK_URL
    with build_pipeline(s, Secrets(), db, SAMPLE_PROFILE, sample=True) as p:
        summary = p.run()
    assert summary.status == "done"
    assert summary.counts["delivered"] == summary.counts["delivery_failed"] == 0


def test_scores_are_saved_per_batch(db):
    """A failure on a later batch never loses the scores already paid for."""

    class Boom:
        calls = 0

        def complete(self, prompt):
            Boom.calls += 1
            if Boom.calls == 3:
                raise RuntimeError("unexpected")
            return FakeProvider().complete(prompt)

    jobs = [make_job(job_id=str(i), title=f"T{i}") for i in range(30)]
    db.insert_scraped(jobs, {j.pk: j.pk for j in jobs})
    settings = Settings()
    settings.scoring.batch_size = 10
    s = make_pipeline(db, [], Boom(), settings=settings).run()
    assert s.counts["scored"] == 20 and s.counts["failed_scoring"] == 10
    assert len(db.jobs_in_state(JobState.SCRAPED)) == 10
    assert s.status == "done"  # some jobs scored: the step does not fail


def test_scores_survive_a_run_killed_mid_scoring(db):
    class KilledOnSecondBatch:
        calls = 0

        def complete(self, prompt):
            KilledOnSecondBatch.calls += 1
            if KilledOnSecondBatch.calls == 2:
                raise Escape()
            return FakeProvider().complete(prompt)

    jobs = [make_job(job_id=str(i), title=f"T{i}") for i in range(20)]
    db.insert_scraped(jobs, {j.pk: j.pk for j in jobs})
    settings = Settings()
    settings.scoring.batch_size = 10
    with pytest.raises(Escape):
        make_pipeline(db, [], KilledOnSecondBatch(), settings=settings).run()
    assert len(db.jobs_in_state(JobState.SCRAPED)) == 10  # the first batch is safe


def test_rescore_reads_notion_statuses_first(db, respx_mock):
    """A job marked Applied in Notion before a re-score is never re-scored nor pushed back to To review."""
    pk = scored_row(db, make_job(job_id="1"), score=80)
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False}
    )
    page = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    s = make_pipeline(db, [], FakeProvider({pk: 85}), settings=_notion_settings(), notion=notion).rescore()
    assert s.status == "done" and s.counts["scored"] == 0
    assert not page.called
    row = db.get(pk)
    assert row.status == Status.APPLIED and row.score == 80
    assert db.get_delivery(pk, "notion")["pushed_status"] == "applied"


def test_rescore_pushes_nothing_to_notion_when_statuses_cant_be_read(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"), score=80)
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.mark_delivery(pk, "webhook", "delivered", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(500)
    page = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    hook = respx_mock.post(HOOK_URL).respond(200)
    events = []
    s = make_pipeline(
        db, [], FakeProvider({pk: 95}), settings=_notion_settings(), on_event=events.append,
        notion=NotionClient("tok", httpx.Client(), sleep=no_sleep),
        webhook=WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep),
    ).rescore()  # fmt: skip
    assert s.counts["scored"] == 1 and db.get(pk).score == 95
    assert not page.called and hook.call_count == 1  # other outputs are unaffected
    assert db.get_delivery(pk, "notion")["state"] == "pending"  # pushed after the next read-back
    assert "Notion not updated" in final_events(events)["score"].message


def test_rescore_never_touches_jobs_the_user_acted_on(db):
    acted = {
        status: scored_row(db, make_job(job_id=status.value, title=status.value), score=80, status=status)
        for status in (Status.INTERESTED, Status.NOT_INTERESTED, Status.REJECTED, Status.APPLIED)
    }
    review = scored_row(db, make_job(job_id="r", title="Review me"), score=80)
    below = scored_row(db, make_job(job_id="b", title="Below"), score=10)
    scores = {pk: 5 for pk in acted.values()} | {review: 99, below: 99}
    s = make_pipeline(db, [], FakeProvider(scores)).rescore()
    assert s.counts["scored"] == 2
    assert all(db.get(pk).score == 80 and db.get(pk).state == JobState.KEPT for pk in acted.values())
    assert db.get(review).score == 99 and db.get(below).state == JobState.KEPT


def test_restored_job_stays_restored_until_its_status_changes(db):
    pk = scored_row(db, make_job(job_id="1"), deadline="2020-01-01")
    make_pipeline(db, []).run()
    assert db.get(pk).archived_reason
    db.restore(pk)
    make_pipeline(db, []).run()
    assert db.get(pk).archived_reason is None
    db.set_status(pk, Status.INTERESTED)
    make_pipeline(db, []).run()
    assert db.get(pk).archived_reason == "deadline passed (2020-01-01)"


def test_restored_job_gets_a_new_notion_page(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"), deadline="2020-01-01")
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "To review")], "has_more": False}
    )
    archive = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={"id": "page-1"})
    create = respx_mock.post(f"{NOTION_API}/pages").respond(200, json={"id": "page-2"})
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    assert archive.call_count == 1
    d = db.get_delivery(pk, "notion")
    assert d["state"] == "pending" and d["external_id"] is None
    db.restore(pk)
    make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    assert create.call_count == 1 and db.get_delivery(pk, "notion")["external_id"] == "page-2"


def test_cleanup_archives_notion_pages_of_jobs_archived_elsewhere_and_retries_failures(db, respx_mock):
    """A job archived by `jobscout cleanup` (local only), or whose page archive failed, loses its page next run."""
    local = scored_row(db, make_job(job_id="1"))
    db.archive(local, "deadline passed (2020-01-01)")
    failed = scored_row(db, make_job(job_id="2", title="Other"))
    db.archive(failed, "deadline passed (2020-01-01)")
    db.mark_delivery(local, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.mark_delivery(failed, "notion", "failed", external_id="page-2", error="Couldn't archive the Notion page.",
                     pushed_status=Status.TO_REVIEW)  # fmt: skip
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(200, json={"results": [], "has_more": False})
    pages = [respx_mock.patch(f"{NOTION_API}/pages/page-{n}").respond(200, json={}) for n in (1, 2)]
    events = []
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    s = make_pipeline(db, [], settings=_notion_settings(), notion=notion, on_event=events.append).run()
    assert [p.call_count for p in pages] == [1, 1]
    assert all(json.loads(p.calls[0].request.content) == {"archived": True} for p in pages)
    assert s.counts["archived"] == 0 and final_events(events)["cleanup"].message == "0 archived"
    assert db.get_delivery(local, "notion")["external_id"] is None


def test_job_marked_applied_in_notion_after_a_local_archive_is_restored(db, respx_mock):
    pk = scored_row(db, make_job(job_id="1"))
    db.archive(pk, "posted 2026-01-01, over 60 days ago")
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    respx_mock.post(f"{NOTION_API}/databases/{DB_ID}/query").respond(
        200, json={"results": [_notion_page("page-1", "Applied")], "has_more": False}
    )
    page = respx_mock.patch(f"{NOTION_API}/pages/page-1").respond(200, json={})
    notion = NotionClient("tok", httpx.Client(), sleep=no_sleep)
    make_pipeline(db, [], settings=_notion_settings(), notion=notion).run()
    row = db.get(pk)
    assert row.status == Status.APPLIED and row.archived_reason is None
    assert not page.called


def test_every_ai_call_keeps_the_run_alive(db, monkeypatch):
    beats = []
    monkeypatch.setattr(db, "heartbeat", lambda run_id: beats.append(run_id))
    jobs = [make_job(job_id=str(i), title=f"T{i}") for i in range(3)]
    db.insert_scraped(jobs, {j.pk: j.pk for j in jobs})
    settings = Settings()
    settings.scoring.batch_size = 1
    s = make_pipeline(db, [], settings=settings).run()
    assert beats == [s.run_id] * 3


def _same_key_pair(db, first_kw=None, second_kw=None):
    first, second = make_job(job_id="1"), make_job(job_id="2")
    db.insert_scraped([first, second], {first.pk: "same|key|city", second.pk: "same|key|city"})
    for job in (first, second):
        db.save_score(ScoreResult(job.pk, 90, "Good.", [], "", ""), min_score=70)
    return first.pk, second.pk


def test_save_dedup_keeps_a_delivered_duplicate(db):
    first, second = _same_key_pair(db)
    db.mark_delivery(second, "notion", "delivered", external_id="page-2", pushed_status=Status.TO_REVIEW)
    s = make_pipeline(db, []).run()
    assert db.get(first).state == JobState.DUPLICATE and db.get(second).state == JobState.KEPT
    assert s.counts["duplicates"] == 1


def test_save_dedup_never_demotes_a_job_the_user_acted_on(db):
    first, second = _same_key_pair(db)
    db.set_status(second, Status.INTERESTED)
    make_pipeline(db, []).run()
    assert db.get(first).state == JobState.DUPLICATE and db.get(second).state == JobState.KEPT
    db.set_state(first, JobState.KEPT)
    db.set_status(first, Status.APPLIED)
    make_pipeline(db, []).run()
    assert db.get(first).state == db.get(second).state == JobState.KEPT  # both acted on: both stay


def test_changing_min_score_relabels_stored_scores(db, respx_mock):
    low = scored_row(db, make_job(job_id="1", title="Low"), score=60)
    mid = scored_row(db, make_job(job_id="2", title="Mid"), score=75)
    liked = scored_row(db, make_job(job_id="3", title="Liked"), score=75, status=Status.INTERESTED)
    assert db.get(low).state == JobState.BELOW_THRESHOLD
    hook = respx_mock.post(HOOK_URL).respond(200)
    settings = Settings()
    settings.scoring.min_score = 50
    s = make_pipeline(db, [], settings=settings, webhook=WebhookOutput(HOOK_URL, httpx.Client(), sleep=no_sleep)).run()
    assert db.get(low).state == JobState.KEPT and s.counts["delivered"] == 3 and hook.call_count == 3
    settings.scoring.min_score = 80
    make_pipeline(db, [], settings=settings).run()
    assert db.get(mid).state == JobState.BELOW_THRESHOLD and db.get(low).state == JobState.BELOW_THRESHOLD
    assert db.get(liked).state == JobState.KEPT  # acted on: stays in the tracker
    settings.scoring.min_score = 0
    make_pipeline(db, [], settings=settings).run()
    assert {db.get(pk).state for pk in (low, mid, liked)} == {JobState.KEPT}


def test_new_counts_only_jobs_found_this_run(db):
    leftover = make_job(job_id="old", title="Left over")
    db.insert_scraped([leftover], {leftover.pk: "left over|fjord labs|oslo"})
    events = []
    src = FakeSource("linkedin", {"linkedin:UK": ok(make_job(job_id="new", title="Fresh"))})
    s = make_pipeline(db, [src], on_event=events.append).run()
    assert s.counts["new"] == 1
    assert final_events(events)["filter"].message == (
        "1 new · 0 duplicates · 0 excluded places · 1 waiting from earlier runs"
    )


def test_sample_headline_and_no_deadline_flag_on_kept_jobs():
    assert sample_settings().ui.headline == "Software engineer, early career · Europe and the UK"
    for pk, answer in sample_answers().items():
        if answer["score"] >= sample_settings().scoring.min_score:
            assert "Deadline passed" not in answer["flags"], pk


def test_sample_start_dates_count_from_today():
    postings = {p["id"]: p for p in json.loads(SAMPLE_DATA.joinpath("sample_jobs.json").read_text(encoding="utf-8"))}
    assert not [i for i, p in postings.items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.get("start_date") or "")]
    today = date(2031, 5, 20)  # whatever day it is, the sample's start dates move with it
    answers = sample_answers(today)
    assert answers["sample:eu-01"]["start_date"] == "2031-07-19"
    assert answers["sample:uk-04"]["start_date"] == "2031-09-16"
    assert sample_answers(date(2031, 6, 1))["sample:eu-01"]["start_date"] == "2031-07-31"
