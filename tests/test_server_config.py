import socket

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from jobscout import __version__, cli
from jobscout.db import Database
from jobscout.paths import app_dir, db_path, profile_path, settings_path
from jobscout.server import launch
from jobscout.server.app import create_app
from jobscout.settings import load_profile, load_secrets, save_secrets, Secrets

BASE = "http://127.0.0.1:8765"
APP_HEADER = {"X-Requested-By": "ai-job-scout"}
SAMPLE_MESSAGE = "You're exploring sample data. Set up your own search to change settings."


# --- security ---------------------------------------------------------------------------------


def test_health(client):
    assert client.get("/api/health").json() == {"app": "ai-job-scout", "version": __version__}


def test_post_without_app_header_is_rejected(raw_client):
    assert raw_client.put("/api/profile", json={"text": "x"}).status_code == 403
    assert not profile_path().exists()


def test_get_does_not_need_app_header(raw_client):
    assert raw_client.get("/api/health").status_code == 200


def test_foreign_host_header_is_rejected(raw_client):
    assert raw_client.get("/api/state", headers={"Host": "evil.example:8765"}).status_code == 403


def test_localhost_host_header_is_accepted(raw_client):
    assert raw_client.get("/api/health", headers={"Host": "localhost:8765"}).status_code == 200


