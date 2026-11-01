import sys

import pytest
from pydantic import ValidationError

from jobscout.errors import ConfigError
from jobscout.paths import app_dir, secrets_path, settings_path
from jobscout.settings import (
    CleanupSettings,
    IndeedSettings,
    LinkedInSettings,
    ScoringSettings,
    Secrets,
    Settings,
    load_profile,
    load_secrets,
    load_settings,
    save_profile,
    save_secrets,
    save_settings,
    WindowSettings,
)


def test_defaults_when_no_file():
    s = load_settings()
    assert s.scoring.min_score == 70
    assert s.linkedin.cap_per_keyword == 25
    assert s.linkedin.keywords == [] and s.linkedin.regions == []
    assert s.filters.excluded_countries == []
    assert s.cleanup.earliest_start is None
    assert s.indeed.enabled is False


def test_roundtrip_settings():
    s = Settings()
    s.scoring.min_score = 0
    save_settings(s)
    assert load_settings().scoring.min_score == 0


def test_min_score_bounds():
    with pytest.raises(ValidationError):
        ScoringSettings(min_score=101)


def test_corrupt_settings_raises_config_error():
    settings_path().write_text("{not json")
    with pytest.raises(ConfigError) as e:
        load_settings()
    assert "settings" in str(e.value).lower()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows doesn't use Unix permission bits.")
def test_secrets_file_is_private():
    save_secrets(Secrets(apify_token="test-token-123"))
    assert oct(secrets_path().stat().st_mode & 0o777) == "0o600"
    assert load_secrets().apify_token == "test-token-123"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows doesn't use Unix permission bits.")
def test_settings_file_is_private_because_the_webhook_url_can_hold_a_secret():
    save_settings(Settings())
    assert oct(settings_path().stat().st_mode & 0o777) == "0o600"
    settings_path().chmod(0o644)  # a file written by an earlier version
    save_settings(Settings())
    assert oct(settings_path().stat().st_mode & 0o777) == "0o600"
    assert not list(app_dir().glob(".*.tmp"))


def test_app_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_HOME", str(tmp_path / "x"))
    assert app_dir() == tmp_path / "x" and app_dir().is_dir()


def test_unknown_keys_are_ignored():
    settings_path().write_text('{"scoring": {"min_score": 55, "future_field": 1}, "new_section": {}}')
    assert load_settings().scoring.min_score == 55


def test_invalid_values_raise_config_error():
    settings_path().write_text('{"scoring": {"min_score": 500}}')
    with pytest.raises(ConfigError):
        load_settings()


def test_default_bands_has_four_lines():
    lines = ScoringSettings().bands.split("\n")
    assert len(lines) == 4
    assert lines[0].startswith("85-100:") and lines[3].startswith("0-39:")


def test_no_personal_defaults():
    s = Settings()
    assert s.scoring.dealbreakers == []
    assert s.linkedin.keywords == [] and s.indeed.queries == []
    assert s.filters.excluded_cities == []


def test_missing_secrets_are_none():
    assert load_secrets() == Secrets()


def test_corrupt_secrets_error_does_not_leak_content():
    secrets_path().write_text("{sk-supersecret")
    with pytest.raises(ConfigError) as e:
        load_secrets()
    assert "sk-supersecret" not in str(e.value)


def test_profile_roundtrip():
    assert load_profile() == ""
    save_profile("I am a fictional engineer.")
    assert load_profile() == "I am a fictional engineer."


@pytest.mark.parametrize(
    "model,field,bad,good",
    [
        (LinkedInSettings, "cap_per_keyword", [0, 1001, -5], [1, 1000]),
        (LinkedInSettings, "price_per_result", [-0.001], [0, 0.002]),
        (LinkedInSettings, "sort_by", ["date", "dd"], ["DD", "R", ""]),
        (IndeedSettings, "max_rows_per_query", [0, 1001], [1, 1000]),
        (IndeedSettings, "price_per_result", [-1], [0]),
        (IndeedSettings, "job_types", [["full-time"], ["internship", "nope"]],
         [["fulltime", "parttime", "contract", "internship", "temporary", "permanent", "seasonal", "freelance"]]),
        (WindowSettings, "default_days", [0, 366], [1, 365]),
        (WindowSettings, "overlap_minutes", [-1, 1441], [0, 1440]),
        (ScoringSettings, "batch_size", [0, 51], [1, 50]),
        (ScoringSettings, "max_jobs_per_run", [0, -1], [None, 1]),
        (CleanupSettings, "max_age_days", [0, -3], [1, 60]),
    ],
)
def test_numeric_and_choice_settings_are_bounded(model, field, bad, good):
    for value in bad:
        with pytest.raises(ValidationError):
            model(**{field: value})
    for value in good:
        assert getattr(model(**{field: value}), field) == value


def test_saves_are_atomic(monkeypatch):
    save_settings(Settings())
    save_profile("Old profile.")
    save_secrets(Secrets(apify_token="old-token"))
    import jobscout.settings as settings_module

    def broken_replace(src, dst):
        raise OSError("disk full")

    s = Settings()
    s.scoring.min_score = 5
    with monkeypatch.context() as m:
        m.setattr(settings_module.os, "replace", broken_replace)
        for save in (lambda: save_settings(s), lambda: save_profile("New"), lambda: save_secrets(Secrets(apify_token="n"))):
            with pytest.raises(OSError):
                save()
    assert load_settings().scoring.min_score == 70
    assert load_profile() == "Old profile." and load_secrets().apify_token == "old-token"
    assert sorted(p.name for p in app_dir().iterdir()) == ["profile.md", "secrets.json", "settings.json"]
