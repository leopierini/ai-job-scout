"""SQLite storage. This is the only module that touches SQL."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal

from jobscout.errors import RunInProgress
from jobscout.models import (
    ACTIVE_STATUSES,
    Job,
    JobRow,
    JobState,
    RunSummary,
    ScoreResult,
    Status,
    StepEvent,
)

SCHEMA_VERSION = 2
STALE_RUN_AFTER = timedelta(minutes=20)  # a running run with no sign of life for this long has crashed
INTERRUPTED = "Interrupted"  # the error of a run whose process died before it finished

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    pk TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL,
    posted TEXT NOT NULL,
    keyword TEXT NOT NULL,
    area TEXT NOT NULL,
    recruiter TEXT NOT NULL DEFAULT '',
    recruiter_title TEXT NOT NULL DEFAULT '',
    recruiter_url TEXT NOT NULL DEFAULT '',
    employment_type TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    status TEXT NOT NULL,
    score INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    flags TEXT NOT NULL DEFAULT '[]',
    start_date TEXT NOT NULL DEFAULT '',
    deadline TEXT NOT NULL DEFAULT '',
    archived_reason TEXT,
    restored INTEGER NOT NULL DEFAULT 0,
    dedup_key TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    status_updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_dedup_key ON jobs(dedup_key);
CREATE TABLE IF NOT EXISTS deliveries (
    pk TEXT NOT NULL,
    output TEXT NOT NULL,
    state TEXT NOT NULL,
    external_id TEXT,
    error TEXT,
    pushed_status TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pk, output)
);
CREATE TABLE IF NOT EXISTS windows (
    area TEXT PRIMARY KEY,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT,
    heartbeat_at TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

# Columns added after version 1, with their definitions: older databases gain them on open.
_ADDED_COLUMNS = {
    "jobs": {"restored": "INTEGER NOT NULL DEFAULT 0"},
    "runs": {"heartbeat_at": "TEXT"},
}

_JOB_COLUMNS = (
    "source", "job_id", "title", "company", "location", "url", "description", "posted",
    "keyword", "area", "recruiter", "recruiter_title", "recruiter_url", "employment_type",
)  # fmt: skip


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _marks(values: list) -> str:
    return ", ".join("?" for _ in values)


def _alive_run_id(c: sqlite3.Connection, now: datetime) -> int | None:
    """The id of a running run whose heartbeat (or start) is under STALE_RUN_AFTER old, else None."""
    for r in c.execute("SELECT id, started_at, heartbeat_at FROM runs WHERE status = 'running' ORDER BY id DESC"):
        if now - datetime.fromisoformat(r["heartbeat_at"] or r["started_at"]) < STALE_RUN_AFTER:
            return r["id"]
    return None


def _job_row(r: sqlite3.Row) -> JobRow:
    return JobRow(
        **{c: r[c] for c in _JOB_COLUMNS},
        state=JobState(r["state"]),
        status=Status(r["status"]),
        score=r["score"],
        reason=r["reason"],
        flags=json.loads(r["flags"]),
        start_date=r["start_date"],
        deadline=r["deadline"],
        archived_reason=r["archived_reason"],
        restored=bool(r["restored"]),
        dedup_key=r["dedup_key"],
        first_seen_at=r["first_seen_at"],
        status_updated_at=r["status_updated_at"],
    )


def _run_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["counts"] = json.loads(d.pop("counts_json"))
    return d


class Database:
    """Thread-safe: one lock serialises every public method."""

    def __init__(self, path: Path) -> None:
        # isolation_level=None: autocommit; transactions are explicit (see _tx).
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._add_missing_columns()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _add_missing_columns(self) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Lock, then run a write transaction (IMMEDIATE, so other processes wait their turn)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- jobs -------------------------------------------------------------

    def insert_scraped(self, jobs: list[Job], dedup_keys: dict[str, str]) -> list[str]:
        """Save new jobs and return the pks really inserted (a job already stored is left as it is).

        Every job needs its dedup key (KeyError). All or nothing: a row the table rejects (e.g. a
        missing title) raises and nothing is saved.
        """
        now = _now()
        columns = ", ".join(_JOB_COLUMNS)
        marks = ", ".join("?" for _ in _JOB_COLUMNS)
        inserted: list[str] = []
        with self._tx() as c:
            for job in jobs:
                cur = c.execute(
                    f"INSERT INTO jobs (pk, {columns}, state, status, dedup_key,"
                    f" first_seen_at, status_updated_at) VALUES (?, {marks}, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(pk) DO NOTHING",
                    (
                        job.pk,
                        *(getattr(job, col) for col in _JOB_COLUMNS),
                        JobState.SCRAPED.value,
                        Status.TO_REVIEW.value,
                        dedup_keys[job.pk],
                        now,
                        now,
                    ),
                )
                if cur.rowcount:
                    inserted.append(job.pk)
        return inserted

    def get(self, pk: str) -> JobRow | None:
        rows = self._query("SELECT * FROM jobs WHERE pk = ?", (pk,))
        return _job_row(rows[0]) if rows else None

    def jobs_in_state(self, state: JobState) -> list[JobRow]:
        # Oldest first; rowid breaks ties in insertion order (first_seen_at has one-second resolution).
        rows = self._query("SELECT * FROM jobs WHERE state = ? ORDER BY first_seen_at, rowid", (state.value,))
        return [_job_row(r) for r in rows]

    def find_by_dedup_key(self, key: str) -> list[str]:
        return [r["pk"] for r in self._query("SELECT pk FROM jobs WHERE dedup_key = ? ORDER BY pk", (key,))]

    def _update_job(self, pk: str, sql: str, params: tuple) -> None:
        """Run an UPDATE on one job (`sql` ends with `WHERE pk = ?`); KeyError if there is no such job."""
        with self._tx() as c:
            if c.execute(sql, (*params, pk)).rowcount == 0:
                raise KeyError(pk)

    def apply_threshold(self, min_score: int) -> int:
        """Re-label stored scores against `min_score` (no AI): below ones at or above it become kept;
        kept ones under it go below, unless the user acted on them. Returns how many changed."""
        with self._tx() as c:
            up = c.execute(
                "UPDATE jobs SET state = ? WHERE state = ? AND score >= ?",
                (JobState.KEPT.value, JobState.BELOW_THRESHOLD.value, min_score),
            ).rowcount
            down = c.execute(
                "UPDATE jobs SET state = ? WHERE state = ? AND score < ? AND status = ?",
                (JobState.BELOW_THRESHOLD.value, JobState.KEPT.value, min_score, Status.TO_REVIEW.value),
            ).rowcount
        return up + down

    def set_state(self, pk: str, state: JobState) -> None:
        self._update_job(pk, "UPDATE jobs SET state = ? WHERE pk = ?", (state.value,))

    def save_score(self, r: ScoreResult, min_score: int) -> None:
        state = JobState.KEPT if r.score >= min_score else JobState.BELOW_THRESHOLD
        self._update_job(
            r.pk,
            "UPDATE jobs SET score = ?, reason = ?, flags = ?, start_date = ?, deadline = ?, state = ? WHERE pk = ?",
            (r.score, r.reason, json.dumps(r.flags), r.start_date, r.deadline, state.value),
        )

    def set_status(self, pk: str, status: Status) -> None:
        """A real status change also ends a restore's exemption from cleanup."""
        self._update_job(
            pk,
            "UPDATE jobs SET restored = CASE WHEN status = ? THEN restored ELSE 0 END,"
            " status = ?, status_updated_at = ? WHERE pk = ?",
            (status.value, status.value, _now()),
        )

    def archive(self, pk: str, reason: str) -> bool:
        """Archive a job unless it is already archived or was applied to (checked in the same write,
        so a status changed meanwhile wins). Returns whether it was archived; KeyError if unknown."""
        active = [s.value for s in ACTIVE_STATUSES]
        with self._tx() as c:
            if c.execute("SELECT 1 FROM jobs WHERE pk = ?", (pk,)).fetchone() is None:
                raise KeyError(pk)
            cur = c.execute(
                "UPDATE jobs SET archived_reason = ? WHERE pk = ? AND archived_reason IS NULL"
                f" AND status NOT IN ({_marks(active)})",
                (reason, pk, *active),
            )
            return cur.rowcount == 1

    def restore(self, pk: str) -> None:
        """Bring an archived job back; cleanup leaves its dates alone until its status changes."""
        self._update_job(pk, "UPDATE jobs SET archived_reason = NULL, restored = 1 WHERE pk = ?", ())

    def tracked(self, include_archived: bool = False) -> list[JobRow]:
        sql = "SELECT * FROM jobs WHERE state = ?"
        if not include_archived:
            sql += " AND archived_reason IS NULL"
        rows = self._query(sql + " ORDER BY score DESC, first_seen_at DESC, pk", (JobState.KEPT.value,))
        return [_job_row(r) for r in rows]

    def keep(self, pk: str) -> None:
        """Keep a job anyway: kept and Interested (a real status change), so a later threshold leaves it."""
        self._update_job(
            pk,
            "UPDATE jobs SET state = ?, restored = CASE WHEN status = ? THEN restored ELSE 0 END,"
            " status = ?, status_updated_at = ? WHERE pk = ?",
            (JobState.KEPT.value, Status.INTERESTED.value, Status.INTERESTED.value, _now()),
        )

    def view_counts(self) -> dict[str, int]:
        """Jobs per view: one key per Status (kept, not archived), plus "archived" and "below"."""
        counts = {s.value: 0 for s in Status} | {"archived": 0, "below": 0}
        rows = self._query(
            "SELECT status, state, archived_reason IS NOT NULL AS archived, COUNT(*) AS n FROM jobs"
            " GROUP BY status, state, archived"
        )
        for r in rows:
            if r["archived"]:
                counts["archived"] += r["n"]
            elif r["state"] == JobState.KEPT.value:
                counts[r["status"]] += r["n"]
            elif r["state"] == JobState.BELOW_THRESHOLD.value:
                counts["below"] += r["n"]
        return counts

    def jobs_view(self, view: str) -> list[JobRow]:
        """The jobs of one view: a Status value, "archived" or "below" (ValueError for any other)."""
        if view == "archived":
            where, params = "archived_reason IS NOT NULL", ()
        elif view == "below":
            where, params = "archived_reason IS NULL AND state = ?", (JobState.BELOW_THRESHOLD.value,)
        elif view in {s.value for s in Status}:
            where, params = "archived_reason IS NULL AND state = ? AND status = ?", (JobState.KEPT.value, view)
        else:
            raise ValueError(f"Unknown view: {view}")
        rows = self._query(f"SELECT * FROM jobs WHERE {where} ORDER BY score DESC, first_seen_at DESC, pk", params)
        return [_job_row(r) for r in rows]

    def rescore_candidates(self) -> list[str]:
        """Scored jobs still To review (kept or below the threshold), not archived; acted-on jobs never are."""
        rows = self._query(
            "SELECT pk FROM jobs WHERE state IN (?, ?) AND archived_reason IS NULL AND status = ? ORDER BY pk",
            (JobState.KEPT.value, JobState.BELOW_THRESHOLD.value, Status.TO_REVIEW.value),
        )
        return [r["pk"] for r in rows]

    # --- deliveries -------------------------------------------------------

    def get_delivery(self, pk: str, output: str) -> dict | None:
        rows = self._query("SELECT * FROM deliveries WHERE pk = ? AND output = ?", (pk, output))
        return dict(rows[0]) if rows else None

    def mark_delivery(
        self,
        pk: str,
        output: str,
        state: Literal["pending", "delivered", "failed"],
        external_id: str | None = None,
        error: str | None = None,
        pushed_status: Status | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO deliveries"
                " (pk, output, state, external_id, error, pushed_status, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pk, output, state, external_id, error, pushed_status.value if pushed_status else None, _now()),
            )

    def clear_deliveries(self, output: str) -> int:
        """Forget every delivery to `output` (it now points somewhere new). Returns how many."""
        with self._tx() as c:
            return c.execute("DELETE FROM deliveries WHERE output = ?", (output,)).rowcount

    def orphaned_pages(self, output: str, include_below: bool) -> list[str]:
        """Jobs whose page in `output` should no longer be there: archived, or dropped from the outputs
        (below the threshold or a duplicate) while still To review. Never a job the user applied to."""
        states = [JobState.KEPT.value] + ([JobState.BELOW_THRESHOLD.value] if include_below else [])
        active = [s.value for s in ACTIVE_STATUSES]
        rows = self._query(
            "SELECT d.pk FROM deliveries d JOIN jobs j ON j.pk = d.pk"
            f" WHERE d.output = ? AND d.external_id IS NOT NULL AND j.status NOT IN ({_marks(active)})"
            f" AND (j.archived_reason IS NOT NULL OR (j.status = ? AND j.state NOT IN ({_marks(states)})))"
            " ORDER BY d.pk",
            (output, *active, Status.TO_REVIEW.value, *states),
        )
        return [r["pk"] for r in rows]

    def delivered_pks(self) -> set[str]:
        """Jobs some output already has (delivered, or with a page there)."""
        rows = self._query("SELECT DISTINCT pk FROM deliveries WHERE state = 'delivered' OR external_id IS NOT NULL")
        return {r["pk"] for r in rows}

    def deliveries(self, output: str) -> list[dict]:
        rows = self._query("SELECT * FROM deliveries WHERE output = ? ORDER BY pk", (output,))
        return [dict(r) for r in rows]

    def deliveries_needed(self, output: str, include_below: bool, sync_status: bool = True) -> list[JobRow]:
        """Jobs to send: never delivered, failed or pending, and (with `sync_status`) whose status changed since."""
        states = [JobState.KEPT.value] + ([JobState.BELOW_THRESHOLD.value] if include_below else [])
        marks = ", ".join("?" for _ in states)
        status_changed = " OR d.pushed_status IS NULL OR d.pushed_status != j.status" if sync_status else ""
        rows = self._query(
            "SELECT j.* FROM jobs j LEFT JOIN deliveries d ON d.pk = j.pk AND d.output = ?"
            f" WHERE j.state IN ({marks}) AND j.archived_reason IS NULL"
            f" AND (d.pk IS NULL OR d.state != 'delivered'{status_changed})"
            " ORDER BY j.score DESC, j.first_seen_at, j.pk",
            (output, *states),
        )
        return [_job_row(r) for r in rows]

    # --- windows ----------------------------------------------------------

    def window_start(self, area: str) -> datetime | None:
        rows = self._query("SELECT started_at FROM windows WHERE area = ?", (area,))
        return datetime.fromisoformat(rows[0]["started_at"]) if rows else None

    def commit_window(self, area: str, started_at: datetime) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO windows (area, started_at) VALUES (?, ?)",
                (area, started_at.isoformat(timespec="seconds")),
            )

    # --- runs -------------------------------------------------------------

    def active_run(self, now: datetime) -> int | None:
        """The id of a run that is running and alive (heartbeat or start under 20 minutes old), else None."""
        with self._lock:
            return _alive_run_id(self._conn, now)

    def get_run(self, run_id: int) -> dict | None:
        rows = self._query("SELECT * FROM runs WHERE id = ?", (run_id,))
        return _run_dict(rows[0]) if rows else None

    def start_run(self, trigger: str, now: datetime) -> int:
        """Start a run, or raise RunInProgress. A running run with no heartbeat for 20 minutes crashed."""
        with self._tx() as c:
            if _alive_run_id(c, now) is not None:
                raise RunInProgress("Another run is already in progress.")
            # Whatever is still running is stale: it crashed.
            c.execute(
                "UPDATE runs SET status = 'error', finished_at = ?, error = ? WHERE status = 'running'",
                (now.isoformat(timespec="seconds"), INTERRUPTED),
            )
            started = now.isoformat(timespec="seconds")
            cur = c.execute(
                "INSERT INTO runs (trigger, started_at, status, heartbeat_at) VALUES (?, ?, 'running', ?)",
                (trigger, started, started),
            )
            return cur.lastrowid

    def interrupt_runs(self, trigger_prefix: str) -> int:
        """End every run still marked running whose trigger starts with `trigger_prefix` as interrupted (its
        process died without finishing it). Returns how many."""
        with self._tx() as c:
            return c.execute(
                "UPDATE runs SET status = 'error', finished_at = ?, error = ?"
                " WHERE status = 'running' AND substr(trigger, 1, ?) = ?",
                (_now(), INTERRUPTED, len(trigger_prefix), trigger_prefix),
            ).rowcount

    def interrupt_run(self, run_id: int) -> bool:
        """End one run still marked running as interrupted; False when it isn't running."""
        with self._tx() as c:
            return (
                c.execute(
                    "UPDATE runs SET status = 'error', finished_at = ?, error = ? WHERE id = ? AND status = 'running'",
                    (_now(), INTERRUPTED, run_id),
                ).rowcount
                == 1
            )

    def heartbeat(self, run_id: int) -> None:
        """The run is still alive (every event also counts)."""
        with self._tx() as c:
            c.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (_now(), run_id))

    def finish_run(self, run_id: int, summary: RunSummary) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE runs SET status = ?, finished_at = ?, counts_json = ?, cost_usd = ?, error = ?"
                " WHERE id = ?",
                (summary.status, _now(), json.dumps(summary.counts), summary.cost_usd, summary.error, run_id),
            )

    def add_event(self, run_id: int, ev: StepEvent) -> None:
        with self._tx() as c:
            seq = c.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO run_events (run_id, seq, json) VALUES (?, ?, ?)",
                (run_id, seq, json.dumps(asdict(ev))),
            )
            c.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (_now(), run_id))

    def events(self, run_id: int) -> list[StepEvent]:
        rows = self._query("SELECT json FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,))
        return [StepEvent(**json.loads(r["json"])) for r in rows]

    def events_after(self, run_id: int, after_seq: int) -> list[tuple[int, StepEvent]]:
        """Events with a seq above `after_seq`, in order, each with its seq (for live polling)."""
        rows = self._query(
            "SELECT seq, json FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        )
        return [(r["seq"], StepEvent(**json.loads(r["json"]))) for r in rows]

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [_run_dict(r) for r in rows]

    # --- reset ------------------------------------------------------------

    def reset(self) -> None:
        """Delete every job, delivery, window and run (RunInProgress while a run is alive)."""
        with self._tx() as c:
            if _alive_run_id(c, datetime.now()) is not None:
                raise RunInProgress("A run is in progress. Wait for it to finish, then try again.")
            for table in ("jobs", "deliveries", "windows", "run_events", "runs"):
                c.execute(f"DELETE FROM {table}")
