"""Setup wizard: bundled region lists, connection tests, the Notion database and the profile draft."""

import base64
import binascii
import json
from datetime import datetime
from importlib.resources import files
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from jobscout import connections
from jobscout._output_sync import NOTION, read_back_notion_statuses
from jobscout.drafting import MAX_CV_CHARS, Draft, WizardAnswers, draft_profile, extract_cv_text
from jobscout.errors import ConfigError, NotionError, ProviderError, RunInProgress
from jobscout.outputs.notion import NotionClient, parse_page_id
from jobscout.scoring.providers import make_provider, test_provider
from jobscout.server.workspace import SAMPLE_READ_ONLY, Workspace
from jobscout.settings import NotionOutputSettings, ScoringSettings

router = APIRouter(prefix="/api")

TEST_TIMEOUT_S = 15
NO_NOTION_TOKEN = "Add your Notion token first."
NO_APIFY_TOKEN = "Add your Apify token first."
NOTION_CREATED = "Created the AI Job Scout database in Notion."
OLD_NOTION_UNREADABLE = "Couldn't read your current Notion database first, so nothing changed. Try again."
UNREADABLE_FILE = "That file couldn't be read. Try again or paste your CV."

_DATA = files("jobscout.data")


class AiTestBody(BaseModel):
    provider: Literal["anthropic", "openai", "gemini", "openrouter", "ollama", "claude_code", "fake"]
    model: str
    api_key: str | None = None


class ApifyTestBody(BaseModel):
    token: str | None = None


class WebhookTestBody(BaseModel):
    url: str


class CsvTestBody(BaseModel):
    path: str


class NotionConnectBody(BaseModel):
    parent_page: str


class CvFile(BaseModel):
    name: str
    base64: str


class DraftBody(BaseModel):
    cv_text: str | None = None
    cv_file: CvFile | None = None
    answers: WizardAnswers


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def _real_workspace(request: Request) -> Workspace:
    """The workspace, refusing sample mode: these calls use the user's own keys and accounts."""
    ws = _workspace(request)
    if ws.mode == "sample":
        raise ConfigError(SAMPLE_READ_ONLY)
    return ws


def _given(value: str | None) -> str | None:
    return (value.strip() or None) if value else None


def _result(res: connections.ConnectionResult) -> dict:
    return {"ok": res.ok, "message": res.message}


@router.get("/regions")
def regions() -> dict:
    linkedin = json.loads(_DATA.joinpath("linkedin_regions.json").read_text(encoding="utf-8"))
    indeed = json.loads(_DATA.joinpath("indeed_countries.json").read_text(encoding="utf-8"))
    return {
        "linkedin": [{"name": r["name"], "geo_id": r["geo_id"]} for r in linkedin],
        "indeed": indeed["countries"],
    }


@router.post("/test/ai")
def test_ai(body: AiTestBody, request: Request) -> dict:
    ws = _real_workspace(request)
    secrets = ws.secrets()
    key = _given(body.api_key)
    key_field = f"{body.provider}_api_key"  # claude_code, ollama and fake have none
    if key and key_field in type(secrets).model_fields:
        secrets = secrets.model_copy(update={key_field: key})  # in memory only: never saved
    try:
        provider = make_provider(ScoringSettings(provider=body.provider, model=body.model), secrets)
    except ConfigError as exc:  # a missing key is a test result, not a bad request
        return {"ok": False, "message": str(exc)}
    ok, message = test_provider(provider)
    return {"ok": ok, "message": message}


@router.post("/test/apify")
def test_apify(body: ApifyTestBody, request: Request) -> dict:
    ws = _real_workspace(request)
    token = _given(body.token) or ws.secrets().apify_token
    if not token:
        return {"ok": False, "message": NO_APIFY_TOKEN, "credit_usd": None}
    with httpx.Client(timeout=TEST_TIMEOUT_S) as client:
        res = connections.test_apify(token, client)
    return {**_result(res), "credit_usd": res.credit_usd}


@router.post("/test/webhook")
def test_webhook(body: WebhookTestBody, request: Request) -> dict:
    _real_workspace(request)
    with httpx.Client(timeout=TEST_TIMEOUT_S) as client:
        return _result(connections.test_webhook(body.url, client))


@router.post("/test/csv")
def test_csv(body: CsvTestBody, request: Request) -> dict:
    _real_workspace(request)
    return _result(connections.test_csv_path(body.path))


@router.post("/notion/connect")
def notion_connect(body: NotionConnectBody, request: Request) -> dict:
    """Create the tracker database. Replacing one first reads back the statuses set in the old one, then
    forgets every page there: the new database gets every job afresh, and the old one is left as it is.
    An old database Notion can't find (deleted, or hidden from the token) has nothing to read back."""
    ws = _real_workspace(request)
    token = ws.secrets().notion_token
    if not token:
        raise ConfigError(NO_NOTION_TOKEN)
    settings = ws.settings()
    old_database_id = settings.outputs.notion.database_id
    if old_database_id and ws.db().active_run(datetime.now()) is not None:
        raise RunInProgress  # a run is still writing to the old database
    parent_page_id = parse_page_id(body.parent_page)  # a bad link is a ConfigError before any Notion call
    with httpx.Client(timeout=TEST_TIMEOUT_S) as client:
        if old_database_id:
            try:
                read_back_notion_statuses(ws.db(), NotionClient(token, client), old_database_id)
            except NotionError as exc:
                if exc.status != 404:  # a 404 means the old database is gone: there is nothing to read back
                    raise ConfigError(OLD_NOTION_UNREADABLE) from None
        try:
            database_id = connections.connect_notion(token, body.parent_page, client)
        except NotionError as exc:
            raise HTTPException(502, str(exc)) from None
    if old_database_id:
        ws.db().clear_deliveries(NOTION)
    settings.outputs.notion = NotionOutputSettings(
        enabled=True, parent_page_id=parent_page_id, database_id=database_id
    )
    ws.save_settings(settings)
    return {"ok": True, "message": NOTION_CREATED, "database_id": database_id}


def _cv_text(body: DraftBody) -> str:
    if body.cv_file is not None:
        try:
            data = base64.b64decode(body.cv_file.base64, validate=True)
        except (binascii.Error, ValueError):
            raise ConfigError(UNREADABLE_FILE) from None
        return extract_cv_text(data, body.cv_file.name)
    return (body.cv_text or "").strip()[:MAX_CV_CHARS]


@router.post("/draft")
def draft(body: DraftBody, request: Request) -> Draft:
    ws = _real_workspace(request)
    cv = _cv_text(body)
    provider = make_provider(ws.settings().scoring, ws.secrets())
    try:
        return draft_profile(provider, cv, body.answers)
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from None
