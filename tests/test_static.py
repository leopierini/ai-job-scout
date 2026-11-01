"""The built frontend in server/static is served at / and its assets load."""

import re

import pytest
from fastapi.testclient import TestClient

from jobscout.server.app import STATIC_DIR, create_app

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client():
    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("The frontend isn't built: run npm run build in frontend/.")
    with TestClient(create_app(static_dir=STATIC_DIR), base_url=BASE) as c:
        yield c


def test_root_serves_the_built_app(client):
    res = client.get("/")
    assert res.status_code == 200
    assert '<div id="root"></div>' in res.text


def test_scripts_referenced_by_index_load(client):
    html = client.get("/").text
    scripts = re.findall(r'src="\./(assets/[^"]+\.js)"', html)
    assert scripts, "index.html references no script in assets/"
    for path in scripts:
        res = client.get(f"/{path}")
        assert res.status_code == 200
        assert "javascript" in res.headers["content-type"]


def test_app_routes_fall_back_to_index(client):
    res = client.get("/settings/profile")
    assert res.status_code == 200
    assert '<div id="root"></div>' in res.text
