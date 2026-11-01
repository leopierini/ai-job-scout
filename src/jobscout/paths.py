"""Locations of the user's data files. Nothing is ever written inside the repo."""

import os
from pathlib import Path

import platformdirs

APP_NAME = "ai-job-scout"


def app_dir() -> Path:
    """The app-data folder (JOBSCOUT_HOME overrides the OS default). Created if missing."""
    override = os.environ.get("JOBSCOUT_HOME")
    path = Path(override) if override else Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_dir() / "settings.json"


def secrets_path() -> Path:
    return app_dir() / "secrets.json"


def profile_path() -> Path:
    return app_dir() / "profile.md"


def db_path() -> Path:
    return app_dir() / "jobscout.db"
