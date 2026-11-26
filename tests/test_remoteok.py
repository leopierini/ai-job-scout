import json
import threading
from datetime import date
from pathlib import Path

import httpx

from jobscout.settings import RemoteOkSettings
from jobscout.sources import SearchArea
from jobscout.sources.remoteok import RemoteOkSource, parse_item

FEED = json.loads((Path(__file__).parent / "fixtures" / "remoteok_feed.json").read_text(encoding="utf-8"))
URL = "https://remoteok.com/api"
TODAY = date(2026, 11, 26)
MONTH = 30 * 86400
QUERIES = ("Product Manager", "Associate Product Manager")


def make_source(queries=QUERIES, cancel=None, sleeps=None):
    settings = RemoteOkSettings(enabled=True, queries=list(queries))
    sleeps = [] if sleeps is None else sleeps
    return RemoteOkSource(settings, httpx.Client(), cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append)


def run(src, window=MONTH, calls=None):
    report = (lambda *a: calls.append(a)) if calls is not None else (lambda *a: None)
    return src.search_area(src.areas()[0], window, report)


def test_the_legal_notice_is_not_a_job():
    assert parse_item(FEED[0], "k", "a") is None


def test_parse_item_full():
    job = parse_item(FEED[1], "Product Manager", "Remote OK")
    assert job.pk == "remoteok:4000001"
    assert (job.title, job.company, job.location) == ("Junior Product Manager", "Pinecone Studio", "Remote (Worldwide)")
    assert job.url == "https://remoteOK.com/remote-jobs/remote-junior-product-manager-pinecone-studio-4000001"
    assert (job.posted, job.salary) == ("2026-11-24", "USD 45,000–60,000 a year")
    assert job.description.startswith("Open to people in: Worldwide\nTags: product, saas\nSalary: USD 45,000–60,000")
    assert "scheduling software" in job.description and "<div>" not in job.description


def test_parse_item_without_a_salary_or_date_string():
    job = parse_item({"id": 7, "position": "PM", "epoch": 1795444809, "salary_min": 0, "salary_max": 0}, "k", "a")
    assert (job.job_id, job.posted, job.salary, job.url) == ("7", "2026-11-23", "", "")


def test_areas_one_request():
    assert make_source().areas() == [SearchArea("remoteok:all", "Remote OK", "remoteok", 1, {})]


def test_one_request_for_every_search(respx_mock):
    route = respx_mock.get(URL).respond(200, json=FEED)
    sleeps = []
    calls = []
    res = run(make_source(sleeps=sleeps), calls=calls)
    assert route.call_count == 1 and sleeps == []
    assert route.calls[0].request.headers["User-Agent"].startswith("ai-job-scout")
    # 4000002 is US only, 4000004 not a search, 4000005 in a place we can't place, 4000006 too old.
    assert [j.job_id for j in res.jobs] == ["4000001", "4000003"]
    assert res.jobs[1].location == "Remote (UK)"
    assert res.complete and res.error is None
    assert calls == [(0, 1, 0), (1, 1, 2)]


def test_server_errors_are_retried_then_reported(respx_mock):
    route = respx_mock.get(URL).respond(502)
    sleeps = []
    res = run(make_source(sleeps=sleeps))
    assert route.call_count == 3 and sleeps == [1, 2]
    assert res.complete is False and str(res.error) == "Remote OK answered with an error (HTTP 502)."


def test_unreadable_answer(respx_mock):
    respx_mock.get(URL).respond(200, json={"error": "blocked"})
    res = run(make_source())
    assert res.complete is False and str(res.error) == "Remote OK sent an answer we couldn't read."
