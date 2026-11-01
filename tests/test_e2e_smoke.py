"""End-to-end smoke test: the real server and the built frontend in a real browser. Opt in with JOBSCOUT_E2E=1."""

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from jobscout.server.app import STATIC_DIR, create_app

ROOT = Path(__file__).resolve().parent.parent
SHOOT = ROOT / "frontend" / "scripts" / "shoot.mjs"
ROUTES = ["#/", "#/history", "#/settings/profile"]
START_TIMEOUT_S = 15
SHOOT_TIMEOUT_S = 300

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("JOBSCOUT_E2E") != "1", reason="Set JOBSCOUT_E2E=1 to run the browser smoke test."),
    pytest.mark.skipif(shutil.which("node") is None, reason="The smoke test needs node on PATH."),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server_url():
    """The sample-mode app on a free port, in a thread. JOBSCOUT_HOME is the per-test folder from conftest.py."""
    yield from _serve(sample=True)


@pytest.fixture
def real_server_url():
    """The real-mode app on an empty JOBSCOUT_HOME: a first launch, so the setup wizard shows."""
    yield from _serve(sample=False)


def _serve(sample: bool):
    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("The frontend isn't built: run npm run build in frontend/.")
    port = free_port()
    app = create_app(sample=sample)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            try:
                if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline or not thread.is_alive():
                pytest.fail("The server didn't start.")
            time.sleep(0.1)
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def shoot(url: str, out: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(SHOOT), url, str(out), *args],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
        timeout=SHOOT_TIMEOUT_S,
    )


def test_main_history_and_settings_render_with_a_clean_console(server_url, tmp_path):
    shot = shoot(server_url, tmp_path / "shots", "--start-sample-run", *ROUTES)
    assert shot.returncode == 0, f"{shot.stdout}\n{shot.stderr}"
    assert "Console clean" in shot.stdout or "Clean of errors" in shot.stdout
    assert len(list((tmp_path / "shots").glob("*.png"))) == 2 * len(ROUTES)

    counts = httpx.get(f"{server_url}/api/jobs/counts").json()
    assert sum(counts.values()) > 0


def test_first_launch_shows_the_setup_wizard_with_a_clean_console(real_server_url, tmp_path):
    routes = ["#/", "#/setup?step=2"]  # the main screen sends a first launch to setup
    shot = shoot(real_server_url, tmp_path / "shots", *routes)
    assert shot.returncode == 0, f"{shot.stdout}\n{shot.stderr}"
    assert len(list((tmp_path / "shots").glob("*.png"))) == 2 * len(routes)
    assert httpx.get(f"{real_server_url}/api/state").json()["mode"] == "real"
