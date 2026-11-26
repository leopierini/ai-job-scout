import json
import threading
from datetime import date
from pathlib import Path

import httpx

from jobscout.settings import RemotiveSettings
from jobscout.sources import AreaResult, SearchArea, remotive
from jobscout.sources.remotive import RemotiveSource, parse_item

FEED = json.loads((Path(__file__).parent / "fixtures" / "remotive_feed.json").read_text(encoding="utf-8"))
URL = "https://remotive.com/api/remote-jobs"
TODAY = date(2026, 11, 26)
MONTH = 30 * 86400
QUERIES = ("Product Manager", "Associate Product Manager")


def make_source(queries=QUERIES, cancel=None, sleeps=None):
    settings = RemotiveSettings(enabled=True, queries=list(queries))
    sleeps = [] if sleeps is None else sleeps
    return RemotiveSource(settings, httpx.Client(), cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append)


def run(src, window=MONTH, calls=None):
    report = (lambda *a: calls.append(a)) if calls is not None else (lambda *a: None)
    return src.search_area(src.areas()[0], window, report)


def test_parse_item_full():
    job = parse_item(FEED["jobs"][0], "Associate Product Manager", "Remotive")
    assert job.pk == "remotive:3000001"
    assert (job.title, job.company, job.location) == ("Associate Product Manager", "Kestrel Payments", "Remote (Europe)")
    assert job.url == "https://remotive.com/remote-jobs/product/associate-product-manager-3000001"
    assert (job.posted, job.employment_type, job.salary) == ("2026-11-20", "Full time", "€40k - €50k")
    assert (job.keyword, job.area) == ("Associate Product Manager", "Remotive")
    assert job.description.startswith("Open to people in: Europe\nCategory: Product Management\nJob type: Full time")
    assert "- Own the roadmap" in job.description and "<p>" not in job.description


def test_parse_item_needs_an_id_and_coerces_fields():
    assert parse_item({"title": "No id"}, "k", "a") is None
    job = parse_item({"id": 9, "title": None, "company_name": ["x"], "url": "javascript:alert(1)",
                      "publication_date": "yesterday", "candidate_required_location": None}, "k", "a")  # fmt: skip
    assert job.job_id == "9" and job.location == "Remote (Worldwide)"
    assert (job.title, job.company, job.url, job.posted, job.salary) == ("",) * 5


def test_areas_one_request():
    assert make_source().areas() == [SearchArea("remotive:all", "Remotive", "remotive", 1, {})]


def test_one_request_with_no_filters(respx_mock):
    route = respx_mock.get(URL).respond(200, json=FEED)
    run(make_source(queries=("a", "b", "c")))
    assert route.call_count == 1
    request = route.calls[0].request
    assert str(request.url) == URL  # filters are matched here, not sent
    assert request.headers["User-Agent"].startswith("ai-job-scout")


def test_keeps_matching_jobs_open_to_europe_in_the_window(respx_mock):
    respx_mock.get(URL).respond(200, json=FEED)
    calls = []
    res = run(make_source(), calls=calls)
    assert isinstance(res, AreaResult)
    # 3000002 is US only, 3000003 too old, 3000004 not a search, the one without an id is skipped.
    assert [j.job_id for j in res.jobs] == ["3000001", "3000005"]
    assert res.jobs[0].title == "Associate Product Manager"  # first sighting wins
    assert [j.keyword for j in res.jobs] == ["Product Manager", "Product Manager"]  # the first search that matches
    assert res.jobs[1].location == "Remote (Europe)"
    assert (res.billed, res.complete, res.error) == (0, True, None)
    assert calls == [(0, 1, 0), (1, 1, 2)]


def test_a_longer_window_keeps_older_jobs(respx_mock):
    respx_mock.get(URL).respond(200, json=FEED)
    res = run(make_source(), window=150 * 86400)
    assert "3000003" in [j.job_id for j in res.jobs]


def test_no_matching_search_keeps_nothing(respx_mock):
    respx_mock.get(URL).respond(200, json=FEED)
    res = run(make_source(queries=("Data Engineer",)))
    assert res.jobs == [] and res.complete


def test_rate_limit_is_not_retried(respx_mock):
    route = respx_mock.get(URL).respond(429)
    sleeps = []
    res = run(make_source(sleeps=sleeps))
    assert route.call_count == 1 and sleeps == []  # a repeat within a minute gets the app blocked
    assert res.complete is False and res.jobs == []
    assert str(res.error) == "Remotive asked us to slow down. It will be retried next run."


def test_error_is_reported_without_the_address(respx_mock):
    respx_mock.get(URL).respond(503)
    res = run(make_source())
    assert res.complete is False and "HTTP 503" in str(res.error) and "remotive.com" not in str(res.error)


def test_unreadable_answer(respx_mock):
    respx_mock.get(URL).respond(200, text="<html>maintenance</html>")
    res = run(make_source())
    assert res.complete is False and str(res.error) == "Remotive sent an answer we couldn't read."


def test_cancelled_before_the_request(respx_mock):
    cancel = threading.Event()
    cancel.set()
    route = respx_mock.get(URL).respond(200, json=FEED)
    res = run(make_source(cancel=cancel))
    assert route.call_count == 0 and res.complete is False and res.error is None


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    real = remotive.parse_item

    def flaky(raw, query, area):
        if raw.get("id") == 3000005:
            raise ValueError("bad item")
        return real(raw, query, area)

    monkeypatch.setattr(remotive, "parse_item", flaky)
    respx_mock.get(URL).respond(200, json=FEED)
    res = run(make_source())
    assert [j.job_id for j in res.jobs] == ["3000001"] and res.complete


def test_a_job_published_the_day_before_the_window_is_kept(respx_mock):
    """Remotive lists a job a day after its publication date: a short window must still find it."""
    item = dict(FEED["jobs"][0], publication_date="2026-11-25T22:00:00")
    respx_mock.get(URL).respond(200, json={"jobs": [item]})
    result = run(make_source(), window=7 * 3600)  # read again 6 hours after the last run, plus the overlap
    assert [j.job_id for j in result.jobs] == [str(item["id"])]


def test_not_read_again_within_six_hours(respx_mock):
    route = respx_mock.get(URL).respond(200, json=FEED)
    result = run(make_source(), window=5 * 3600)
    assert route.call_count == 0
    assert (result.jobs, result.complete) == ([], False)  # the next read's window still covers this gap
    assert "a few times a day" in str(result.error)
