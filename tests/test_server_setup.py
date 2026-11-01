import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from test_db_web import store

from jobscout.errors import ProviderError
from jobscout.models import Status
from jobscout.outputs.notion import NotionClient
from jobscout.paths import app_dir
from jobscout.server import routes_setup
from jobscout.server.workspace import SAMPLE_READ_ONLY
from jobscout.settings import Secrets, load_secrets, load_settings, save_secrets

FIXTURES = Path(__file__).parent / "fixtures"
ME_URL = "https://api.apify.com/v2/users/me"
LIMITS_URL = "https://api.apify.com/v2/users/me/limits"
NOTION_DB_URL = "https://api.notion.com/v1/databases"
HOOK_URL = "https://hooks.example.com/secret-path-123"
PAGE_ID = "0123456789abcdef0123456789abcdef"
DASHED_ID = "01234567-89ab-cdef-0123-456789abcdef"
REJECTED = "Apify rejected the token. Copy it again from Apify → Settings → API & Integrations."


class StubProvider:
    def __init__(self, reply="OK", error=None):
        self.reply, self.error, self.prompts = reply, error, []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.reply


def use_provider(monkeypatch, provider):
    """Replace make_provider and record the settings and secrets it was given."""
    seen = {}

    def fake(cfg, secrets):
        seen.update(cfg=cfg, secrets=secrets)
        return provider

    monkeypatch.setattr(routes_setup, "make_provider", fake)
    return seen


def credit_response():
    return httpx.Response(200, json={"data": {"limits": {"maxMonthlyUsageUsd": 5}, "current": {"monthlyUsageUsd": 1.5}}})


def test_regions_lists_bundled_data(client):
    body = client.get("/api/regions").json()
    assert {"name": "European Economic Area", "geo_id": "91000002"} in body["linkedin"]
    assert all(set(r) == {"name", "geo_id"} for r in body["linkedin"])
    assert {"uk", "ch", "de"} <= set(body["indeed"])  # the actor's codes: "uk", not "gb"


def test_regions_work_in_sample_mode(sample_client):
    assert sample_client.get("/api/regions").status_code == 200


def test_ai_test_with_unsaved_key_does_not_save(client, monkeypatch):
    seen = use_provider(monkeypatch, StubProvider("OK"))
    r = client.post("/api/test/ai", json={"provider": "openai", "model": "m1", "api_key": "sk-typed-key"})
    assert r.json() == {"ok": True, "message": "Connected."}
    assert seen["secrets"].openai_api_key == "sk-typed-key" and seen["cfg"].model == "m1"
    assert load_secrets() == Secrets()  # tested, not saved


def test_ai_test_without_key_uses_stored_one(client, monkeypatch):
    save_secrets(Secrets(anthropic_api_key="sk-stored-key"))
    seen = use_provider(monkeypatch, StubProvider("OK"))
    r = client.post("/api/test/ai", json={"provider": "anthropic", "model": "m", "api_key": None})
    assert r.json()["ok"] is True and seen["secrets"].anthropic_api_key == "sk-stored-key"


def test_ai_test_given_key_replaces_only_that_provider_key(client, monkeypatch):
    save_secrets(Secrets(anthropic_api_key="sk-stored-key", openai_api_key="sk-old"))
    seen = use_provider(monkeypatch, StubProvider("OK"))
    client.post("/api/test/ai", json={"provider": "openai", "model": "m", "api_key": "  sk-new  "})
    assert seen["secrets"].openai_api_key == "sk-new" and seen["secrets"].anthropic_api_key == "sk-stored-key"


def test_ai_test_reports_provider_error(client, monkeypatch):
    use_provider(monkeypatch, StubProvider(error=ProviderError("OpenAI rejected the API key. Check it in Settings → Keys.")))
    r = client.post("/api/test/ai", json={"provider": "openai", "model": "m", "api_key": "sk-bad"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "OpenAI rejected the API key. Check it in Settings → Keys."}


def test_ai_test_missing_key_is_a_result_not_an_error(client):
    r = client.post("/api/test/ai", json={"provider": "gemini", "model": "m", "api_key": None})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "Add your Gemini API key first."}


