import pytest
from fastapi.testclient import TestClient

from jobscout.server.app import create_app

BASE = "http://127.0.0.1:8765"
APP_HEADER = {"X-Requested-By": "ai-job-scout"}


@pytest.fixture(autouse=True)
def no_sample_pause(monkeypatch):
    """The app pauses between steps of a sample run so people can watch it; tests don't wait."""
    monkeypatch.setattr("jobscout.sources.sample.PACE_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def jobscout_home(tmp_path, monkeypatch):
    """Every test gets its own empty app-data folder."""
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path))
    return tmp_path


def make_job(**kw):
    """A fictional Job; override any field by keyword."""
    from jobscout.models import Job

    defaults = dict(
        source="linkedin",
        job_id="100",
        title="Data Analyst Intern",
        company="Fjord Labs",
        location="Oslo, Norway",
        url="https://example.com/jobs/100",
        description="Analyse product data and build dashboards.",
        posted="2026-10-20",
        keyword="data analyst intern",
        area="Norway",
    )
    defaults.update(kw)
    return Job(**defaults)


def make_row(**kw):
    """A fictional JobRow (kept, to review, score 80); override any field by keyword."""
    from jobscout.models import JobRow, JobState, Status

    job = make_job(**{k: kw.pop(k) for k in list(kw) if k in _JOB_FIELDS})
    defaults = dict(
        state=JobState.KEPT,
        status=Status.TO_REVIEW,
        score=80,
        reason="Good match.",
        flags=[],
        start_date="",
        deadline="",
        archived_reason=None,
        dedup_key=f"key-{job.job_id}",
        first_seen_at="2026-10-20T10:00:00",
        status_updated_at="2026-10-20T10:00:00",
    )
    defaults.update(kw)
    return JobRow(**vars(job), **defaults)


_JOB_FIELDS = {
    "source", "job_id", "title", "company", "location", "url", "description", "posted",
    "keyword", "area", "recruiter", "recruiter_title", "recruiter_url", "employment_type", "salary",
}


@pytest.fixture
def db():
    from jobscout.db import Database
    from jobscout.paths import db_path

    database = Database(db_path())
    yield database
    database.close()


@pytest.fixture
def static_dir(tmp_path):
    return tmp_path / "static"  # never created: most tests don't need a built frontend


@pytest.fixture
def client(static_dir):
    with TestClient(create_app(static_dir=static_dir), base_url=BASE, headers=APP_HEADER) as c:
        yield c


@pytest.fixture
def raw_client(static_dir):
    with TestClient(create_app(static_dir=static_dir), base_url=BASE) as c:
        yield c


@pytest.fixture
def sample_client(static_dir):
    with TestClient(create_app(static_dir=static_dir, sample=True), base_url=BASE, headers=APP_HEADER) as c:
        yield c
