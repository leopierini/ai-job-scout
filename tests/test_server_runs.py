import csv
import io
import threading
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from test_db_web import store

from jobscout.costs import LIMITS_URL
from jobscout.db import Database
from jobscout.models import JobState, Status
from jobscout.outputs.csv_out import CSV_COLUMNS
from jobscout.paths import app_dir, db_path
from jobscout.pipeline import build_pipeline
from jobscout.server import runner
from jobscout.server.app import create_app
from jobscout.server.routes_runs import CreditCache
from jobscout.settings import Secrets, save_profile, save_secrets
from jobscout.sources import AreaResult

BASE = "http://127.0.0.1:8765"
APP_HEADER = {"X-Requested-By": "ai-job-scout"}
STEPS_DONE = ["cleanup", "search", "filter", "score", "save"]


class BlockingSource:
    """Wraps a real source: its first search waits until released (or cancelled)."""

    def __init__(self, inner, cancel, started, release):
        self.inner, self.cancel, self.started, self.release = inner, cancel, started, release
        self.name = inner.name

    def areas(self):
        return self.inner.areas()

    def search_area(self, area, window_seconds, progress):
        self.started.set()
        deadline = 10  # seconds: a stuck test fails instead of hanging
        while deadline > 0 and not self.release.is_set() and not self.cancel.is_set():
            self.release.wait(0.05)
            deadline -= 0.05
        if self.cancel.is_set():
            return AreaResult(jobs=[], billed=0, complete=False)
        return self.inner.search_area(area, window_seconds, progress)


@pytest.fixture
def gate():
    return {"started": threading.Event(), "release": threading.Event()}


@pytest.fixture
def blocking_client(static_dir, gate):
    def factory(*args, cancel=None, **kwargs):
        pipeline = build_pipeline(*args, cancel=cancel, **kwargs)
        pipeline._sources = [BlockingSource(s, cancel, gate["started"], gate["release"]) for s in pipeline._sources]
        return pipeline

    app = create_app(static_dir=static_dir, sample=True, pipeline_factory=factory)
    with TestClient(app, base_url=BASE, headers=APP_HEADER) as c:
        yield c
    gate["release"].set()


@pytest.fixture
def manager(sample_client):
    return sample_client.app.state.runs


def start_blocked(c, gate, **body):
    rid = c.post("/api/runs", json={"kind": "run", **body}).json()["run_id"]
    assert gate["started"].wait(10)
    return rid


# --- runs -------------------------------------------------------------------------------------


def test_sample_run_through_api_completes(sample_client, manager):
    rid = sample_client.post("/api/runs", json={"kind": "run"}).json()["run_id"]
    manager.wait(30)
    body = sample_client.get(f"/api/runs/{rid}").json()
    assert body["run"]["status"] == "done"
    assert [e["step"] for e in body["events"] if e["status"] != "running"] == STEPS_DONE
    assert sample_client.get("/api/jobs/counts").json()["to_review"] > 0
    assert set(body["run"]) == {"id", "trigger", "started_at", "finished_at", "status", "counts", "cost_usd", "error"}
    assert body["run"]["trigger"] == "app"
    assert set(body["events"][0]) == {"seq", "step", "status", "message", "done", "total", "elapsed_s", "data"}


def test_events_after_cursor(sample_client, manager):
    rid = sample_client.post("/api/runs", json={"kind": "run"}).json()["run_id"]
    manager.wait(30)
    everything = sample_client.get(f"/api/runs/{rid}").json()["events"]
    assert [e["seq"] for e in everything] == list(range(1, len(everything) + 1))
    cursor = everything[3]["seq"]
    later = sample_client.get(f"/api/runs/{rid}?after={cursor}").json()["events"]
    assert later == everything[4:]
    assert sample_client.get(f"/api/runs/{rid}?after={everything[-1]['seq']}").json()["events"] == []


def test_unknown_run_is_404(client):
    r = client.get("/api/runs/999")
    assert r.status_code == 404 and r.json()["fields"] is None
    assert client.post("/api/runs/999/cancel").status_code == 404


def test_second_start_while_running_is_409(blocking_client, gate):
    start_blocked(blocking_client, gate)
    r = blocking_client.post("/api/runs", json={"kind": "run"})
    assert r.status_code == 409
    assert "Another run is in progress" in r.json()["error"]
    gate["release"].set()


def test_active_run_is_reported_then_cleared(blocking_client, gate):
    assert blocking_client.get("/api/runs/active").json() == {"run_id": None}
    rid = start_blocked(blocking_client, gate)
    assert blocking_client.get("/api/runs/active").json() == {"run_id": rid}
    gate["release"].set()
    blocking_client.app.state.runs.wait(30)
    assert blocking_client.get("/api/runs/active").json() == {"run_id": None}