@pytest.mark.parametrize("provider", ["claude_code", "ollama"])
def test_ai_test_needs_no_key_for_local_providers(client, monkeypatch, provider):
    seen = use_provider(monkeypatch, StubProvider("OK"))
    r = client.post("/api/test/ai", json={"provider": provider, "model": "m", "api_key": None})
    assert r.json()["ok"] is True and seen["cfg"].provider == provider


def test_apify_rejected_token_message(client, respx_mock):
    respx_mock.get(ME_URL).respond(401)
    r = client.post("/api/test/apify", json={"token": "bad-token-value"})
    assert r.json() == {"ok": False, "message": REJECTED, "credit_usd": None}


def test_apify_ok_reports_credit(client, respx_mock):
    me = respx_mock.get(ME_URL).respond(200, json={"data": {}})
    respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    r = client.post("/api/test/apify", json={"token": "typed-token-value"})
    assert r.json() == {"ok": True, "message": "Connected.", "credit_usd": 3.5}
    assert me.calls.last.request.headers["authorization"] == "Bearer typed-token-value"
    assert load_secrets() == Secrets()


def test_apify_ok_without_readable_credit(client, respx_mock):
    respx_mock.get(ME_URL).respond(200, json={"data": {}})
    respx_mock.get(LIMITS_URL).respond(500)
    assert client.post("/api/test/apify", json={"token": "t"}).json() == {"ok": True, "message": "Connected.", "credit_usd": None}


def test_apify_null_token_uses_stored(client, respx_mock):
    save_secrets(Secrets(apify_token="stored-token"))
    me = respx_mock.get(ME_URL).respond(200, json={"data": {}})
    respx_mock.get(LIMITS_URL).mock(return_value=credit_response())
    assert client.post("/api/test/apify", json={"token": None}).json()["ok"] is True
    assert me.calls.last.request.headers["authorization"] == "Bearer stored-token"


def test_apify_without_any_token(client):
    r = client.post("/api/test/apify", json={"token": None}).json()
    assert r == {"ok": False, "message": "Add your Apify token first.", "credit_usd": None}


def test_apify_network_error_message(client, respx_mock):
    respx_mock.get(ME_URL).mock(side_effect=httpx.ConnectError("no route"))
    r = client.post("/api/test/apify", json={"token": "t"}).json()
    assert r == {"ok": False, "message": "Apify couldn't be reached. Check your internet connection and try again.", "credit_usd": None}


