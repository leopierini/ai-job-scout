import json
import threading
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from jobscout.settings import HimalayasSettings
from jobscout.sources import SearchArea, himalayas
from jobscout.sources.himalayas import HimalayasSource, parse_item, search_url

PAGE = json.loads((Path(__file__).parent / "fixtures" / "himalayas_page.json").read_text(encoding="utf-8"))
URL = "https://himalayas.app/jobs/api/search"
TODAY = date(2026, 11, 26)
MONTH = 30 * 86400
DAY = 1795600000  # 2026-11-25


def make_source(queries=("Product Manager",), countries=(), cancel=None, sleeps=None, **kw):
    settings = HimalayasSettings(enabled=True, queries=list(queries), countries=list(countries), **kw)
    sleeps = [] if sleeps is None else sleeps
    return HimalayasSource(
        settings, httpx.Client(), cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append
    )


def item(n: int, pub: int = DAY, places=(), title: str = "Product Manager") -> dict:
    return {"title": f"{title} {n}", "companyName": "Fjord Labs", "locationRestrictions": list(places),
            "pubDate": pub, "guid": f"https://himalayas.app/companies/fjord-labs/jobs/pm-{n}",
            "applicationLink": f"https://himalayas.app/companies/fjord-labs/jobs/pm-{n}?utm_source=api"}  # fmt: skip


def page(jobs: list) -> dict:
    return {"jobs": jobs, "totalCount": len(jobs), "limit": 20, "offset": 0}


def run(src, window=MONTH, calls=None):
    report = (lambda *a: calls.append(a)) if calls is not None else (lambda *a: None)
    return src.search_area(src.areas()[0], window, report)


def params(call) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(call.request.url)).query).items()}


# --- settings / parse ---


def test_countries_are_upper_case_codes():
    assert HimalayasSettings(countries=[" it", "IT", "de", ""]).countries == ["IT", "DE"]
    with pytest.raises(ValidationError, match="Not a two-letter country code: ITALY"):
        HimalayasSettings(countries=["Italy"])


def test_search_url():
    assert search_url(" product manager ", "IT", 2) == f"{URL}?q=product+manager&sort=recent&page=2&country=IT"
    assert search_url("pm", "", 1) == f"{URL}?q=pm&sort=recent&page=1"


def test_parse_item_full():
    job = parse_item(PAGE["jobs"][0], "Associate Product Manager", "Himalayas")
    assert job.pk == "himalayas:meadowlark-agritech/associate-product-manager"
    assert (job.title, job.company) == ("Associate Product Manager", "Meadowlark Agritech")
    assert job.location == "Remote (Italy, Spain)"
    # Every Apply goes through the API's applicationLink (Himalayas' terms).
    assert job.url == "https://himalayas.app/companies/meadowlark-agritech/jobs/associate-product-manager?utm_source=api"
    assert (job.posted, job.employment_type, job.salary) == ("2026-11-25", "Full Time", "EUR 38,000–46,000 a year")
    for text in ("Open to people in: Italy, Spain", "Seniority: Entry-level", "Salary: EUR 38,000–46,000 a year",
                 "Work with growers across Southern Europe."):  # fmt: skip
        assert text in job.description


def test_parse_item_worldwide_and_long_lists():
    assert parse_item(PAGE["jobs"][1], "k", "a").location == "Remote (Worldwide)"
    job = parse_item(PAGE["jobs"][4], "k", "a")
    assert job.location == "Remote (Europe)"  # more than three European countries; the others aren't shown
    many = parse_item({"guid": "x", "locationRestrictions": [f"Country {n}" for n in range(12)]}, "k", "a")
    assert "Open to people in: 12 countries, including Country 0" in many.description


def test_parse_item_needs_an_id_and_coerces_fields():
    assert parse_item(PAGE["jobs"][5], "k", "a") is None
    job = parse_item({"guid": "odd-guid", "title": None, "applicationLink": "javascript:x", "pubDate": "soon",
                      "minSalary": "n/a", "locationRestrictions": "Italy"}, "k", "a")  # fmt: skip
    assert job.job_id == "odd-guid" and job.location == "Remote (Worldwide)"
    assert (job.title, job.url, job.posted, job.salary) == ("",) * 4