def test_mode_switch_is_refused_while_a_run_is_active(blocking_client, gate):
    start_blocked(blocking_client, gate)
    assert blocking_client.put("/api/mode", json={"mode": "real"}).status_code == 409
    assert blocking_client.get("/api/state").json()["mode"] == "sample"


def test_run_survives_without_client(static_dir):
    app = create_app(static_dir=static_dir, sample=True)
    first = TestClient(app, base_url=BASE, headers=APP_HEADER)  # no `with`: the app itself keeps running
    rid = first.post("/api/runs", json={"kind": "run"}).json()["run_id"]
    first.close()  # the browser tab is gone; the run was not tied to it
    app.state.runs.wait(30)
    second = TestClient(app, base_url=BASE, headers=APP_HEADER)
    assert second.get(f"/api/runs/{rid}").json()["run"]["status"] == "done"
    app.state.runs.close()
    app.state.workspace.close()


def test_cancel_stops_run(blocking_client, gate):
    rid = start_blocked(blocking_client, gate)
    assert blocking_client.post(f"/api/runs/{rid}/cancel").json() == {"ok": True}
    blocking_client.app.state.runs.wait(30)
    body = blocking_client.get(f"/api/runs/{rid}").json()
    assert body["run"]["status"] == "cancelled"
    assert [e["step"] for e in body["events"] if e["status"] != "running"] == STEPS_DONE  # every step closed


def test_shutdown_cancels_and_joins_the_run_before_closing_the_database(static_dir, gate):
    def factory(*args, cancel=None, **kwargs):
        pipeline = build_pipeline(*args, cancel=cancel, **kwargs)
        pipeline._sources = [BlockingSource(s, cancel, gate["started"], gate["release"]) for s in pipeline._sources]
        return pipeline

    app = create_app(static_dir=static_dir, sample=True, pipeline_factory=factory)
    alive_when_db_closed = []
    workspace = app.state.workspace
    original_close = workspace.close

    def close():
        alive_when_db_closed.append(any(t.name.startswith("run-") and t.is_alive() for t in threading.enumerate()))
        original_close()

    workspace.close = close
    with TestClient(app, base_url=BASE, headers=APP_HEADER) as c:
        rid = start_blocked(c, gate)
    assert alive_when_db_closed == [False]
    check = Database(app_dir() / "sample.db")
    assert check.get_run(rid)["status"] == "cancelled"
    check.close()


def test_cancel_of_cli_run_is_explained(client, db):
    rid = db.start_run("cli", datetime.now())
    r = client.post(f"/api/runs/{rid}/cancel")
    assert r.status_code == 400
    assert r.json()["error"] == "This run was started from the command line. Stop it there with Ctrl+C."


def test_app_run_left_running_by_a_crash_is_interrupted_when_the_app_opens_its_data(static_dir, db):
    crashed = db.start_run("app", datetime.now())  # the terminal was closed mid-run: no shutdown, still running
    with TestClient(create_app(static_dir=static_dir), base_url=BASE, headers=APP_HEADER) as c:
        assert c.get("/api/runs/active").json() == {"run_id": None}
        run = c.get(f"/api/runs/{crashed}").json()["run"]
    assert run["status"] == "error" and run["error"] == "Interrupted"


def test_command_line_run_is_not_interrupted_when_the_app_opens(client, db):
    rid = db.start_run("cli", datetime.now())
    assert client.get("/api/runs/active").json() == {"run_id": rid}


def test_cancel_of_an_app_run_from_another_process_interrupts_it(client):
    client.get("/api/runs/active")  # the app has opened its database
    other = Database(db_path())
    rid = other.start_run("app", datetime.now())  # alive, but no thread of this server runs it
    other.close()
    assert client.post(f"/api/runs/{rid}/cancel").json() == {"ok": True}
    run = client.get(f"/api/runs/{rid}").json()["run"]
    assert run["status"] == "error" and run["error"] == "Interrupted"
    assert client.get("/api/runs/active").json() == {"run_id": None}


def test_close_warns_when_a_run_does_not_stop_in_time(manager, monkeypatch, caplog):
    monkeypatch.setattr(runner, "STOP_WAIT_S", 0.01)
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, args=(5,), daemon=True)
    stuck.start()
    manager._owned[7] = runner._Active(threading.Event(), stuck)
    with caplog.at_level("WARNING", logger="jobscout.server"):
        manager.close()
    release.set()
    manager._owned.pop(7, None)
    assert "Run 7 didn't stop in time; it will show as interrupted." in caplog.text


def test_start_without_profile_in_real_mode_is_400(client):
    r = client.post("/api/runs", json={"kind": "run"})
    assert r.status_code == 400
    assert r.json()["error"] == "Your profile is empty. Write it first, then try again."
    assert client.get("/api/runs").json() == []


