"""What the server is looking at: the user's real data, or the built-in sample data."""

import threading
from pathlib import Path
from typing import Literal

from jobscout import paths
from jobscout.db import Database
from jobscout.errors import ConfigError
from jobscout.settings import (
    Secrets,
    Settings,
    load_profile,
    load_secrets,
    load_settings,
    save_profile,
    save_secrets,
    save_settings,
)
from jobscout.sources.sample import SAMPLE_PROFILE, sample_settings

Mode = Literal["real", "sample"]
APP_TRIGGER = "app"  # every run the app starts has a trigger beginning with this ("app", "app:rescore")
SAMPLE_READ_ONLY = "You're exploring sample data. Set up your own search to change settings."


class Workspace:
    """The data behind the API. Settings, secrets and profile are read from disk on every call, so edits
    made outside the app show up. In sample mode nothing is read from or written to the user's files."""

    def __init__(self, sample: bool = False) -> None:
        self.mode: Mode = "sample" if sample else "real"
        self._db: Database | None = None
        self._opened: set[Path] = set()  # database files this app has opened at least once
        self._lock = threading.Lock()

    @property
    def db_file(self) -> Path:
        return paths.app_dir() / "sample.db" if self.mode == "sample" else paths.db_path()

    def db(self) -> Database:
        with self._lock:
            if self._db is None:
                path = self.db_file
                self._db = Database(path)
                if path not in self._opened:
                    # An app run still marked running belongs to an app that was closed mid-run (the terminal
                    # was shut, so it never wound down): only one app runs at a time, so it is dead.
                    self._db.interrupt_runs(APP_TRIGGER)
                    self._opened.add(path)
            return self._db

    def settings(self) -> Settings:
        return sample_settings() if self.mode == "sample" else load_settings()

    def secrets(self) -> Secrets:
        return Secrets() if self.mode == "sample" else load_secrets()

    def profile(self) -> str:
        return SAMPLE_PROFILE if self.mode == "sample" else load_profile()

    def save_settings(self, settings: Settings) -> None:
        self._require_real()
        save_settings(settings)

    def save_secrets(self, secrets: Secrets) -> None:
        self._require_real()
        save_secrets(secrets)

    def save_profile(self, text: str) -> None:
        self._require_real()
        save_profile(text)

    def set_mode(self, mode: Mode) -> None:
        self.close()
        self.mode = mode

    def close(self) -> None:
        with self._lock:
            db, self._db = self._db, None
        if db is not None:
            db.close()

    def _require_real(self) -> None:
        if self.mode == "sample":
            raise ConfigError(SAMPLE_READ_ONLY)
