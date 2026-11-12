import json
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobscout.settings import JobgetherSettings
from jobscout.sources import AreaResult, SearchArea, jobgether
from jobscout.sources.jobgether import (
    JobgetherSource,
    parse_item,
    parse_salary,
    request_body,
)

PAGE = json.loads((Path(__file__).parent / "fixtures" / "jobgether_page.json").read_text(encoding="utf-8"))
URL = "https://jobgether.com/api/v1/jobs"
TODAY = date(2026, 11, 12)
MONTH = 30 * 86400


def make_source(queries=("data analyst",), cancel=None, sleeps=None, **kw):
    settings = JobgetherSettings(enabled=True, queries=list(queries), **kw)
    sleeps = [] if sleeps is None else sleeps
    return JobgetherSource(
        settings, httpx.Client(), cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append
    )


def item(n: int, day: str = "2026-11-10") -> dict:
    return {"id": f"gen-{n}", "title": f"Analyst {n}", "company": "Fjord Labs",
            "url": f"https://jobgether.com/offer/gen-{n}", "postedAt": f"{day}T10:00:00.000Z"}  # fmt: skip


def page(jobs: list, has_more: bool) -> dict:
    return {"jobs": jobs, "pagination": {"page": 1, "limit": 25, "hasMore": has_more}}


def run(src, window=MONTH, calls=None):
    report = (lambda *a: calls.append(a)) if calls is not None else (lambda *a: None)
    return src.search_area(src.areas()[0], window, report)


# --- request_body / parse ---


def test_request_body_with_filters():
    s = JobgetherSettings(locations=["italy", " europe "], experience=["entry-level-graduate"])
    assert request_body(s, " data analyst ", 2, 25) == {
        "keyword": "data analyst", "sort": "date", "page": 2, "limit": 25,
        "locations": ["italy", "europe"], "experience": ["entry-level-graduate"],
    }  # fmt: skip


def test_request_body_without_filters_means_anywhere_and_any_level():
    assert request_body(JobgetherSettings(locations=[" "]), "pm", 1, 10) == {
        "keyword": "pm", "sort": "date", "page": 1, "limit": 10,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("38000-45000 EUR", "EUR 38,000–45,000"),
        ("400000-450000 ZAR", "ZAR 400,000–450,000"),
        ("156746.97-449402 USD", "USD 156,747–449,402"),
        ("60,000 - 80,000 gbp", "GBP 60,000–80,000"),
        ("60000 EUR", "EUR 60,000"),
        ("0-0 EUR", "0-0 EUR"),
        ("Competitive", "Competitive"),
        (None, ""),
        (55000, "55,000"),
    ],
)
def test_parse_salary(raw, expected):
    assert parse_salary(raw) == expected


def test_parse_item_full():
    job = parse_item(PAGE["jobs"][0], "data analyst", "Jobgether")
    assert job.pk == "jobgether:6b0000000000000000000001"
    assert (job.title, job.company) == ("Junior Data Analyst", "Kestrel Payments")
    assert job.location == "Italy, Spain (Full Remote)"
    assert job.url == "https://jobgether.com/offer/6b0000000000000000000001-junior-data-analyst"
    assert (job.posted, job.employment_type, job.salary) == ("2026-11-11", "Full time", "EUR 38,000–45,000")
    assert (job.keyword, job.area) == ("data analyst", "Jobgether")
    for text in ("Job functions: Data Analyst, Business Analyst", "Experience: Junior (1-2 years)",
                 "Salary: EUR 38,000–45,000"):  # fmt: skip
        assert text in job.description


def test_parse_item_keeps_unparsed_salary_text():
    assert parse_item(PAGE["jobs"][1], "k", "a").salary == "Competitive"


def test_parse_item_needs_an_id_and_coerces_fields():
    assert parse_item({"title": "No id"}, "k", "a") is None
    job = parse_item({"id": 9, "title": None, "company": ["x"], "url": "javascript:alert(1)",
                      "postedAt": "yesterday", "jobFunctions": "Analyst"}, "k", "a")  # fmt: skip
    assert job.job_id == "9"
    assert (job.title, job.company, job.url, job.posted, job.location, job.description) == ("",) * 6


def test_areas_single_area():
    src = make_source(queries=("a", "b"))
    assert src.areas() == [SearchArea("jobgether:all", "Jobgether", "jobgether", 2, {})]


# --- search_area ---


def test_request_shape(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([], False))
    run(make_source(locations=["italy"], experience=["junior-1-2-years"]))
    request = route.calls[0].request
    assert str(request.url) == URL  # filters go in the body: robots.txt disallows query strings
    assert json.loads(request.content) == {"keyword": "data analyst", "sort": "date", "page": 1, "limit": 25,
                                           "locations": ["italy"], "experience": ["junior-1-2-years"]}  # fmt: skip
    assert request.headers["User-Agent"].startswith("ai-job-scout")


def test_window_dedup_and_malformed_items(respx_mock):
    respx_mock.post(URL).respond(200, json=PAGE)
    calls = []
    res = run(make_source(), calls=calls)
    assert isinstance(res, AreaResult)
    assert [j.job_id for j in res.jobs] == ["6b0000000000000000000001", "6b0000000000000000000002"]
    assert res.jobs[0].title == "Junior Data Analyst"  # first sighting wins
    assert all(j.source == "jobgether" for j in res.jobs)
    assert (res.billed, res.complete, res.error) == (0, True, None)
    assert calls == [(0, 1, 0), (1, 1, 2)]


