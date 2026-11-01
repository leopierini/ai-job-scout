"""State, mode, settings, profile and API keys."""

import shutil
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, create_model

import jobscout
from jobscout import paths
from jobscout.errors import RunInProgress
from jobscout.server.workspace import Workspace
from jobscout.settings import Secrets, Settings

router = APIRouter(prefix="/api")

HINT_MIN_LENGTH = 12  # shorter keys would give away too much of themselves


class ModeBody(BaseModel):
    mode: Literal["real", "sample"]


class ProfileBody(BaseModel):
    text: str


# {field: str | null} for each secret; unknown fields are refused.
KeysUpdate = create_model(
    "KeysUpdate",
    __config__=ConfigDict(extra="forbid"),
    **{name: (str | None, None) for name in Secrets.model_fields},
)


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def _state(ws: Workspace) -> dict:
    ui = ws.settings().ui
    return {
        "mode": ws.mode,
        "setup_complete": ui.setup_complete,
        "headline": ui.headline,
        "claude_code_found": shutil.which("claude") is not None,
        "paths": {
            "app_dir": str(paths.app_dir()),
            "database": str(ws.db_file),
            "settings": str(paths.settings_path()),
            "profile": str(paths.profile_path()),
        },
    }


def _keys(ws: Workspace) -> dict:
    """Whether each key is set, never its value: at most the last 4 characters of a long one."""
    out = {}
    for name, value in ws.secrets().model_dump().items():
        out[name] = {"set": bool(value), "hint": "…" + value[-4:] if value and len(value) >= HINT_MIN_LENGTH else ""}
    return out


@router.get("/health")
def health() -> dict:
    return {"app": "ai-job-scout", "version": jobscout.__version__}


@router.get("/state")
def get_state(request: Request) -> dict:
    return _state(_workspace(request))


@router.put("/mode")
def put_mode(body: ModeBody, request: Request) -> dict:
    ws = _workspace(request)
    if ws.db().active_run(datetime.now()) is not None:
        raise RunInProgress
    ws.set_mode(body.mode)
    return _state(ws)


@router.get("/settings")
def get_settings(request: Request) -> dict:
    return _workspace(request).settings().model_dump(mode="json")


@router.put("/settings")
def put_settings(body: Settings, request: Request) -> dict:
    ws = _workspace(request)
    ws.save_settings(body)  # FastAPI has already validated the body: an invalid one never gets here
    return ws.settings().model_dump(mode="json")


@router.get("/profile")
def get_profile(request: Request) -> dict:
    return {"text": _workspace(request).profile()}


@router.put("/profile")
def put_profile(body: ProfileBody, request: Request) -> dict:
    ws = _workspace(request)
    ws.save_profile(body.text)
    return {"text": ws.profile()}


@router.get("/keys")
def get_keys(request: Request) -> dict:
    return _keys(_workspace(request))


@router.put("/keys")
def put_keys(body: KeysUpdate, request: Request) -> dict:
    ws = _workspace(request)
    secrets = ws.secrets()
    for name in body.model_fields_set:
        value = getattr(body, name)
        value = value.strip() if value else None
        setattr(secrets, name, value or None)
    ws.save_secrets(secrets)
    return _keys(ws)
