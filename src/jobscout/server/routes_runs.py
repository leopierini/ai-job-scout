"""Starting, watching and cancelling runs; cost estimate and re-score preview."""

import hashlib
import math
import threading
import time
from collections.abc import Callable
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jobscout.costs import apify_credit, estimate
from jobscout.models import StepEvent
from jobscout.server.runner import RunManager
from jobscout.server.workspace import Workspace
from jobscout.settings import Settings

router = APIRouter(prefix="/api")

CREDIT_TTL_S = 300.0
CREDIT_TIMEOUT_S = 10.0
CHARS_PER_TOKEN = 4
RUN_FIELDS = ("id", "trigger", "started_at", "finished_at", "status", "counts", "cost_usd", "error")


class StartBody(BaseModel):
    kind: Literal["run", "rescore"]
    window_days: int | None = Field(default=None, ge=1)


class EstimateBody(BaseModel):
    settings: Settings | None = None


class CreditCache:
    """Apify's remaining credit per token for a few minutes. Keyed by a hash, so the token is never kept."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, float]] = {}  # token hash -> (expires at, credit)
        self._lock = threading.Lock()

    def get(self, token: str, fetch: Callable[[str], float | None]) -> float | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            hit = self._entries.get(key)
        if hit is not None and self._clock() < hit[0]:
            return hit[1]
        credit = fetch(token)
        if credit is not None:  # a failure is not remembered: the next estimate tries again
            with self._lock:
                self._entries[key] = (self._clock() + CREDIT_TTL_S, credit)
        return credit


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def _manager(request: Request) -> RunManager:
    return request.app.state.runs


def _fetch_credit(token: str) -> float | None:
    with httpx.Client(timeout=CREDIT_TIMEOUT_S) as client:
        return apify_credit(token, client)


def _run_out(run: dict) -> dict:
    return {field: run[field] for field in RUN_FIELDS}


def _event_out(seq: int, ev: StepEvent) -> dict:
    return {
        "seq": seq, "step": ev.step, "status": ev.status, "message": ev.message, "done": ev.done,
        "total": ev.total, "elapsed_s": ev.elapsed_s, "data": ev.data,
    }  # fmt: skip


def _with_events(ws: Workspace, run: dict, after: int) -> dict:
    events = ws.db().events_after(run["id"], after)
    return {"run": _run_out(run), "events": [_event_out(seq, ev) for seq, ev in events]}


@router.post("/runs")
def start_run(body: StartBody, request: Request) -> dict:
    return {"run_id": _manager(request).start(body.kind, body.window_days)}


@router.get("/runs/active")
def active_run(request: Request) -> dict:
    return {"run_id": _manager(request).active()}


@router.get("/runs")
def list_runs(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    return [_run_out(r) for r in _workspace(request).db().runs(limit)]


@router.get("/runs/latest")
def latest_run(request: Request, after: int = Query(0, ge=0)) -> dict | None:
    ws = _workspace(request)
    runs = ws.db().runs(1)
    return _with_events(ws, runs[0], after) if runs else None


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request, after: int = Query(0, ge=0)) -> dict:
    ws = _workspace(request)
    run = ws.db().get_run(run_id)
    if run is None:
        raise HTTPException(404, "That run no longer exists.")
    return _with_events(ws, run, after)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, request: Request) -> dict:
    if _workspace(request).db().get_run(run_id) is None:
        raise HTTPException(404, "That run no longer exists.")
    _manager(request).cancel(run_id)  # already ended: nothing to stop, and that is fine
    return {"ok": True}


@router.post("/estimate")
def post_estimate(body: EstimateBody, request: Request) -> dict:
    ws = _workspace(request)
    est = estimate(body.settings or ws.settings())
    token = ws.secrets().apify_token  # always the saved token: never taken from the request
    credit = request.app.state.credit.get(token, _fetch_credit) if token and ws.mode == "real" else None
    return {
        "searches": est.searches,
        "max_results": est.max_results,
        "max_usd": est.max_usd,
        "credit_usd": credit,
    }


@router.get("/rescore/preview")
def rescore_preview(request: Request) -> dict:
    ws = _workspace(request)
    db = ws.db()
    rows = [row for pk in db.rescore_candidates() if (row := db.get(pk)) is not None]
    if not rows:
        return {"count": 0, "approx_tokens": 0}
    batches = math.ceil(len(rows) / ws.settings().scoring.batch_size)
    chars = sum(len(r.description) for r in rows) + batches * len(ws.profile())
    return {"count": len(rows), "approx_tokens": math.ceil(chars / CHARS_PER_TOKEN)}
