import argparse
import signal
import threading
from datetime import date, datetime, timedelta

import pytest
from conftest import make_job

from jobscout import paths
from jobscout.cli import _cancel_on_ctrl_c, _finish, _Printer, main, parse_window
from jobscout.costs import LIMITS_URL
from jobscout.db import Database
from jobscout.models import RunSummary, ScoreResult, Status, StepEvent
from jobscout.scoring.providers import FakeProvider
from jobscout.settings import (
    LinkedInSettings,
    Region,
    ScoringSettings,
    Secrets,
    Settings,
    save_profile,
    save_secrets,
    save_settings,
)


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    """Real mode refuses the fake AI, so tests pick Ollama (no key) and score with the fake one."""
    monkeypatch.setattr("jobscout.pipeline.make_provider", lambda cfg, secrets: FakeProvider())


def real_setup(profile="I am a fictional analyst looking for a data internship.", **settings):
    """A real-mode app folder that needs no keys: an offline AI and nothing to search."""
    save_settings(Settings(scoring=ScoringSettings(provider="ollama", model="llama3"), **settings))
    if profile:
        save_profile(profile)


def stored_job(job_id="1", **kw):
    db = Database(paths.db_path())
    job = make_job(job_id=job_id, **kw)
    db.insert_scraped([job], {job.pk: f"key-{job_id}"})
    db.save_score(ScoreResult(job.pk, 80, "Good match.", [], "", ""), min_score=70)
    db.close()
    return job.pk


# --- run ---


def test_run_sample_prints_steps(capsys, jobscout_home):
    assert main(["run", "--sample"]) == 0
    out = capsys.readouterr().out
    assert "[1/5] Clean up · Done" in out and "[5/5] Save · Done" in out
    assert "[2/5] Search · Running" in out and "[4/5] Score · Running" in out
    assert out.strip().splitlines()[-1] == "Done · 7 kept · ~$0.00"  # the sample answers keep 7 of 14
    assert (jobscout_home / "sample.db").exists() and not paths.db_path().exists()


