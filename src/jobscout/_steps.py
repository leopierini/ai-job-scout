"""Running a run's steps in order and reporting each one as StepEvents. Private to jobscout.pipeline."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from jobscout.db import Database
from jobscout.models import StepEvent, StepName, StepStatus

STEPS: tuple[StepName, ...] = ("cleanup", "search", "filter", "score", "save")
CANCELLED = "Cancelled"
AFTER_ERROR = "Skipped because an earlier step failed"
UNEXPECTED = "The run stopped unexpectedly."
NOT_IN_RESCORE = "Not part of a re-score"


def ignore(_event: StepEvent) -> None:
    pass


class Cancelled(Exception):
    """Raised inside a step when the user cancels; its message reports the progress made."""


@dataclass
class StepResult:
    message: str
    status: StepStatus = "done"
    done: int | None = None
    total: int | None = None
    data: dict = field(default_factory=dict)
    error: str | None = None  # for the run summary when it differs from the message


class StepRunner:
    """Runs the steps of one run in order; every event is stored in the run's log and passed to `on_event`."""

    def __init__(self, db: Database, on_event: Callable[[StepEvent], None], cancel: threading.Event | None) -> None:
        self._db = db
        self._on_event = on_event
        self._cancel = cancel if cancel is not None else threading.Event()
        self._run_id = 0
        self._step: StepName = "cleanup"
        self._step_started = 0.0
        self._closed: set[StepName] = set()  # steps that already have their final event

    def _run_steps(self, steps: dict[str, Callable[[], StepResult]]) -> tuple[str, str | None]:
        status, error = "done", None
        stopped: str | None = None  # why the remaining steps will not run
        for name in STEPS:
            self._step, self._step_started = name, time.monotonic()
            fn = steps.get(name)
            if stopped is None and self._cancel.is_set():
                status, stopped = "cancelled", CANCELLED
            if stopped is not None or fn is None:
                self._emit("skipped", stopped or NOT_IN_RESCORE, elapsed=False)
                continue
            self._emit("running", "Starting")
            try:
                result = fn()
            except Cancelled as exc:
                status, stopped = "cancelled", CANCELLED
                self._emit("skipped", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - any failure ends the run with a readable step error
                message = str(exc) or type(exc).__name__
                status, error, stopped = "error", message, AFTER_ERROR
                self._emit("error", message)
                continue
            if result.status == "error":
                status, error = "error", error or result.error or result.message
            self._emit(result.status, result.message, result.done, result.total, result.data)
        return status, error

    def _emit(
        self,
        status: StepStatus,
        message: str,
        done: int | None = None,
        total: int | None = None,
        data: dict | None = None,
        *,
        elapsed: bool = True,
    ) -> None:
        seconds = round(time.monotonic() - self._step_started, 3) if elapsed else 0.0
        ev = StepEvent(self._step, status, message, done, total, seconds, data or {})
        if status != "running":
            self._closed.add(self._step)
        self._db.add_event(self._run_id, ev)
        try:
            self._on_event(ev)
        except Exception:  # noqa: BLE001 - a broken listener must never stop a step (paid results could be lost)
            pass

    def _close_open_steps(self) -> None:
        """After an exception escaped a step: the step that was running fails, the rest are skipped."""
        running = self._step
        try:
            for name in STEPS:
                if name in self._closed:
                    continue
                self._step = name
                if name == running:
                    self._emit("error", UNEXPECTED)
                else:
                    self._emit("skipped", AFTER_ERROR, elapsed=False)
        except Exception:  # noqa: BLE001 - never hide the original exception
            pass

    def _check_cancel(self, progress: str) -> None:
        if self._cancel.is_set():
            raise Cancelled(f"{CANCELLED} · {progress}")