def test_foreign_origin_is_rejected(client):
    r = client.put("/api/profile", json={"text": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert not profile_path().exists()


def test_local_origin_is_accepted(client):
    r = client.put("/api/profile", json={"text": "x"}, headers={"Origin": "http://localhost:8765"})
    assert r.status_code == 200


def test_security_rejection_uses_the_error_shape(raw_client):
    body = raw_client.put("/api/profile", json={"text": "x"}).json()
    assert set(body) == {"error", "fields"} and body["fields"] is None


def test_no_cors_headers(client):
    r = client.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"})
    assert "access-control-allow-origin" not in r.headers


# --- state and mode ---------------------------------------------------------------------------


def test_state_reports_mode_and_setup(client):
    state = client.get("/api/state").json()
    assert state["mode"] == "real" and state["setup_complete"] is False and state["headline"] == ""
    assert isinstance(state["claude_code_found"], bool)
    assert state["paths"] == {
        "app_dir": str(app_dir()),
        "database": str(db_path()),
        "settings": str(settings_path()),
        "profile": str(profile_path()),
    }


def test_state_reflects_saved_settings(client):
    s = client.get("/api/settings").json()
    s["ui"].update(setup_complete=True, headline="Data analyst, Oslo")
    assert client.put("/api/settings", json=s).status_code == 200
    state = client.get("/api/state").json()
    assert state["setup_complete"] is True and state["headline"] == "Data analyst, Oslo"


def test_claude_code_found_follows_path(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert client.get("/api/state").json()["claude_code_found"] is True
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert client.get("/api/state").json()["claude_code_found"] is False


def test_sample_app_starts_in_sample_mode(sample_client):
    state = sample_client.get("/api/state").json()
    assert state["mode"] == "sample" and state["paths"]["database"] == str(app_dir() / "sample.db")


def test_put_mode_switches_and_back(client):
    assert client.put("/api/mode", json={"mode": "sample"}).json()["mode"] == "sample"
    assert client.get("/api/settings").json()["linkedin"]["keywords"]  # the sample settings
    assert client.put("/api/mode", json={"mode": "real"}).json()["mode"] == "real"


def test_put_mode_rejects_unknown_mode(client):
    r = client.put("/api/mode", json={"mode": "demo"})
    assert r.status_code == 422 and "mode" in r.json()["fields"]


def test_put_mode_is_409_while_a_run_is_active(client):
    from datetime import datetime

    other = Database(db_path())
    other.start_run("manual", datetime.now())
    other.close()
    r = client.put("/api/mode", json={"mode": "sample"})
    assert r.status_code == 409 and r.json()["fields"] is None
    assert client.get("/api/state").json()["mode"] == "real"


# --- settings and profile ---------------------------------------------------------------------


def test_settings_round_trip(client):
    s = client.get("/api/settings").json()
    s["scoring"]["min_score"] = 55
    assert client.put("/api/settings", json=s).json()["scoring"]["min_score"] == 55
    assert client.get("/api/settings").json()["scoring"]["min_score"] == 55
    assert settings_path().exists()


def test_invalid_settings_returns_field_errors_and_saves_nothing(client):
    s = client.get("/api/settings").json()
    s["scoring"]["min_score"] = 150
    r = client.put("/api/settings", json=s)
    assert r.status_code == 422 and "scoring.min_score" in r.json()["fields"] and not settings_path().exists()
    assert r.json()["error"]


def test_validation_message_is_plain(client):
    s = client.get("/api/settings").json()
    s["scoring"]["min_score"] = 150
    r = client.put("/api/settings", json=s)
    assert r.json()["fields"]["scoring.min_score"] == "Use a number from 0 to 100."


def test_validation_message_for_too_small(client):
    s = client.get("/api/settings").json()
    s["scoring"]["batch_size"] = 0
    r = client.put("/api/settings", json=s)
    assert r.json()["fields"]["scoring.batch_size"] == "Use a number from 1 to 50."


def test_validation_message_with_one_bound_and_other_kinds(client):
    s = client.get("/api/settings").json()
    s["scoring"]["max_jobs_per_run"] = 0
    s["scoring"]["min_score"] = "lots"
    s["cleanup"]["max_age_days"] = 0
    fields = client.put("/api/settings", json=s).json()["fields"]
    assert fields["scoring.max_jobs_per_run"] == "Use 1 or more."
    assert fields["cleanup.max_age_days"] == "Use 1 or more."
    assert fields["scoring.min_score"] == "Enter a number."


def test_profile_round_trip(client):
    assert client.get("/api/profile").json() == {"text": ""}
    assert client.put("/api/profile", json={"text": "I like data."}).json() == {"text": "I like data."}
    assert load_profile() == "I like data."


# --- keys -------------------------------------------------------------------------------------


def test_keys_endpoint_never_returns_values(client):
    client.put("/api/keys", json={"apify_token": "apify_api_FAKE0000000000wxyz"})
    r = client.get("/api/keys")
    body = r.text
    assert "FAKE0000" not in body and '"hint":"…wxyz"' in body.replace(" ", "")
    assert r.json()["apify_token"]["set"] is True and r.json()["notion_token"] == {"set": False, "hint": ""}


def test_put_keys_response_never_returns_values(client):
    r = client.put("/api/keys", json={"apify_token": "apify_api_FAKE0000000000wxyz"})
    assert "FAKE0000" not in r.text


def test_short_keys_have_no_hint(client):
    client.put("/api/keys", json={"openai_api_key": "short-key"})
    assert client.get("/api/keys").json()["openai_api_key"] == {"set": True, "hint": ""}


def test_put_keys_trims_whitespace(client):
    client.put("/api/keys", json={"notion_token": "  ntn_fake_123456789\n"})
    assert load_secrets().notion_token == "ntn_fake_123456789"


def test_put_keys_null_removes_and_absent_keeps(client):
    save_secrets(Secrets(apify_token="apify_fake_token_1234", notion_token="ntn_fake_123456789"))
    client.put("/api/keys", json={"apify_token": None})
    after = load_secrets()
    assert after.apify_token is None and after.notion_token == "ntn_fake_123456789"
    client.put("/api/keys", json={"notion_token": "   "})
    assert load_secrets().notion_token is None


def test_put_keys_rejects_unknown_fields(client):
    r = client.put("/api/keys", json={"bogus_key": "x"})
    assert r.status_code == 422 and "bogus_key" in r.json()["fields"]


# --- sample mode ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, body",
    [("/api/profile", {"text": "x"}), ("/api/keys", {"notion_token": "abc"})],
)
def test_sample_mode_refuses_writes(sample_client, path, body):
    r = sample_client.put(path, json=body)
    assert r.status_code == 400 and r.json() == {"error": SAMPLE_MESSAGE, "fields": None}
    assert not profile_path().exists() and load_secrets() == Secrets()


def test_sample_mode_refuses_settings_writes(sample_client):
    s = sample_client.get("/api/settings").json()
    r = sample_client.put("/api/settings", json=s)
    assert r.status_code == 400 and r.json()["error"] == SAMPLE_MESSAGE and not settings_path().exists()


def test_sample_mode_reads_sample_data(sample_client):
    assert sample_client.get("/api/profile").json()["text"].strip()
    assert all(not v["set"] for v in sample_client.get("/api/keys").json().values())


# --- errors -----------------------------------------------------------------------------------


def test_unreadable_settings_file_is_a_400(client):
    settings_path().write_text("{not json", encoding="utf-8")
    r = client.get("/api/settings")
    assert r.status_code == 400 and "settings" in r.json()["error"]


def test_unknown_errors_are_a_plain_500(static_dir, monkeypatch):
    app = create_app(static_dir=static_dir)

    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(app.state.workspace, "profile", boom)
    with TestClient(app, base_url=BASE, headers=APP_HEADER, raise_server_exceptions=False) as c:
        r = c.get("/api/profile")
    assert r.status_code == 500 and "secret detail" not in r.text
    assert r.json() == {"error": "Something went wrong. Check the terminal window for details.", "fields": None}


def test_unknown_api_path_is_a_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and set(r.json()) == {"error", "fields"}


# --- static files -----------------------------------------------------------------------------


def test_spa_fallback_serves_index(tmp_path):
    static = tmp_path / "built"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    with TestClient(create_app(static_dir=static), base_url=BASE) as c:
        assert "INDEX" in c.get("/").text
        assert "INDEX" in c.get("/settings/keys").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404  # the fallback never swallows /api


def test_spa_fallback_cannot_escape_the_static_folder(tmp_path):
    static = tmp_path / "built"
    static.mkdir()
    (static / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("TOP SECRET", encoding="utf-8")
    with TestClient(create_app(static_dir=static), base_url=BASE) as c:
        assert "TOP SECRET" not in c.get("/%2e%2e/outside.txt").text
        assert "TOP SECRET" not in c.get("/..%2foutside.txt").text


def test_missing_static_shows_build_hint(tmp_path):
    with TestClient(create_app(static_dir=tmp_path / "nothing"), base_url=BASE) as c:
        r = c.get("/")
    assert r.status_code == 200 and "npm run build" in r.text and "isn't built yet" in r.text


# --- launch -----------------------------------------------------------------------------------


def test_pick_port_reuses_running_app():
    assert launch.pick_port(8765, probe=lambda port: {"app": "ai-job-scout"}) == (8765, True)


def test_pick_port_finds_a_running_app_on_a_later_port_before_taking_a_free_one():
    running_on = 8765 + 3  # the preferred port was busy when that instance started
    probed = []

    def probe(port):
        probed.append(port)
        return {"app": "ai-job-scout"} if port == running_on else None

    assert launch.pick_port(8765, probe=probe) == (running_on, True)
    assert probed == list(range(8765, running_on + 1))


def test_probe_ignores_proxy_settings(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs, url=url)
        raise launch.httpx.ConnectError("refused")

    monkeypatch.setattr(launch.httpx, "get", fake_get)
    assert launch._probe_health(8765) is None
    assert seen["trust_env"] is False and seen["url"] == "http://127.0.0.1:8765/api/health"


def test_pick_port_ignores_other_apps_and_dead_ports():
    free = _free_port()
    assert launch.pick_port(free, probe=lambda port: {"app": "something-else"}) == (free, False)
    assert launch.pick_port(free, probe=lambda port: None) == (free, False)


def test_pick_port_skips_busy_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        picked, running = launch.pick_port(port, probe=lambda p: None)
    assert running is False and port < picked <= port + 20


@pytest.mark.parametrize("posix, expected", [(True, [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]), (False, [])])
def test_is_free_sets_reuseaddr_only_on_posix(monkeypatch, posix, expected):
    options = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def setsockopt(self, *args):
            options.append(args)

        def bind(self, address):
            pass

    monkeypatch.setattr(launch.socket, "socket", lambda: FakeSocket())
    monkeypatch.setattr(launch, "_is_posix", lambda: posix)
    assert launch._is_free(8765) is True
    assert options == expected


def test_pick_port_gives_up_when_everything_is_busy(monkeypatch):
    monkeypatch.setattr(launch, "_is_free", lambda port: False)
    with pytest.raises(OSError):
        launch.pick_port(8765, probe=lambda p: None)


def test_serve_only_opens_the_browser_when_already_running(monkeypatch):
    opened = []
    monkeypatch.setattr(launch, "pick_port", lambda preferred: (8765, True))
    monkeypatch.setattr(launch.webbrowser, "open", opened.append)
    assert launch.serve(None, True, False) == 0
    assert opened == ["http://127.0.0.1:8765/"]


def test_serve_runs_uvicorn_on_localhost(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr(launch, "pick_port", lambda preferred: (8801, False))
    monkeypatch.setattr(launch, "_run_server", lambda app, port, open_browser: calls.update(port=port, open=open_browser))
    assert launch.serve(None, False, True) == 0
    assert calls == {"port": 8801, "open": False}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- cli --------------------------------------------------------------------------------------


def test_cli_start_parses_flags(monkeypatch, capsys):
    seen = {}

    def fake_serve(port, open_browser, sample):
        seen.update(port=port, open_browser=open_browser, sample=sample)
        return 0

    monkeypatch.setattr("jobscout.server.launch.serve", fake_serve)
    assert cli.main(["start", "--port", "8799", "--no-browser", "--sample"]) == 0
    assert seen == {"port": 8799, "open_browser": False, "sample": True}


def test_cli_start_defaults(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "jobscout.server.launch.serve",
        lambda port, open_browser, sample: seen.update(port=port, open_browser=open_browser, sample=sample) or 0,
    )
    cli.main(["start"])
    assert seen == {"port": None, "open_browser": True, "sample": False}


# --- framing ----------------------------------------------------------------------------------

NO_FRAMING = {"x-frame-options": "DENY", "content-security-policy": "frame-ancestors 'none'"}


def _assert_no_framing(res):
    for name, value in NO_FRAMING.items():
        assert res.headers[name] == value


def test_api_response_forbids_framing(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    _assert_no_framing(res)


def test_index_page_forbids_framing(client):
    res = client.get("/")
    assert res.status_code == 200
    _assert_no_framing(res)


def test_static_file_forbids_framing(static_dir):
    static_dir.mkdir()
    (static_dir / "app.js").write_text("export {}")
    with TestClient(create_app(static_dir=static_dir), base_url=BASE) as c:
        res = c.get("/app.js")
    assert res.status_code == 200
    _assert_no_framing(res)


def test_not_found_forbids_framing(client):
    res = client.get("/api/nothing-here")
    assert res.status_code == 404
    _assert_no_framing(res)


def test_rejected_request_forbids_framing(raw_client):
    res = raw_client.put("/api/profile", json={"text": "x"})
    assert res.status_code == 403
    _assert_no_framing(res)


def test_server_error_forbids_framing(static_dir):
    app = create_app(static_dir=static_dir)

    def boom():
        raise RuntimeError("boom")

    app.router.routes.insert(0, APIRoute("/api/boom", boom))
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as c:
        res = c.get("/api/boom")
    assert res.status_code == 500
    _assert_no_framing(res)