def test_areas_one_search_per_query_and_country():
    src = make_source(queries=("a", "b"), countries=("IT", "DE"))
    assert src.areas() == [SearchArea("himalayas:all", "Himalayas", "himalayas", 4, {})]
    assert make_source(queries=("a", "b")).areas()[0].searches == 2  # no country: once each


# --- search_area ---


def test_request_shape(respx_mock):
    route = respx_mock.get(URL).respond(200, json=page([]))
    run(make_source(countries=("IT",)))
    assert params(route.calls[0]) == {"q": "Product Manager", "sort": "recent", "page": "1", "country": "IT"}
    assert route.calls[0].request.headers["User-Agent"].startswith("ai-job-scout")


def test_keeps_title_matches_open_to_europe(respx_mock):
    respx_mock.get(URL).mock(side_effect=[httpx.Response(200, json=PAGE), httpx.Response(200, json=page([]))])
    calls = []
    res = run(make_source(), calls=calls)
    # "Sales Manager, SMB" only matched in its text, "Senior Product Manager" is US only, and one has no id.
    assert [j.job_id for j in res.jobs] == [
        "meadowlark-agritech/associate-product-manager",
        "kestrel-payments/product-manager-payments",
        "heron-robotics/product-manager",
    ]
    assert (res.billed, res.complete, res.error) == (0, True, None)
    assert calls == [(0, 1, 0), (1, 1, 3)]


def test_each_query_in_each_country_with_a_pause(respx_mock):
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(1)])), httpx.Response(200, json=page([])),  # Product Manager in IT
        httpx.Response(200, json=page([item(1)])), httpx.Response(200, json=page([])),  # ... in DE
        httpx.Response(200, json=page([item(2, title="APM")])), httpx.Response(200, json=page([])),
        httpx.Response(200, json=page([])),
    ])  # fmt: skip
    sleeps = []
    calls = []
    res = run(make_source(queries=("Product Manager", "APM"), countries=("IT", "DE"), sleeps=sleeps), calls=calls)
    sent = [(params(c)["q"], params(c)["country"], params(c)["page"]) for c in route.calls]
    assert sent == [
        ("Product Manager", "IT", "1"), ("Product Manager", "IT", "2"),
        ("Product Manager", "DE", "1"), ("Product Manager", "DE", "2"),
        ("APM", "IT", "1"), ("APM", "IT", "2"), ("APM", "DE", "1"),
    ]  # fmt: skip
    assert sleeps == [1.0] * 6  # between requests; the first goes at once
    assert [j.job_id for j in res.jobs] == ["fjord-labs/pm-1", "fjord-labs/pm-2"]  # one job in two countries: once
    assert [j.keyword for j in res.jobs] == ["Product Manager", "APM"]
    assert calls == [(0, 4, 0), (1, 4, 1), (2, 4, 1), (3, 4, 2), (4, 4, 2)]


def test_a_short_page_is_not_the_end(respx_mock):
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(7)])),
        httpx.Response(200, json=page([item(n) for n in range(7, 12)])),
        httpx.Response(200, json=page([])),
    ])  # fmt: skip
    res = run(make_source())
    assert route.call_count == 3 and len(res.jobs) == 12


def test_pages_until_max_results(respx_mock):
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(20)])),
        httpx.Response(200, json=page([item(n) for n in range(20, 40)])),
    ])  # fmt: skip
    res = run(make_source(max_results_per_query=30))
    assert [params(c)["page"] for c in route.calls] == ["1", "2"]
    assert len(res.jobs) == 30 and res.complete


def test_page_cap(respx_mock):
    pages = [httpx.Response(200, json=page([item(p * 20 + n) for n in range(20)])) for p in range(7)]
    route = respx_mock.get(URL).mock(side_effect=pages)
    res = run(make_source(max_results_per_query=100))
    assert route.call_count == himalayas.MAX_PAGES == 5
    assert len(res.jobs) == 100 and res.complete