def test_run_sample_progress_lines_are_not_repeated(capsys):
    main(["run", "--sample"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(set(lines))


def test_run_prints_the_most_it_can_cost_first(capsys, monkeypatch):
    linkedin = LinkedInSettings(keywords=["a", "b"], regions=[Region(name="Norway", geo_id="1")], cap_per_keyword=10)
    real_setup(linkedin=linkedin)
    save_secrets(Secrets(apify_token="test-token-123"))
    import jobscout.pipeline

    original = jobscout.pipeline.Pipeline.run
    first_line = []

    def spy(self, *a, **kw):
        first_line.append(capsys.readouterr().out)
        self._sources = []  # no real searching in a test
        return original(self, *a, **kw)

    monkeypatch.setattr(jobscout.pipeline.Pipeline, "run", spy)
    assert main(["run"]) == 0
    assert first_line == ["This run can cost up to $0.04 on Apify (2 searches, at most 20 results).\n"]


def test_sample_run_prints_no_cost_line(capsys):
    main(["run", "--sample"])
    assert "can cost" not in capsys.readouterr().out


def test_rescore_needs_no_apify_token_and_reads_settings_once(capsys, monkeypatch):
    linkedin = LinkedInSettings(keywords=["data analyst"], regions=[Region(name="Norway", geo_id="1")])
    real_setup(linkedin=linkedin)
    stored_job()
    import jobscout.cli

    loads = []
    original = jobscout.cli.load_settings
    monkeypatch.setattr(jobscout.cli, "load_settings", lambda: loads.append(1) or original())
    assert main(["rescore", "--yes"]) == 0
    assert "[4/5] Score · Done · 1 of 1 scored" in capsys.readouterr().out
    assert loads == [1]


def test_run_real_mode_uses_its_own_database(capsys, jobscout_home):
    real_setup()
    assert main(["run", "--window", "week"]) == 0
    out = capsys.readouterr().out
    assert "[2/5] Search · Skipped · No searches configured" in out
    assert out.strip().splitlines()[-1] == "Done · 0 kept · ~$0.00"
    assert paths.db_path().exists() and not (jobscout_home / "sample.db").exists()


def test_window_parsing(capsys):
    assert [parse_window(v) for v in ("today", "week", "month", "21")] == [1, 7, 30, 21]
    for bad in ("abc", "0", "-3", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_window(bad)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--window", "abc"])
    assert exc.value.code == 2 and "not today, week, month" in capsys.readouterr().err


def test_window_is_passed_to_the_run(monkeypatch, capsys):
    seen = []
    from jobscout.pipeline import Pipeline

    original = Pipeline.run

    def spy(self, trigger="cli", override_days=None):
        seen.append(override_days)
        return original(self, trigger, override_days)

    monkeypatch.setattr(Pipeline, "run", spy)
    main(["run", "--sample", "--window", "week"])
    main(["run", "--sample"])
    assert seen == [7, None]


def test_config_error_exit_code(capsys):
    linkedin = LinkedInSettings(keywords=["data analyst"], regions=[Region(name="Norway", geo_id="103819153")])
    real_setup(linkedin=linkedin)
    assert main(["run"]) == 2
    captured = capsys.readouterr()
    assert "Apify token" in captured.err and "Traceback" not in captured.err
    assert captured.out == ""


def test_empty_profile_is_a_config_error(capsys):
    real_setup(profile="")
    assert main(["run"]) == 2
    err = capsys.readouterr().err
    assert "Your profile is empty" in err and str(paths.profile_path()) in err


def test_unreadable_settings_file_is_a_config_error(capsys):
    paths.settings_path().write_text("{not json", encoding="utf-8")
    assert main(["estimate"]) == 2
    assert "settings file could not be read" in capsys.readouterr().err


def test_finish_reports_error_and_cancel(capsys):
    error = RunSummary(1, "error", {}, 0.0, "Apify monthly limit reached.")
    assert _finish(error) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "Apify monthly limit reached.\n"
    assert _finish(RunSummary(2, "cancelled", {"kept": 3}, 0.0)) == 1
    assert capsys.readouterr().out == "Cancelled\n"
    assert _finish(RunSummary(3, "done", {"kept": 3}, 1.234)) == 0
    assert capsys.readouterr().out == "Done · 3 kept · ~$1.23\n"


def test_printer_prints_final_events_and_changed_progress_only(capsys):
    printer = _Printer()
    for ev in (
        StepEvent("search", "running", "Starting"),
        StepEvent("search", "running", "a", 0, 4),
        StepEvent("search", "running", "b", 2, 4),
        StepEvent("search", "running", "b again", 2, 4),
        StepEvent("filter", "running", "Starting"),
        StepEvent("save", "running", "x", 1, 3),
        StepEvent("search", "error", "Apify monthly limit reached.", 2, 4),
        StepEvent("score", "skipped", "Skipped because an earlier step failed"),
    ):
        printer(ev)
    assert capsys.readouterr().out.splitlines() == [
        "[2/5] Search · Running · b",
        "[2/5] Search · Error · Apify monthly limit reached.",
        "[4/5] Score · Skipped · Skipped because an earlier step failed",
    ]


def test_ctrl_c_cancels_once_then_restores_the_previous_handler(capsys):
    cancel = threading.Event()
    before = signal.getsignal(signal.SIGINT)
    with _cancel_on_ctrl_c(cancel):
        signal.raise_signal(signal.SIGINT)
        assert cancel.is_set()
        assert signal.getsignal(signal.SIGINT) == before  # a second Ctrl+C behaves as usual
    assert signal.getsignal(signal.SIGINT) == before
    assert "Ctrl+C again" in capsys.readouterr().err


# --- other commands ---


def test_paths_lists_files(capsys, jobscout_home):
    assert main(["paths"]) == 0
    out = capsys.readouterr().out
    for path in (jobscout_home, paths.settings_path(), paths.profile_path(), paths.db_path()):
        assert str(path) in out


def test_estimate_without_token(capsys):
    linkedin = LinkedInSettings(keywords=["a", "b"], regions=[Region(name="Norway", geo_id="1")], cap_per_keyword=10)
    save_settings(Settings(linkedin=linkedin))
    assert main(["estimate"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["Searches: 2", "Max results: 20", "Max cost: $0.04"]


def test_estimate_prints_credit_left(capsys, respx_mock):
    save_secrets(Secrets(apify_token="test-token-123"))
    route = respx_mock.get(LIMITS_URL).respond(
        200, json={"data": {"limits": {"maxMonthlyUsageUsd": 5}, "current": {"monthlyUsageUsd": 1.25}}}
    )
    assert main(["estimate"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Apify credit left: $3.75"
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token-123"


def test_estimate_skips_credit_line_when_apify_fails(capsys, respx_mock):
    save_secrets(Secrets(apify_token="test-token-123"))
    respx_mock.get(LIMITS_URL).respond(500)
    assert main(["estimate"]) == 0
    assert "credit" not in capsys.readouterr().out


def old_job_pk():
    return stored_job(posted=(date.today() - timedelta(days=400)).isoformat())


def test_cleanup_dry_run_lists_without_archiving(capsys):
    pk = old_job_pk()
    assert main(["cleanup", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would archive: Data Analyst Intern at Fjord Labs · posted" in out
    assert "1 would archive." in out
    db = Database(paths.db_path())
    assert db.get(pk).archived_reason is None
    db.close()


def test_cleanup_archives(capsys):
    pk = old_job_pk()
    assert main(["cleanup"]) == 0
    assert "archived: Data Analyst Intern at Fjord Labs · posted" in capsys.readouterr().out
    db = Database(paths.db_path())
    assert db.get(pk).archived_reason.startswith("posted")
    db.close()
    assert main(["cleanup"]) == 0
    assert "Nothing to archive." in capsys.readouterr().out


def test_cleanup_help_says_notion_pages_follow_on_the_next_run(capsys):
    with pytest.raises(SystemExit):
        main(["cleanup", "--help"])
    assert "Notion pages are archived on your next run." in " ".join(capsys.readouterr().out.split())


def _notion_on():
    settings = Settings()
    settings.outputs.notion.enabled = True
    settings.outputs.notion.database_id = "db-1"
    save_settings(settings)
    save_secrets(Secrets(notion_token="test-token-123"))


def test_cleanup_reads_notion_statuses_first(capsys, respx_mock):
    pk = old_job_pk()
    db = Database(paths.db_path())
    db.mark_delivery(pk, "notion", "delivered", external_id="page-1", pushed_status=Status.TO_REVIEW)
    db.close()
    _notion_on()
    page = {"id": "page-1", "last_edited_time": "2026-11-01T08:00:00.000Z",
            "properties": {"Status": {"select": {"name": "Applied"}}}}  # fmt: skip
    query = respx_mock.post("https://api.notion.com/v1/databases/db-1/query").respond(
        200, json={"results": [page], "has_more": False}
    )
    assert main(["cleanup"]) == 0
    assert query.called and "Nothing to archive." in capsys.readouterr().out
    db = Database(paths.db_path())
    assert db.get(pk).status == Status.APPLIED and db.get(pk).archived_reason is None
    db.close()


def test_cleanup_archives_nothing_when_notion_cant_be_read(capsys, respx_mock):
    pk = old_job_pk()
    _notion_on()
    respx_mock.post("https://api.notion.com/v1/databases/db-1/query").respond(401)
    assert main(["cleanup"]) == 1
    assert "couldn't read statuses from Notion" in capsys.readouterr().err
    db = Database(paths.db_path())
    assert db.get(pk).archived_reason is None
    db.close()


def test_rescore_asks_before_spending(capsys, monkeypatch):
    real_setup()
    stored_job()
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert main(["rescore"]) == 1
    out = capsys.readouterr().out
    assert "1 job will be scored again" in out and out.strip().splitlines()[-1] == "Cancelled"

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["rescore"]) == 0
    out = capsys.readouterr().out
    assert "[4/5] Score · Done · 1 of 1 scored" in out and "[1/5] Clean up · Skipped" in out
    assert out.strip().splitlines()[-1].startswith("Done · ")


def test_rescore_yes_skips_the_question(capsys, monkeypatch):
    real_setup()
    stored_job()
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("must not ask"))
    assert main(["rescore", "--yes"]) == 0
    assert "[4/5] Score · Done" in capsys.readouterr().out


def test_rescore_run_is_labelled_cli_rescore(capsys):
    real_setup()
    stored_job()
    assert main(["rescore", "--yes"]) == 0
    db = Database(paths.db_path())
    assert [r["trigger"] for r in db.runs()] == ["cli:rescore"]
    db.close()


def test_rescore_with_nothing_to_do(capsys):
    real_setup()
    assert main(["rescore"]) == 0
    assert capsys.readouterr().out == "Nothing to re-score.\n"


def test_rescore_without_input_does_not_continue(capsys, monkeypatch):
    real_setup()
    stored_job()

    def eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main(["rescore"]) == 1


def test_export_writes_tracked_jobs(capsys, tmp_path):
    stored_job("1")
    stored_job("2", title="Product Intern")
    target = tmp_path / "out" / "jobs.csv"
    assert main(["export", str(target)]) == 0
    assert f"Exported 2 jobs to {target}" in capsys.readouterr().out
    text = target.read_text(encoding="utf-8-sig")
    assert "Fit score,Title" in text and "Product Intern" in text


def test_run_in_progress_is_a_plain_message(capsys):
    real_setup()
    db = Database(paths.db_path())
    db.start_run("ui", datetime.now())
    db.close()
    assert main(["run"]) == 2
    err = capsys.readouterr().err
    assert err == "Another run is in progress. Wait for it to finish, or try again in a few minutes.\n"


def test_no_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
