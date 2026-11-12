import json
import threading
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from jobscout.settings import BuiltInEuropeSettings
from jobscout.sources import AreaResult, SearchArea, builtineurope
from jobscout.sources.builtineurope import BuiltInEuropeSource, parse_item, search_query

PAGE = json.loads((Path(__file__).parent / "fixtures" / "builtineurope_page.json").read_text(encoding="utf-8"))
URL = "https://api.builtineurope.com/search"
TODAY = date(2026, 11, 12)
MONTH = 30 * 86400


def make_source(queries=("data analyst",), countries=("italy",), cancel=None, sleeps=None, **kw):
    settings = BuiltInEuropeSettings(enabled=True, queries=list(queries), countries=list(countries), **kw)
    sleeps = [] if sleeps is None else sleeps
    return BuiltInEuropeSource(
        settings, httpx.Client(), cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append
    )


def item(n: int, day: str = "2026-11-10") -> dict:
    seen = int(datetime.fromisoformat(f"{day}T09:00:00+00:00").astimezone(UTC).timestamp())
    return {"id": f"gen-{n}", "title_raw": f"Analyst {n}", "company_display_name": "Kestrel Payments",
            "posting_url": f"https://jobs.example.com/kestrel/{n}", "first_seen": seen}  # fmt: skip


def page(results: list, total: int) -> dict:
    return {"total": total, "total_jobs": 16878, "page": 1, "per_page": 50, "results": results}


def run(src, window=MONTH, calls=None):
    report = (lambda *a: calls.append(a)) if calls is not None else (lambda *a: None)
    return src.search_area(src.areas()[0], window, report)


# --- query / areas ---


def test_search_query_adds_the_country_as_the_site_does():
    assert search_query("data analyst", "italy") == "data analyst in Italy"
    assert search_query("  ", "united kingdom") == "all jobs in United Kingdom"
    assert search_query(" AI engineer ", "") == "AI engineer"


def test_areas_one_per_country():
    src = make_source(queries=("a", "b"), countries=("italy", "United Kingdom", " italy "))
    assert src.areas() == [
        SearchArea("builtineurope:italy", "Italy", "builtineurope", 2, {"country": "italy"}),
        SearchArea("builtineurope:united kingdom", "United Kingdom", "builtineurope", 2, {"country": "united kingdom"}),
    ]


def test_areas_all_of_europe_without_countries():
    src = make_source(queries=("a",), countries=())
    assert src.areas() == [SearchArea("builtineurope:europe", "Europe", "builtineurope", 1, {"country": ""})]


# --- parse_item ---


def test_parse_item_full():
    job = parse_item(PAGE["results"][0], "data analyst", "Italy")
    assert job.pk == "builtineurope:QkUtS2VzdHJlbC0wMDE"
    assert (job.title, job.company) == ("Data Analyst - Growth", "Kestrel Payments")
    assert job.location == "Milan, Italy (hybrid)"
    assert job.url == "https://jobs.example.com/kestrel-payments/data-analyst-growth"
    assert job.posted == "2026-11-03"
    assert job.salary == "EUR 43,000–50,000 a year"
    assert (job.keyword, job.area) == ("data analyst", "Italy")
    for text in ("Kestrel Payments (financial services, 201-500 employees)", "Work policy: hybrid",
                 "Skills: sql, dbt, looker", "Salary: EUR 43,000–50,000 a year"):  # fmt: skip
        assert text in job.description


def test_parse_item_country_only_and_no_salary():
    job = parse_item(PAGE["results"][1], "k", "Italy")
    assert job.location == "Italy (remote)"
    assert job.salary == ""
    assert "Seniority: junior" in job.description


def test_parse_item_fallbacks():
    job = parse_item(PAGE["results"][6], "k", "Italy")
    assert (job.title, job.company) == ("Part-Time Research Assistant", "Merlin Analytics")
    assert job.location == "Rome, Lazio, Italy"
    assert (job.url, job.posted) == ("", "")
    assert job.salary == "EUR 18–22 an hour"


def test_parse_item_needs_an_id_and_coerces_fields():
    assert parse_item({"title_raw": "No id"}, "k", "a") is None
    job = parse_item({"id": 7, "title_raw": ["x"], "company_display_name": {"a": 1}, "location": "Milan",
                      "skills": "sql", "first_seen": "soon", "salary_min": "lots"}, "k", "a")  # fmt: skip
    assert job.job_id == "7"
    assert (job.title, job.company, job.location, job.posted, job.salary) == ("", "", "", "", "")


def test_parse_item_remote_without_place():
    job = parse_item({"id": "1", "remote_work_policy": "remote"}, "k", "a")
    assert job.location == "Remote"


# --- search_area ---


def test_request_shape(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([], 0))
    run(make_source(queries=("AI engineer",), countries=("united kingdom",)))
    request = route.calls[0].request
    assert json.loads(request.content) == {"query": "AI engineer in United Kingdom", "page": 1, "per_page": 50}
    assert request.headers["User-Agent"].startswith("ai-job-scout")