def test_start_with_config_error_starts_nothing(client):
    save_profile("I am a fictional analyst.")
    r = client.post("/api/runs", json={"kind": "run"})  # the default provider needs a key or a pick
    assert r.status_code == 400
    assert client.get("/api/runs").json() == []


def test_window_days_must_be_positive(sample_client):
    assert sample_client.post("/api/runs", json={"kind": "run", "window_days": 0}).status_code == 422
    assert sample_client.post("/api/runs", json={"kind": "nope"}).status_code == 422


def test_rescore_runs_with_its_own_trigger(sample_client, manager):
    sample_client.post("/api/runs", json={"kind": "run"})
    manager.wait(30)
    rid = sample_client.post("/api/runs", json={"kind": "rescore"}).json()["run_id"]
    manager.wait(30)
    run = sample_client.get(f"/api/runs/{rid}").json()["run"]
    assert run["status"] == "done" and run["trigger"] == "app:rescore"


def test_runs_list_latest_and_limit(sample_client, manager):
    assert sample_client.get("/api/runs/latest").json() is None
    first = sample_client.post("/api/runs", json={"kind": "run"}).json()["run_id"]
    manager.wait(30)
    second = sample_client.post("/api/runs", json={"kind": "rescore"}).json()["run_id"]
    manager.wait(30)
    assert [r["id"] for r in sample_client.get("/api/runs").json()] == [second, first]
    assert [r["id"] for r in sample_client.get("/api/runs?limit=1").json()] == [second]
    latest = sample_client.get("/api/runs/latest").json()
    assert latest["run"]["id"] == second and latest["events"]
    after = latest["events"][-1]["seq"]
    assert sample_client.get(f"/api/runs/latest?after={after}").json()["events"] == []


# --- jobs -------------------------------------------------------------------------------------


def test_status_change_and_views(sample_client, manager):
    sample_client.post("/api/runs", json={"kind": "run"})
    manager.wait(30)
    jobs = sample_client.get("/api/jobs?view=to_review").json()
    assert jobs and "description" not in jobs[0] and "dedup_key" not in jobs[0]
    assert jobs[0]["status"] == "to_review" and jobs[0]["state"] == "kept"
    before = sample_client.get("/api/jobs/counts").json()
    pk = jobs[0]["pk"]
    r = sample_client.patch(f"/api/jobs/{pk}", json={"status": "applied"})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    after = sample_client.get("/api/jobs/counts").json()
    assert after["to_review"] == before["to_review"] - 1 and after["applied"] == before["applied"] + 1
    assert pk not in [j["pk"] for j in sample_client.get("/api/jobs?view=to_review").json()]
    assert pk in [j["pk"] for j in sample_client.get("/api/jobs?view=applied").json()]


def test_job_out_shape(client, db):
    store(db, job_id="100")
    job = client.get("/api/jobs?view=to_review").json()[0]
    assert set(job) == {
        "pk", "source", "job_id", "title", "company", "location", "url", "posted", "keyword", "area",
        "recruiter", "recruiter_title", "recruiter_url", "employment_type", "state", "status", "score",
        "reason", "flags", "start_date", "deadline", "archived_reason", "first_seen_at", "status_updated_at",
        "restored",
    }  # fmt: skip
    assert job["pk"] == "linkedin:100" and job["state"] == "kept" and job["status"] == "to_review"
    assert job["score"] == 80


def test_unknown_view_and_status_are_rejected(client):
    assert client.get("/api/jobs?view=nope").status_code == 400
    assert client.patch("/api/jobs/linkedin:1", json={"status": "nope"}).status_code == 422


def test_restore_and_keep(client, db):
    store(db, job_id="1", archived_reason="Deadline passed")
    store(db, job_id="2", state=JobState.BELOW_THRESHOLD, score=40)
    assert client.get("/api/jobs/counts").json()["archived"] == 1
    restored = client.post("/api/jobs/linkedin:1/restore")
    assert restored.status_code == 200 and restored.json()["archived_reason"] is None
    assert client.get("/api/jobs/counts").json()["archived"] == 0

    kept = client.post("/api/jobs/linkedin:2/keep")
    assert kept.status_code == 200 and kept.json()["state"] == "kept" and kept.json()["status"] == "interested"


def test_unknown_job_is_404_everywhere(client):
    for call in (
        lambda: client.patch("/api/jobs/linkedin:nope", json={"status": "applied"}),
        lambda: client.post("/api/jobs/linkedin:nope/restore"),
        lambda: client.post("/api/jobs/linkedin:nope/keep"),
    ):
        r = call()
        assert r.status_code == 404 and r.json() == {"error": "That job no longer exists.", "fields": None}


# --- estimate ---------------------------------------------------------------------------------


