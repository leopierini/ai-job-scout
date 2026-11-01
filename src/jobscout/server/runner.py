"""Runs and re-scores in background threads, so a run outlives the browser tab that started it."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from jobscout.errors import ConfigError
from jobscout.pipeline import Pipeline, build_app_pipeline
from jobscout.server.workspace import APP_TRIGGER, Workspace

log = logging.getLogger("jobscout.server")

PipelineFactory = Callable[..., Pipeline]  # same arguments as build_pipeline
EMPTY_PROFILE = "Your profile is empty. Write it first, then try again."
CLI_RUN = "This run was started from the command line. Stop it there with Ctrl+C."
STOP_WAIT_S = 30.0  # how long shutdown waits for a cancelled run to wind down


@dataclass
class _Active:
    cancel: threading.Event
    thread: threading.Thread


class RunManager:
    """Starts, cancels and tracks the runs this server process owns. Runs started elsewhere (the command
    line) are visible through the database but can't be cancelled from here."""

    def __init__(self, workspace: Workspace, pipeline_factory: PipelineFactory = build_app_pipeline) -> None:
        self._workspace = workspace
        self._factory = pipeline_factory
        self._owned: dict[int, _Active] = {}
        self._lock = threading.Lock()

    def start(self, kind: Literal["run", "rescore"], window_days: int | None) -> int:
        ws = self._workspace
        sample = ws.mode == "sample"
        settings, secrets, profile = ws.settings(), ws.secrets(), ws.profile()  # one snapshot for the whole run
        if not sample and not profile.strip():
            raise ConfigError(EMPTY_PROFILE)
        db = ws.db()  # the pipeline thread keeps this instance, whatever the workspace does later
        cancel = threading.Event()
        pipeline = self._factory(
            settings, secrets, db, profile, sample=sample, for_rescore=kind == "rescore", cancel=cancel
        )
        try:
            run_id = db.start_run(APP_TRIGGER if kind == "run" else f"{APP_TRIGGER}:rescore", datetime.now())  # RunInProgress -> 409
        except BaseException:
            pipeline.close()
            raise
        thread = threading.Thread(
            target=self._work, args=(run_id, kind, window_days, pipeline), name=f"run-{run_id}", daemon=True
        )
        with self._lock:
            self._owned[run_id] = _Active(cancel, thread)
        thread.start()
        return run_id

    def cancel(self, run_id: int) -> bool:
        """Ask a run of ours to stop; False when it has already ended. An app run no thread of ours runs is
        dead (its app was closed mid-run) and is marked interrupted; a command-line run is a ConfigError."""
        with self._lock:
            owned = self._owned.get(run_id)
        if owned is not None:
            owned.cancel.set()
            return True
        db = self._workspace.db()
        if db.active_run(datetime.now()) != run_id:
            return False
        run = db.get_run(run_id)
        if run is not None and run["trigger"].startswith(APP_TRIGGER):
            return db.interrupt_run(run_id)
        raise ConfigError(CLI_RUN)

    def active(self) -> int | None:
        return self._workspace.db().active_run(datetime.now())

    def wait(self, timeout: float) -> None:
        """For tests: block until the runs in progress have ended, or `timeout` seconds have passed."""
        self._join(timeout)

    def close(self) -> None:
        """Cancel every run of ours and wait for it to wind down, so the database can be closed safely."""
        with self._lock:
            for owned in self._owned.values():
                owned.cancel.set()
        for run_id in self._join(STOP_WAIT_S):
            log.warning("Run %s didn't stop in time; it will show as interrupted.", run_id)

    def _join(self, timeout: float) -> list[int]:
        """Wait up to `timeout` seconds in all; returns the ids of the runs still going."""
        with self._lock:
            owned = list(self._owned.items())
        deadline = time.monotonic() + timeout
        for _run_id, active in owned:
            active.thread.join(max(0.0, deadline - time.monotonic()))
        return [run_id for run_id, active in owned if active.thread.is_alive()]

    def _work(self, run_id: int, kind: str, window_days: int | None, pipeline: Pipeline) -> None:
        try:
            with pipeline:
                if kind == "run":
                    pipeline.run(APP_TRIGGER, window_days, run_id)
                else:
                    pipeline.rescore(f"{APP_TRIGGER}:rescore", run_id)
        except BaseException:  # the pipeline has already recorded the failure on the run
            log.exception("Run %s stopped with an error", run_id)
        finally:
            with self._lock:
                self._owned.pop(run_id, None)