def test_dedup_across_queries_and_pause_between_requests(respx_mock):
    route = respx_mock.post(URL).respond(200, json=PAGE)
    sleeps = []
    res = run(make_source(queries=("data analyst", "product analyst"), sleeps=sleeps))
    assert route.call_count == 2 and sleeps == [2.0]
    assert len(res.jobs) == 2 and {j.keyword for j in res.jobs} == {"data analyst"}


def test_pages_until_max_results(respx_mock):
    route = respx_mock.post(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(25)], True)),
        httpx.Response(200, json=page([item(n) for n in range(25, 50)], True)),
    ])  # fmt: skip
    sleeps = []
    res = run(make_source(max_results_per_query=30, sleeps=sleeps))
    assert route.call_count == 2 and sleeps == [2.0]
    assert [json.loads(c.request.content)["page"] for c in route.calls] == [1, 2]
    assert len(res.jobs) == 30 and res.complete


def test_small_max_asks_for_a_small_page(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([item(n) for n in range(5)], True))
    res = run(make_source(max_results_per_query=5))
    assert json.loads(route.calls[0].request.content)["limit"] == 5
    assert route.call_count == 1 and len(res.jobs) == 5


def test_stops_when_there_is_no_more(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([item(n) for n in range(25)], False))
    res = run(make_source(max_results_per_query=200))
    assert route.call_count == 1 and len(res.jobs) == 25


def test_stops_once_a_whole_page_is_older_than_the_window(respx_mock):
    route = respx_mock.post(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(25)], True)),
        httpx.Response(200, json=page([item(n, "2026-08-01") for n in range(25, 50)], True)),
        httpx.Response(200, json=page([item(n) for n in range(50, 75)], True)),
    ])  # fmt: skip
    res = run(make_source(max_results_per_query=200))
    assert route.call_count == 2 and len(res.jobs) == 25


def test_page_cap(respx_mock):
    pages = [httpx.Response(200, json=page([item(p * 25 + n) for n in range(25)], True)) for p in range(12)]
    route = respx_mock.post(URL).mock(side_effect=pages)
    res = run(make_source(max_results_per_query=250))
    assert route.call_count == jobgether.MAX_PAGES == 10
    assert len(res.jobs) == 250 and res.complete


def test_bad_filter_value_has_a_clear_message(respx_mock):
    problem = {"status": 400, "code": "invalid_parameter", "field": "locations", "detail": "Invalid value"}
    respx_mock.post(URL).respond(400, json=problem)
    res = run(make_source(locations=["atlantis"]))
    assert res.complete is False and res.jobs == []
    assert str(res.error) == "Jobgether didn't accept a location or experience setting. Check them in Settings."


def test_rate_limit_is_retried_then_reported(respx_mock):
    route = respx_mock.post(URL).respond(429)
    sleeps = []
    res = run(make_source(sleeps=sleeps))
    assert route.call_count == 3 and sleeps == [1, 2]
    assert str(res.error) == "Jobgether asked us to slow down. It will be retried next run."


def test_error_stops_the_area_and_keeps_jobs(respx_mock):
    route = respx_mock.post(URL).mock(side_effect=[httpx.Response(200, json=PAGE), httpx.Response(404)])
    calls = []
    res = run(make_source(queries=("a", "b", "c")), calls=calls)
    assert route.call_count == 2
    assert res.complete is False and len(res.jobs) == 2
    assert "jobgether.com" not in str(res.error) and "HTTP 404" in str(res.error)
    assert calls == [(0, 3, 0), (1, 3, 2)]


def test_unreadable_answer(respx_mock):
    respx_mock.post(URL).respond(200, text="<html>maintenance</html>")
    res = run(make_source())
    assert res.complete is False and str(res.error) == "Jobgether sent an answer we couldn't read."


def test_cancel_stops_between_queries(respx_mock):
    cancel = threading.Event()
    route = respx_mock.post(URL).respond(200, json=PAGE)
    src = make_source(queries=("a", "b", "c"), cancel=cancel)
    res = src.search_area(src.areas()[0], MONTH, lambda done, total, n: cancel.set() if done == 1 else None)
    assert route.call_count == 1
    assert res.complete is False and res.error is None and len(res.jobs) == 2


def test_cancel_between_pages(respx_mock):
    cancel = threading.Event()
    route = respx_mock.post(URL).respond(200, json=page([item(n) for n in range(25)], True))
    src = make_source(cancel=cancel, max_results_per_query=200)
    src._sleep = lambda seconds: cancel.set()  # cancelled while waiting before page 2
    res = run(src)
    assert route.call_count == 1 and res.complete is False and len(res.jobs) == 25


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    real = jobgether.parse_item

    def flaky(raw, query, area):
        if raw.get("id") == "6b0000000000000000000002":
            raise ValueError("bad item")
        return real(raw, query, area)

    monkeypatch.setattr(jobgether, "parse_item", flaky)
    respx_mock.post(URL).respond(200, json=PAGE)
    res = run(make_source())
    assert [j.job_id for j in res.jobs] == ["6b0000000000000000000001"] and res.complete
