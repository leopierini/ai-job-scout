"""Job lists, status changes, CSV export and reset."""

import uuid
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from jobscout import paths
from jobscout.models import JobRow, Status
from jobscout.outputs.csv_out import export_csv
from jobscout.server.workspace import Workspace

router = APIRouter(prefix="/api")

GONE = "That job no longer exists."
CONFIRM_WORD = "delete"
HIDDEN_FIELDS = {"description", "dedup_key"}


class StatusBody(BaseModel):
    status: Status


class ResetBody(BaseModel):
    confirm: str


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def _job_out(row: JobRow) -> dict:
    out = {"pk": row.pk} | {k: v for k, v in asdict(row).items() if k not in HIDDEN_FIELDS}
    out["state"], out["status"] = row.state.value, row.status.value
    return out


def _changed(request: Request, pk: str, change: str, *args) -> dict:
    """Apply a db change to one job and return the job as it is now; 404 when it is gone."""
    db = _workspace(request).db()
    try:
        getattr(db, change)(pk, *args)
    except KeyError:
        raise HTTPException(404, GONE) from None
    row = db.get(pk)
    if row is None:
        raise HTTPException(404, GONE)
    return _job_out(row)


@router.get("/jobs")
def list_jobs(request: Request, view: str = "to_review") -> list[dict]:
    try:
        rows = _workspace(request).db().jobs_view(view)
    except ValueError:
        raise HTTPException(400, "That list doesn't exist.") from None
    return [_job_out(r) for r in rows]


@router.get("/jobs/counts")
def job_counts(request: Request) -> dict:
    return _workspace(request).db().view_counts()


@router.patch("/jobs/{pk:path}")
def set_job_status(pk: str, body: StatusBody, request: Request) -> dict:
    return _changed(request, pk, "set_status", body.status)


@router.post("/jobs/{pk:path}/restore")
def restore_job(pk: str, request: Request) -> dict:
    return _changed(request, pk, "restore")


@router.post("/jobs/{pk:path}/keep")
def keep_job(pk: str, request: Request) -> dict:
    return _changed(request, pk, "keep")


@router.get("/export.csv")
def export(request: Request) -> Response:
    rows = _workspace(request).db().tracked()
    temp = paths.app_dir() / f"export-{uuid.uuid4().hex}.csv"
    try:
        export_csv(rows, temp)
        content = temp.read_bytes()
    finally:
        temp.unlink(missing_ok=True)
    name = f"ai-job-scout-jobs-{date.today().isoformat()}.csv"
    return Response(content, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/reset")
def reset(body: ResetBody, request: Request) -> dict:
    if body.confirm != CONFIRM_WORD:
        raise HTTPException(400, f'Type "{CONFIRM_WORD}" to confirm. Nothing was deleted.')
    _workspace(request).db().reset()  # RunInProgress while a run is active -> 409
    return {"ok": True}