def estimate_body(client):
    s = client.get("/api/settings").json()
    s["linkedin"].update(
        enabled=True,
        keywords=["data analyst", "product analyst"],
        regions=[{"name": "Norway", "geo_id": "103819153"}],
        cap_per_keyword=25,
    )
    s["indeed"]["enabled"] = False
    return {"settings": s}


def credit_response(limit=50.0, used=12.5):
    return httpx.Response(200, json={"data": {"limits": {"maxMonthlyUsageUsd": limit}, "current": {"monthlyUsageUsd": used}}})


def test_estimate_uses_posted_settings(client, respx_mock):
    save_secrets(Secrets(apify_token="apify-token-1234"))
    route = respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    body = client.post("/api/estimate", json=estimate_body(client)).json()
    assert body["searches"] == 2 and body["max_results"] == 50
    assert body["max_usd"] == pytest.approx(50 * client.get("/api/settings").json()["linkedin"]["price_per_result"])
    assert body["credit_usd"] == 37.5
    assert route.calls.last.request.headers["authorization"] == "Bearer apify-token-1234"


def test_estimate_without_body_settings_uses_saved(client, respx_mock):
    respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    body = client.post("/api/estimate", json={"settings": None}).json()
    assert body["searches"] == 0 and body["max_results"] == 0 and body["credit_usd"] is None  # no token


def test_estimate_hides_credit_on_error(client, respx_mock):
    save_secrets(Secrets(apify_token="apify-token-1234"))
    respx_mock.get(LIMITS_URL).respond(500)
    r = client.post("/api/estimate", json=estimate_body(client))
    assert r.status_code == 200 and r.json()["credit_usd"] is None


def test_estimate_in_sample_mode_has_no_credit(sample_client, respx_mock):
    route = respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    assert sample_client.post("/api/estimate", json={"settings": None}).json()["credit_usd"] is None
    assert not route.called


def test_estimate_credit_is_cached_per_token(client, respx_mock):
    save_secrets(Secrets(apify_token="apify-token-1234"))
    route = respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    for _ in range(3):
        assert client.post("/api/estimate", json={"settings": None}).json()["credit_usd"] == 37.5
    assert route.call_count == 1


def test_credit_cache_expires_and_never_keeps_the_token():
    now = [1000.0]
    cache = CreditCache(clock=lambda: now[0])
    calls = []

    def fetch(token):
        calls.append(token)
        return float(len(calls))

    assert cache.get("secret-token", fetch) == 1.0
    assert cache.get("secret-token", fetch) == 1.0
    assert cache.get("other-token", fetch) == 2.0
    now[0] += 299
    assert cache.get("secret-token", fetch) == 1.0
    now[0] += 2
    assert cache.get("secret-token", fetch) == 3.0
    assert "secret-token" not in repr(vars(cache))


def test_credit_cache_does_not_keep_failures():
    cache = CreditCache(clock=lambda: 0.0)
    answers = iter([None, 4.0])
    assert cache.get("t", lambda _t: next(answers)) is None
    assert cache.get("t", lambda _t: next(answers)) == 4.0


# --- rescore preview, export, reset -----------------------------------------------------------


def test_rescore_preview_counts_candidates(client, db):
    save_profile("x" * 100)
    for i in range(2):
        store(db, job_id=str(i), description="d" * 40)
    store(db, job_id="9", description="d" * 40, status=Status.APPLIED)  # acted on: not a candidate
    # 2 candidates x 40 chars + 1 batch (batch_size 10) x 100 chars profile = 180 chars -> 45 tokens
    assert client.get("/api/rescore/preview").json() == {"count": 2, "approx_tokens": 45}


def test_rescore_preview_with_nothing(client):
    assert client.get("/api/rescore/preview").json() == {"count": 0, "approx_tokens": 0}


def test_export_csv_download(client, db):
    store(db, job_id="1", title="Data Analyst Intern")
    store(db, job_id="2", title="=SUM(A1)")
    r = client.get("/api/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="ai-job-scout-jobs-{date.today().isoformat()}.csv"'
    parsed = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert parsed[0] == CSV_COLUMNS and len(parsed) == 3
    assert "'=SUM(A1)" in [row[1] for row in parsed]
    assert not list(app_dir().glob("*.csv*"))  # the temp file is gone


def test_reset_needs_exact_confirm(client, db):
    store(db, job_id="100")
    for bad in ("Delete", "", "yes"):
        assert client.post("/api/reset", json={"confirm": bad}).status_code == 400
    assert client.post("/api/reset", json={}).status_code == 422
    assert db.get("linkedin:100") is not None
    assert client.post("/api/reset", json={"confirm": "delete"}).json() == {"ok": True}
    assert db.get("linkedin:100") is None


def test_reset_is_409_while_a_run_is_active(client, db):
    db.start_run("cli", datetime.now())
    assert client.post("/api/reset", json={"confirm": "delete"}).status_code == 409
