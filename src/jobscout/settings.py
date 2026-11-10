"""User settings, secrets and profile, stored as files in the app-data folder."""

import json
import os
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobscout.errors import ConfigError
from jobscout.paths import profile_path, secrets_path, settings_path

DEFAULT_BANDS = "\n".join(
    [
        "85-100: an excellent match for the target roles, level and location in the profile, with no dealbreaker.",
        "70-84: a good match with minor gaps (adjacent title, slightly different focus, less known company).",
        "40-69: a partial match: wrong focus, level a bit off, or notable gaps.",
        "0-39: clearly not a match, or a dealbreaker applies.",
    ]
)


class _Model(BaseModel):
    # Unknown keys are ignored so older versions can read newer files.
    model_config = ConfigDict(extra="ignore")


class Region(_Model):
    name: str
    geo_id: str


class LinkedInSettings(_Model):
    enabled: bool = True
    actor: str = "meka.im~linkedin-jobs-scraper"
    keywords: list[str] = []
    regions: list[Region] = []
    cap_per_keyword: int = Field(default=25, ge=1, le=1000)
    sort_by: Literal["DD", "R", ""] = "DD"  # most recent, most relevant, or LinkedIn's default
    scrape_company: bool = True
    price_per_result: float = Field(default=0.0003, ge=0)


# The `jobType` values the Indeed actor accepts (its input schema, checked 2026-11-01).
IndeedJobType = Literal[
    "fulltime", "parttime", "contract", "internship", "temporary", "permanent", "seasonal", "freelance"
]


class IndeedSettings(_Model):
    enabled: bool = False
    actor: str = "kaix~indeed-scraper"
    countries: list[str] = []
    queries: list[str] = []
    job_types: list[IndeedJobType] = ["internship", "fulltime"]
    max_rows_per_query: int = Field(default=20, ge=1, le=1000)
    price_per_result: float = Field(default=0.00005, ge=0)


class WindowSettings(_Model):
    default_days: int = Field(default=15, ge=1, le=365)
    overlap_minutes: int = Field(default=60, ge=0, le=1440)


# Languages `jobscout.language` can recognise, as ISO 639-1 codes.
PostingLanguage = Literal["en", "it", "de", "fr", "nl", "es", "pt", "sv", "da", "no", "pl"]


class FilterSettings(_Model):
    excluded_countries: list[str] = []
    excluded_cities: list[str] = []
    excluded_title_words: list[str] = []  # e.g. "Senior": a job whose title has one is dropped before scoring
    languages: list[PostingLanguage] = []  # keep only postings written in these; empty keeps every language


class Dealbreaker(_Model):
    text: str
    consequence: str


class ScoringSettings(_Model):
    provider: Literal["anthropic", "openai", "gemini", "openrouter", "ollama", "claude_code", "fake"] = "anthropic"
    model: str = "claude-sonnet-5-5"
    min_score: int = Field(default=70, ge=0, le=100)
    send_below_threshold: bool = False
    reasoning_language: str = "English"
    batch_size: int = Field(default=10, ge=1, le=50)
    max_jobs_per_run: int | None = Field(default=None, ge=1)
    bands: str = DEFAULT_BANDS
    dealbreakers: list[Dealbreaker] = []
    extra_instructions: str = ""


class CleanupSettings(_Model):
    enabled: bool = True
    deadline_passed: bool = True
    start_too_early: bool = True
    too_old: bool = True
    excluded_place: bool = True
    earliest_start: date | None = None
    max_age_days: int = Field(default=60, ge=1)
    interested_exempt_from_age: bool = True


class CsvOutputSettings(_Model):
    enabled: bool = False
    path: str = ""
    auto_export: bool = True


class WebhookOutputSettings(_Model):
    enabled: bool = False
    url: str = ""


class NotionOutputSettings(_Model):
    enabled: bool = False
    parent_page_id: str = ""
    database_id: str = ""


class OutputSettings(_Model):
    csv: CsvOutputSettings = CsvOutputSettings()
    webhook: WebhookOutputSettings = WebhookOutputSettings()
    notion: NotionOutputSettings = NotionOutputSettings()


TableColumn = Literal["score", "role", "why", "starts", "posted", "deadline", "source"]


class UiSettings(_Model):
    setup_complete: bool = False
    headline: str = Field(default="", max_length=120)  # top-bar profile summary
    columns: list[TableColumn] = ["score", "role", "why", "starts", "posted"]
    default_sort: Literal["score", "posted", "starts"] = "score"


class Settings(_Model):
    linkedin: LinkedInSettings = LinkedInSettings()
    indeed: IndeedSettings = IndeedSettings()
    windows: WindowSettings = WindowSettings()
    filters: FilterSettings = FilterSettings()
    scoring: ScoringSettings = ScoringSettings()
    cleanup: CleanupSettings = CleanupSettings()
    outputs: OutputSettings = OutputSettings()
    ui: UiSettings = UiSettings()


class Secrets(_Model):
    apify_token: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    notion_token: str | None = None


def _load_model[T: _Model](path: Path, model: type[T], label: str) -> T:
    if not path.exists():
        return model()
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError) as exc:
        # Never include the exception text: it can echo file content (secrets).
        raise ConfigError(
            f"Your {label} file could not be read: {path}. Fix it or delete it to start from defaults."
        ) from None


def _write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write to a temporary file next to `path`, then swap it in: a crash never leaves half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_settings() -> Settings:
    return _load_model(settings_path(), Settings, "settings")


def save_settings(s: Settings) -> None:
    _write_atomic(settings_path(), s.model_dump_json(indent=2), mode=0o600)  # the webhook URL can hold a secret


def load_secrets() -> Secrets:
    return _load_model(secrets_path(), Secrets, "secrets")


def save_secrets(s: Secrets) -> None:
    _write_atomic(secrets_path(), s.model_dump_json(indent=2), mode=0o600)


def load_profile() -> str:
    path = profile_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_profile(text: str) -> None:
    _write_atomic(profile_path(), text)
