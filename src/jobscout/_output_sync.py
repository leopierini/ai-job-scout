"""Pipeline helpers that keep the outputs (Notion, webhook, CSV) in step with the database.

Private to the engine (the pipeline and the CLI). Output errors are recorded per job and never raised.
"""

from collections.abc import Callable
from pathlib import Path

from jobscout.db import Database
from jobscout.errors import NotionError, WebhookError
from jobscout.models import ACTIVE_STATUSES, JobRow, JobState, ScoreResult, Status
from jobscout.outputs.csv_out import export_csv
from jobscout.outputs.notion import NotionClient
from jobscout.outputs.webhook import WebhookOutput
from jobscout.paths import app_dir
from jobscout.settings import CsvOutputSettings

NOTION, WEBHOOK = "notion", "webhook"


def _status(value: str | None) -> Status | None:
    return Status(value) if value else None


def notion_status_changes(db: Database, notion: NotionClient, database_id: str) -> dict[str, Status]:
    """A read only: pk -> the status Notion shows, for every delivered page whose status differs from the
    one we last pushed. Raises NotionError if the database can't be read."""
    statuses = notion.read_statuses(database_id)
    changes: dict[str, Status] = {}
    for d in db.deliveries(NOTION):
        page_id = d["external_id"]
        if not page_id or page_id not in statuses:
            continue
        notion_status = statuses[page_id][0]
        if d["pushed_status"] != notion_status.value:
            changes[d["pk"]] = notion_status
    return changes


def read_back_notion_statuses(db: Database, notion: NotionClient, database_id: str) -> int:
    """A Notion status that differs from the one we last pushed was changed there: Notion wins.

    Returns how many local statuses changed. Raises NotionError if the database can't be read.
    """
    changes = 0
    for pk, notion_status in notion_status_changes(db, notion, database_id).items():
        row, d = db.get(pk), db.get_delivery(pk, NOTION)
        if row is None or d is None:
            continue
        if row.status != notion_status:
            db.set_status(row.pk, notion_status)
            changes += 1
            if notion_status != Status.TO_REVIEW and row.state == JobState.BELOW_THRESHOLD:
                # Acted on: never below the threshold (a run with Notion unreadable may have demoted it).
                db.set_state(row.pk, JobState.KEPT)
        if notion_status in ACTIVE_STATUSES and row.archived_reason is not None:
            db.restore(row.pk)  # applied to in Notion after a local-only cleanup: never archived
        # Only a delivered page stays delivered: a pending or failed one still owes Notion a push
        # (a re-score's new score), so it keeps its state and only learns what Notion shows now.
        state = d["state"] if d["state"] in ("pending", "failed") else "delivered"
        db.mark_delivery(
            row.pk, NOTION, state, external_id=d["external_id"], error=d["error"] if state == "failed" else None,
            pushed_status=notion_status,
        )  # fmt: skip
    return changes


def archive_orphaned_pages(
    db: Database, notion: NotionClient, include_below: bool, beat: Callable[[], None] = lambda: None
) -> int:
    """Archive the Notion page of every job that should no longer have one (archived here, by any
    command, or dropped from the outputs), retrying earlier failures. `beat` is called before each
    Notion call. Returns how many failed."""
    return sum(not _archive_page(db, notion, pk, beat) for pk in db.orphaned_pages(NOTION, include_below))


def _archive_page(db: Database, notion: NotionClient, pk: str, beat: Callable[[], None]) -> bool:
    """Archive the job's Notion page if it has one; False (and the delivery marked failed) if that failed."""
    d = db.get_delivery(pk, NOTION)
    if not d or not d["external_id"]:
        return True
    beat()
    try:
        notion.archive_page(d["external_id"])
    except NotionError as exc:
        db.mark_delivery(
            pk, NOTION, "failed", external_id=d["external_id"],
            error=f"Couldn't archive the Notion page. {exc}", pushed_status=_status(d["pushed_status"]),
        )  # fmt: skip
        return False
    # The page is gone: if the job comes back (restored, or above the threshold again) it gets a new one.
    db.mark_delivery(pk, NOTION, "pending")
    return True


def auto_export_csv(db: Database, cfg: CsvOutputSettings) -> str | None:
    """Write the tracked jobs to the CSV when auto-export is on; returns a note if it couldn't be saved."""
    if not (cfg.enabled and cfg.auto_export):
        return None
    path = Path(cfg.path).expanduser() if cfg.path.strip() else app_dir() / "jobs.csv"
    try:
        export_csv(db.tracked(), path)
    except OSError as exc:
        return f"CSV not saved ({exc.strerror or 'the file could not be written'})"
    return None


def deliver_webhook(db: Database, webhook: WebhookOutput, row: JobRow) -> bool:
    previous = db.get_delivery(row.pk, WEBHOOK) or {}
    try:
        webhook.deliver(row)
    except WebhookError as exc:
        _failed(db, row, WEBHOOK, previous, None, str(exc))
        return False
    db.mark_delivery(row.pk, WEBHOOK, "delivered", pushed_status=row.status)
    return True


def deliver_notion(db: Database, notion: NotionClient, database_id: str, row: JobRow) -> bool:
    """Create the job's page; push only the status to a delivered page; push the whole score otherwise
    (a re-scored job is marked pending, and a failed earlier push may have been a re-score too)."""
    previous = db.get_delivery(row.pk, NOTION) or {}
    page_id = previous.get("external_id")
    try:
        if not page_id:
            page_id = notion.create_page(database_id, row)
        elif previous.get("state") == "delivered":
            notion.update_status(page_id, row.status)
        else:
            notion.update_page(page_id, row)
    except NotionError as exc:
        _failed(db, row, NOTION, previous, page_id, str(exc))
        return False
    db.mark_delivery(row.pk, NOTION, "delivered", external_id=page_id, pushed_status=row.status)
    return True


def mark_rescored(
    db: Database,
    notion: NotionClient | None,
    results: list[ScoreResult],
    min_score: int,
    include_below: bool,
    beat: Callable[[], None] = lambda: None,
) -> int:
    """After a re-score: jobs still sent to outputs are queued again (the webhook payload keeps the same id,
    so automations can upsert); a job that dropped out loses its Notion page (`beat` is called before each
    Notion call). Returns pages not archived."""
    failed = 0
    for r in results:
        if r.score >= min_score or include_below:
            for output in (WEBHOOK, NOTION):
                d = db.get_delivery(r.pk, output)
                if d is not None:
                    db.mark_delivery(
                        r.pk, output, "pending", external_id=d["external_id"],
                        pushed_status=_status(d["pushed_status"]),
                    )  # fmt: skip
            continue
        if notion is not None and not _archive_page(db, notion, r.pk, beat):
            failed += 1  # retried by the next run's cleanup
    return failed


def _failed(db: Database, row: JobRow, output: str, previous: dict, external_id: str | None, error: str) -> None:
    # Keep the page id and what the output last got from us, so nothing is created twice
    # and the Notion status read-back still compares against what Notion really shows.
    db.mark_delivery(
        row.pk, output, "failed", external_id=external_id, error=error,
        pushed_status=_status(previous.get("pushed_status")),
    )  # fmt: skip