def test_window_dedup_and_malformed_items(respx_mock):
    respx_mock.post(URL).respond(200, json=PAGE)
    calls = []
    res = run(make_source(), calls=calls)
    assert isinstance(res, AreaResult)
    assert [j.job_id for j in res.jobs] == ["QkUtS2VzdHJlbC0wMDE", "QkUtRmpvcmQtMDAy", "QkUtTWVybGluLTAwNA"]
    assert res.jobs[0].title == "Data Analyst - Growth"  # first sighting wins
    assert all(j.source == "builtineurope" for j in res.jobs)
    assert (res.billed, res.complete, res.error) == (0, True, None)
    assert calls == [(0, 1, 0), (1, 1, 3)]


def test_a_wider_window_keeps_older_jobs(respx_mock):
    respx_mock.post(URL).respond(200, json=PAGE)
    res = run(make_source(), window=200 * 86400)
    assert "QkUtT2xkLTAwMw" in [j.job_id for j in res.jobs]


def test_dedup_across_queries_and_pause_between_requests(respx_mock):
    route = respx_mock.post(URL).respond(200, json=PAGE)
    sleeps = []
    res = run(make_source(queries=("data analyst", "product analyst"), sleeps=sleeps))
    assert route.call_count == 2 and sleeps == [1.0]
    assert len(res.jobs) == 3 and {j.keyword for j in res.jobs} == {"data analyst"}
    assert json.loads(route.calls[1].request.content)["query"] == "product analyst in Italy"


def test_pages_until_max_results(respx_mock):
    route = respx_mock.post(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(50)], 300)),
        httpx.Response(200, json=page([item(n) for n in range(50, 100)], 300)),
    ])  # fmt: skip
    sleeps = []
    res = run(make_source(max_results_per_query=60, sleeps=sleeps))
    assert route.call_count == 2 and sleeps == [1.0]
    assert [json.loads(c.request.content)["page"] for c in route.calls] == [1, 2]
    assert len(res.jobs) == 60 and res.complete


def test_small_max_asks_for_a_small_page(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([item(n) for n in range(5)], 300))
    res = run(make_source(max_results_per_query=5))
    assert json.loads(route.calls[0].request.content)["per_page"] == 5
    assert route.call_count == 1 and len(res.jobs) == 5


def test_stops_when_the_total_is_reached(respx_mock):
    route = respx_mock.post(URL).respond(200, json=page([item(n) for n in range(50)], 50))
    res = run(make_source(max_results_per_query=200))
    assert route.call_count == 1 and len(res.jobs) == 50


def test_page_cap_when_every_job_is_too_old(respx_mock):
    pages = [httpx.Response(200, json=page([item(p * 50 + n, "2026-01-01") for n in range(50)], 9999))
             for p in range(12)]  # fmt: skip
    route = respx_mock.post(URL).mock(side_effect=pages)
    res = run(make_source(max_results_per_query=500))
    assert route.call_count == builtineurope.MAX_PAGES == 10
    assert res.jobs == [] and res.complete


def test_error_stops_the_area_and_keeps_jobs(respx_mock):
    route = respx_mock.post(URL).mock(side_effect=[httpx.Response(200, json=PAGE), httpx.Response(400)])
    calls = []
    res = run(make_source(queries=("a", "b", "c")), calls=calls)
    assert route.call_count == 2
    assert res.complete is False and len(res.jobs) == 3
    assert str(res.error) == "Built in Europe answered with an error (HTTP 400)."
    assert "builtineurope.com" not in str(res.error)
    assert calls == [(0, 3, 0), (1, 3, 3)]


def test_unreachable(respx_mock):
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("boom https://api.builtineurope.com"))
    res = run(make_source())
    assert res.complete is False and res.jobs == []
    assert str(res.error) == "Built in Europe couldn't be reached. Check your internet connection."


def test_unreadable_answer(respx_mock):
    respx_mock.post(URL).respond(200, json={"oops": 1})
    res = run(make_source())
    assert res.complete is False and str(res.error) == "Built in Europe sent an answer we couldn't read."


def test_cancel_stops_between_queries(respx_mock):
    cancel = threading.Event()
    route = respx_mock.post(URL).respond(200, json=PAGE)
    src = make_source(queries=("a", "b", "c"), cancel=cancel)
    res = src.search_area(src.areas()[0], MONTH, lambda done, total, n: cancel.set() if done == 1 else None)
    assert route.call_count == 1
    assert res.complete is False and res.error is None and len(res.jobs) == 3


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    real = builtineurope.parse_item

    def flaky(raw, query, area):
        if raw.get("id") == "QkUtRmpvcmQtMDAy":
            raise ValueError("bad item")
        return real(raw, query, area)

    monkeypatch.setattr(builtineurope, "parse_item", flaky)
    respx_mock.post(URL).respond(200, json=PAGE)
    res = run(make_source())
    assert [j.job_id for j in res.jobs] == ["QkUtS2VzdHJlbC0wMDE", "QkUtTWVybGluLTAwNA"] and res.complete


def test_pause_carries_over_between_areas(respx_mock):
    respx_mock.post(URL).respond(200, json=page([], 0))
    sleeps = []
    src = make_source(countries=("italy", "france"), sleeps=sleeps)
    for area in src.areas():
        src.search_area(area, MONTH, lambda *a: None)
    assert sleeps == [1.0]