def test_stops_once_a_whole_page_is_older_than_the_window(respx_mock):
    old = 1785000000  # 2026-07-25
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n) for n in range(20)])),
        httpx.Response(200, json=page([item(n, pub=old) for n in range(20, 40)])),
        httpx.Response(200, json=page([item(n) for n in range(40, 60)])),
    ])  # fmt: skip
    res = run(make_source(max_results_per_query=100))
    assert route.call_count == 2 and len(res.jobs) == 20


def test_jobs_outside_europe_or_the_search_dont_count_as_old(respx_mock):
    # A page of US-only jobs in the window is not "older than the window": the next page is read.
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=page([item(n, places=["United States"]) for n in range(20)])),
        httpx.Response(200, json=page([item(20, places=["Italy"])])),
        httpx.Response(200, json=page([])),
    ])  # fmt: skip
    res = run(make_source())
    assert route.call_count == 3 and [j.job_id for j in res.jobs] == ["fjord-labs/pm-20"]


def test_unknown_country_has_a_clear_message(respx_mock):
    respx_mock.get(URL).respond(400, json={"ok": False, "errors": "Invalid country"})
    res = run(make_source(countries=("XX",)))
    assert res.complete is False and res.jobs == []
    assert str(res.error) == "Himalayas didn't accept the country XX. Check it in Settings."


def test_rate_limit_is_retried_then_reported(respx_mock):
    route = respx_mock.get(URL).respond(429)
    sleeps = []
    res = run(make_source(sleeps=sleeps))
    assert route.call_count == 3 and sleeps == [1, 2]
    assert str(res.error) == "Himalayas asked us to slow down. It will be retried next run."


def test_error_stops_the_area_and_keeps_jobs(respx_mock):
    route = respx_mock.get(URL).mock(side_effect=[
        httpx.Response(200, json=PAGE), httpx.Response(200, json=page([])), httpx.Response(404),
    ])  # fmt: skip
    calls = []
    res = run(make_source(queries=("Product Manager", "APM", "Product Owner")), calls=calls)
    assert route.call_count == 3
    assert res.complete is False and len(res.jobs) == 3
    assert "himalayas.app" not in str(res.error) and "HTTP 404" in str(res.error)
    assert calls == [(0, 3, 0), (1, 3, 3)]


def test_unreadable_answer(respx_mock):
    respx_mock.get(URL).respond(200, text="<html>maintenance</html>")
    res = run(make_source())
    assert res.complete is False and str(res.error) == "Himalayas sent an answer we couldn't read."


def test_cancel_between_pages(respx_mock):
    cancel = threading.Event()
    route = respx_mock.get(URL).respond(200, json=page([item(n) for n in range(20)]))
    src = make_source(cancel=cancel, max_results_per_query=100)
    src._sleep = lambda seconds: cancel.set()  # cancelled while waiting before page 2
    res = run(src)
    assert route.call_count == 1
    assert res.complete is False and len(res.jobs) == 20


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    real = himalayas.parse_item

    def flaky(raw, query, area):
        if raw.get("companyName") == "Kestrel Payments":
            raise ValueError("bad item")
        return real(raw, query, area)

    monkeypatch.setattr(himalayas, "parse_item", flaky)
    respx_mock.get(URL).mock(side_effect=[httpx.Response(200, json=PAGE), httpx.Response(200, json=page([]))])
    res = run(make_source())
    assert len(res.jobs) == 2 and res.complete


def test_an_unknown_country_skips_only_that_country(respx_mock):
    def answer(request):
        if request.url.params.get("country") == "XX":
            return httpx.Response(400, json={"ok": False, "errors": "Invalid country"})
        return httpx.Response(200, json=page([item(1)]) if request.url.params.get("page") == "1" else page([]))

    route = respx_mock.get(URL).mock(side_effect=answer)
    res = run(make_source(queries=("Product Manager", "APM"), countries=("XX", "IT")))
    countries = [params(c).get("country") for c in route.calls]
    assert countries.count("XX") == 1  # tried once, then its other searches are skipped
    assert "IT" in countries and len(res.jobs) == 1
    assert res.complete is False and "country XX" in str(res.error)


def test_a_400_with_no_country_is_not_blamed_on_a_country(respx_mock):
    respx_mock.get(URL).respond(400, json={"ok": False, "errors": "Bad page"})
    res = run(make_source())
    assert res.complete is False and "country" not in str(res.error)