def test_apify_timeout_message(client, respx_mock):
    respx_mock.get(ME_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    r = client.post("/api/test/apify", json={"token": "t"}).json()
    assert r == {"ok": False, "message": "Apify took too long to answer. Try again.", "credit_usd": None}


def test_webhook_test_posts_fictional_job(client, respx_mock):
    route = respx_mock.post(HOOK_URL).respond(200)
    r = client.post("/api/test/webhook", json={"url": HOOK_URL})
    assert r.json() == {"ok": True, "message": "Sent a test job."}
    payload = json.loads(route.calls.last.request.content)
    assert payload["id"] == "test:0" and payload["title"] == "Test job from AI Job Scout"
    assert payload["company"] == "Example Company" and payload["score"] == 80 and payload["status"] == "To review"


def test_webhook_test_hides_url_in_errors(client, respx_mock):
    respx_mock.post(HOOK_URL).respond(404)
    r = client.post("/api/test/webhook", json={"url": HOOK_URL})
    assert r.json() == {"ok": False, "message": "The webhook returned HTTP 404."}
    assert "secret-path-123" not in r.text


def test_webhook_test_rejects_a_malformed_url(client):
    r = client.post("/api/test/webhook", json={"url": "not a url"})
    assert r.status_code == 200 and r.json()["ok"] is False and "not a url" not in r.text


def test_csv_path_missing_folder(client, tmp_path):
    r = client.post("/api/test/csv", json={"path": str(tmp_path / "nope" / "jobs.csv")})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "doesn't exist" in r.json()["message"]


def test_csv_path_ok_and_writes_nothing(client, tmp_path):
    r = client.post("/api/test/csv", json={"path": str(tmp_path / "out.csv")})
    assert r.json()["ok"] is True and str(tmp_path / "out.csv") in r.json()["message"]
    assert not (tmp_path / "out.csv").exists()


def test_csv_empty_path_means_default_file(client):
    r = client.post("/api/test/csv", json={"path": "  "})
    assert r.json()["ok"] is True and str(app_dir() / "jobs.csv") in r.json()["message"]


def test_csv_path_that_is_a_folder(client, tmp_path):
    r = client.post("/api/test/csv", json={"path": str(tmp_path)})
    assert r.json()["ok"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows folders don't use Unix permission bits.")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write to any folder.")
def test_csv_unwritable_folder(client, tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir(mode=0o500)
    try:
        r = client.post("/api/test/csv", json={"path": str(folder / "jobs.csv")})
    finally:
        folder.chmod(0o700)
    assert r.json()["ok"] is False and "can't write" in r.json()["message"]


def test_notion_connect_saves_database(client, respx_mock):
    save_secrets(Secrets(notion_token="secret-notion-token"))
    route = respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db-new-id"})
    r = client.post("/api/notion/connect", json={"parent_page": f"https://www.notion.so/My-Page-{PAGE_ID}"})
    assert r.json() == {"ok": True, "message": "Created the AI Job Scout database in Notion.", "database_id": "db-new-id"}
    assert json.loads(route.calls.last.request.content)["parent"]["page_id"] == DASHED_ID
    notion = load_settings().outputs.notion
    assert (notion.enabled, notion.parent_page_id, notion.database_id) == (True, DASHED_ID, "db-new-id")
    assert "secret-notion-token" not in r.text


def test_notion_connect_keeps_other_settings(client, respx_mock):
    save_secrets(Secrets(notion_token="t"))
    respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db"})
    settings = client.get("/api/settings").json()
    settings["scoring"]["min_score"] = 55
    client.put("/api/settings", json=settings)
    client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert load_settings().scoring.min_score == 55


def test_notion_connect_without_token_is_400(client):
    r = client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert r.status_code == 400 and r.json()["error"] == "Add your Notion token first."


def test_notion_connect_bad_link_is_400_and_saves_nothing(client):
    save_secrets(Secrets(notion_token="t"))
    r = client.post("/api/notion/connect", json={"parent_page": "https://example.com/nothing"})
    assert r.status_code == 400 and "Notion page link" in r.json()["error"]
    assert load_settings().outputs.notion.enabled is False


def test_notion_connect_notion_error_is_502(client, respx_mock):
    save_secrets(Secrets(notion_token="secret-notion-token"))
    respx_mock.post(NOTION_DB_URL).respond(404)
    r = client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert r.status_code == 502 and "Share it with your integration" in r.json()["error"]
    assert "secret-notion-token" not in r.text and load_settings().outputs.notion.enabled is False


def connected_with_one_page(db, applied_in_notion: bool):
    """Notion already connected to OLD_DB, with one job delivered there as To review."""
    from jobscout.settings import NotionOutputSettings, save_settings

    save_secrets(Secrets(notion_token="secret-notion-token"))
    settings = load_settings()
    settings.outputs.notion = NotionOutputSettings(enabled=True, parent_page_id=DASHED_ID, database_id=OLD_DB)
    save_settings(settings)
    pk = store(db, job_id="501")
    db.mark_delivery(pk, "notion", "delivered", external_id="old-page", pushed_status=Status.TO_REVIEW)
    label = "Applied" if applied_in_notion else "To review"
    page = {"id": "old-page", "last_edited_time": "2026-11-01T10:00:00.000Z", "properties": {"Status": {"select": {"name": label}}}}
    return pk, page


OLD_DB = "db-old-id"


def test_notion_reconnect_reads_back_the_old_database_then_starts_the_new_one_clean(client, db, respx_mock):
    pk, page = connected_with_one_page(db, applied_in_notion=True)
    query = respx_mock.post(f"{NOTION_DB_URL}/{OLD_DB}/query").respond(200, json={"results": [page], "has_more": False})
    create = respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db-new-id"})
    r = client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert r.status_code == 200 and r.json()["database_id"] == "db-new-id"
    assert query.called and create.called
    assert db.get(pk).status == Status.APPLIED  # what the user marked in the old database is kept
    assert db.deliveries("notion") == []  # the new database gets every job afresh
    assert load_settings().outputs.notion.database_id == "db-new-id"


def test_notion_reconnect_proceeds_when_the_old_database_is_gone(client, db, respx_mock):
    pk, _page = connected_with_one_page(db, applied_in_notion=True)
    query = respx_mock.post(f"{NOTION_DB_URL}/{OLD_DB}/query").respond(404)
    create = respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db-new-id"})
    r = client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert r.status_code == 200 and r.json()["database_id"] == "db-new-id"
    assert query.called and create.called
    assert load_settings().outputs.notion.database_id == "db-new-id"
    assert db.deliveries("notion") == []  # nothing carries over to the new database
    assert db.get(pk).status == Status.TO_REVIEW  # nothing was read back, so nothing changed


@pytest.mark.parametrize("failure", [500, 429, httpx.ReadTimeout("slow")])
def test_notion_reconnect_changes_nothing_when_the_old_database_cant_be_read(client, db, respx_mock, monkeypatch, failure):
    # Retries would wait a few real seconds; skip the waiting.
    monkeypatch.setattr(routes_setup, "NotionClient", lambda token, http: NotionClient(token, http, sleep=lambda _s: None))
    pk, _page = connected_with_one_page(db, applied_in_notion=True)
    query = respx_mock.post(f"{NOTION_DB_URL}/{OLD_DB}/query")
    if isinstance(failure, int):
        query.respond(failure)
    else:
        query.mock(side_effect=failure)
    create = respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db-new-id"})
    r = client.post("/api/notion/connect", json={"parent_page": PAGE_ID})
    assert r.status_code == 400
    assert r.json()["error"] == "Couldn't read your current Notion database first, so nothing changed. Try again."
    assert not create.called
    assert len(db.deliveries("notion")) == 1 and load_settings().outputs.notion.database_id == OLD_DB
    assert db.get(pk).status == Status.TO_REVIEW


def test_notion_reconnect_keeps_deliveries_when_creating_fails(client, db, respx_mock):
    _pk, page = connected_with_one_page(db, applied_in_notion=False)
    respx_mock.post(f"{NOTION_DB_URL}/{OLD_DB}/query").respond(200, json={"results": [page], "has_more": False})
    respx_mock.post(NOTION_DB_URL).respond(404)
    assert client.post("/api/notion/connect", json={"parent_page": PAGE_ID}).status_code == 502
    assert len(db.deliveries("notion")) == 1 and load_settings().outputs.notion.database_id == OLD_DB


def test_notion_reconnect_waits_for_a_run_in_progress(client, db, respx_mock):
    connected_with_one_page(db, applied_in_notion=False)
    client.get("/api/runs/active")  # the app has opened its database
    db.start_run("cli", datetime.now())
    create = respx_mock.post(NOTION_DB_URL).respond(200, json={"id": "db-new-id"})
    assert client.post("/api/notion/connect", json={"parent_page": PAGE_ID}).status_code == 409
    assert not create.called and len(db.deliveries("notion")) == 1


DRAFT_JSON = {
    "headline": "Product analyst · early career",
    "profile_md": "## About me\nYou are a fictional analyst.",
    "dealbreakers": [{"text": "the posting requires fluent German", "consequence": "max 25"}],
    "keywords": ["product analyst"],
    "indeed_queries": ["product analyst"],
}
ANSWERS = {"target_roles": ["Product analyst"], "career_level": "early career"}


def test_draft_endpoint_with_pdf(client, monkeypatch):
    provider = StubProvider(json.dumps(DRAFT_JSON))
    use_provider(monkeypatch, provider)
    data = base64.b64encode((FIXTURES / "cv_text.pdf").read_bytes()).decode()
    r = client.post("/api/draft", json={"cv_file": {"name": "cv.pdf", "base64": data}, "answers": ANSWERS})
    assert r.status_code == 200 and r.json() == DRAFT_JSON
    assert "Fictional Person" in provider.prompts[0]


def test_draft_endpoint_with_pasted_text_and_saved_provider(client, monkeypatch):
    provider = StubProvider(json.dumps(DRAFT_JSON))
    seen = use_provider(monkeypatch, provider)
    save_secrets(Secrets(anthropic_api_key="sk-stored"))
    r = client.post("/api/draft", json={"cv_text": "  Fictional pasted CV  ", "answers": ANSWERS})
    assert r.status_code == 200
    assert "Fictional pasted CV" in provider.prompts[0]
    assert seen["secrets"].anthropic_api_key == "sk-stored" and seen["cfg"] == load_settings().scoring


def test_draft_endpoint_without_cv_drafts_from_answers(client, monkeypatch):
    provider = StubProvider(json.dumps(DRAFT_JSON))
    use_provider(monkeypatch, provider)
    assert client.post("/api/draft", json={"answers": ANSWERS}).status_code == 200
    assert "no CV provided" in provider.prompts[0]


def test_draft_endpoint_truncates_long_pasted_text(client, monkeypatch):
    provider = StubProvider(json.dumps(DRAFT_JSON))
    use_provider(monkeypatch, provider)
    client.post("/api/draft", json={"cv_text": "x" * 40_000, "answers": ANSWERS})
    assert "x" * 30_000 in provider.prompts[0] and "x" * 30_001 not in provider.prompts[0]


def test_draft_endpoint_bad_base64_is_400(client, monkeypatch):
    use_provider(monkeypatch, StubProvider())
    r = client.post("/api/draft", json={"cv_file": {"name": "cv.pdf", "base64": "!!not base64!!"}, "answers": ANSWERS})
    assert r.status_code == 400
    assert r.json()["error"] == "That file couldn't be read. Try again or paste your CV."


def test_draft_endpoint_unreadable_file_is_400(client, monkeypatch):
    use_provider(monkeypatch, StubProvider())
    data = base64.b64encode(b"x").decode()
    r = client.post("/api/draft", json={"cv_file": {"name": "cv.docx", "base64": data}, "answers": ANSWERS})
    assert r.status_code == 400 and "PDF or a text file" in r.json()["error"]


def test_draft_endpoint_provider_error_is_502(client, monkeypatch):
    use_provider(monkeypatch, StubProvider(error=ProviderError("Anthropic took too long to answer.")))
    r = client.post("/api/draft", json={"cv_text": "cv", "answers": ANSWERS})
    assert r.status_code == 502 and r.json()["error"] == "Anthropic took too long to answer."


def test_draft_endpoint_missing_key_is_400(client):
    r = client.post("/api/draft", json={"cv_text": "cv", "answers": ANSWERS})  # default provider, no key
    assert r.status_code == 400 and r.json()["error"] == "Add your Anthropic API key first."


def test_draft_endpoint_needs_a_target_role(client):
    r = client.post("/api/draft", json={"answers": {"target_roles": [], "career_level": "x"}})
    assert r.status_code == 422 and "answers.target_roles" in r.json()["fields"]


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/test/ai", {"provider": "openai", "model": "m", "api_key": "k"}),
        ("/api/test/apify", {"token": "t"}),
        ("/api/test/webhook", {"url": HOOK_URL}),
        ("/api/test/csv", {"path": ""}),
        ("/api/notion/connect", {"parent_page": PAGE_ID}),
        ("/api/draft", {"cv_text": "cv", "answers": ANSWERS}),
    ],
)
def test_setup_endpoints_refuse_in_sample_mode(sample_client, respx_mock, path, body):
    r = sample_client.post(path, json=body)  # respx_mock: any real request would fail the test
    assert r.status_code == 400 and r.json() == {"error": SAMPLE_READ_ONLY, "fields": None}
