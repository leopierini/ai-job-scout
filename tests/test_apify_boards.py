"""The startup job boards searched through Apify actors: Welcome to the Jungle, Wellfound, YC and the ATS index."""

import json
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobscout.costs import board_searches
from jobscout.settings import (
    AtsIndexSettings,
    WellfoundSettings,
    WttjSettings,
    YcSettings,
)
from jobscout.sources import AreaResult, SearchArea, atsindex, wellfound, wttj, yc
from jobscout.sources._apify_board import country_code, humanize, is_remote
from jobscout.sources.apify import ApifyClient
from jobscout.sources.atsindex import AtsIndexSource
from jobscout.sources.wellfound import WellfoundSource, location_slug
from jobscout.sources.wttj import WttjSource, date_posted
from jobscout.sources.yc import YcSource

FIXTURES = Path(__file__).parent / "fixtures"
DAY = 86400
TODAY = date(2026, 11, 12)


def items(name):
    return json.loads((FIXTURES / f"{name}_items.json").read_text(encoding="utf-8"))


WTTJ, WELLFOUND, YC, ATS = items("wttj"), items("wellfound"), items("yc"), items("atsindex")

# name -> (source class, settings class, module, fixture items)
BOARDS = {
    "wttj": (WttjSource, WttjSettings, wttj, WTTJ),
    "wellfound": (WellfoundSource, WellfoundSettings, wellfound, WELLFOUND),
    "yc": (YcSource, YcSettings, yc, YC),
    "atsindex": (AtsIndexSource, AtsIndexSettings, atsindex, ATS),
}


def run_url(actor):
    return rf".*acts/{actor}/run-sync-get-dataset-items.*"


def make(name, queries=("Product Manager",), locations=("Italy",), cancel=None, **kw):
    cls, settings_cls, _, _ = BOARDS[name]
    settings = settings_cls(enabled=True, queries=list(queries), locations=list(locations), **kw)
    apify = ApifyClient("tok-secret-123", httpx.Client(), sleep=lambda s: None)
    return cls(settings, apify, cancel or threading.Event(), today=lambda: TODAY)


def mock_run(respx_mock, name, json_body=None, **kw):
    actor = BOARDS[name][1]().actor
    route = respx_mock.post(url__regex=run_url(actor))
    return route.respond(200, json=BOARDS[name][3] if json_body is None else json_body, **kw)


def search(src, window=3 * DAY, progress=None):
    return src.search_area(src.areas()[0], window, progress or (lambda *a: None))


def bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


# --- shared helpers ---


@pytest.mark.parametrize(
    ("text", "code"),
    [("it", "IT"), (" IT ", "IT"), ("UK", "GB"), ("EU", ""), ("Italy", ""), ("I1", ""), ("", "")],
)
def test_country_code(text, code):
    assert country_code(text) == code


def test_is_remote_and_humanize():
    assert is_remote(" Remote ") and is_remote("anywhere") and not is_remote("Remote, Italy")
    assert humanize("full_time") == "Full time" and humanize("FULL-TIME") == "Full time"
    assert humanize("Full-time") == "Full-time" and humanize(None) == "" and humanize(3) == "3"


# --- every board: areas, billing, errors, cancel ---


@pytest.mark.parametrize("name", BOARDS)
def test_areas_one_per_location(name):
    src = make(name, queries=("a", "b", "c"), locations=("Italy", "Berlin"))
    areas = src.areas()
    assert areas == [
        SearchArea(f"{name}:Italy", "Italy", name, 3, {"location": "Italy"}),
        SearchArea(f"{name}:Berlin", "Berlin", name, 3, {"location": "Berlin"}),
    ]
    # costs.board_searches counts the same searches the areas run
    assert sum(a.searches for a in areas) == board_searches(src._settings)


@pytest.mark.parametrize("name", BOARDS)
def test_no_locations_means_one_area_everywhere(name):
    src = make(name, queries=("a", "b"), locations=())
    assert src.areas() == [SearchArea(f"{name}:all", "All locations", name, 2, {"location": ""})]
    assert board_searches(src._settings) == 2


@pytest.mark.parametrize("name", BOARDS)
def test_one_call_per_query_billed_for_every_item(respx_mock, name):
    route = mock_run(respx_mock, name)
    src = make(name, queries=("Product Manager", "Product Owner"))
    calls = []
    res = search(src, progress=lambda *a: calls.append(a))
    assert isinstance(res, AreaResult) and res.complete is True and res.error is None
    assert route.call_count == 2
    assert res.billed == 2 * len(BOARDS[name][3])  # every returned item, duplicates and junk included
    assert len({j.job_id for j in res.jobs}) == len(res.jobs)  # deduplicated within the area
    assert all(j.source == name and j.area == "Italy" for j in res.jobs)
    assert all(j.keyword == "Product Manager" for j in res.jobs)  # first sighting wins
    assert calls[0] == (0, 2, 0) and calls[-1][:2] == (2, 2)
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok-secret-123"
    assert "tok-secret-123" not in str(route.calls[0].request.url)


@pytest.mark.parametrize("name", BOARDS)
def test_no_queries_means_no_calls(respx_mock, name):
    route = mock_run(respx_mock, name)
    calls = []
    res = make(name, queries=()).search_area(SearchArea(f"{name}:all", "All locations", name, 0), DAY,
                                              lambda *a: calls.append(a))
    assert route.call_count == 0 and calls == [(0, 0, 0)]
    assert res == AreaResult(jobs=[], billed=0, complete=True)


@pytest.mark.parametrize("name", BOARDS)
def test_stops_at_first_apify_error_keeping_jobs_so_far(respx_mock, name):
    actor = BOARDS[name][1]().actor
    route = respx_mock.post(url__regex=run_url(actor)).mock(
        side_effect=[httpx.Response(200, json=BOARDS[name][3]), httpx.Response(403, text="Monthly usage limit")]
    )
    res = search(make(name, queries=("a b", "c", "d")))
    assert route.call_count == 2  # the third query never ran
    assert res.complete is False and res.error.kind == "limit"
    assert res.billed == len(BOARDS[name][3])


@pytest.mark.parametrize("name", BOARDS)
def test_cancel_stops_between_searches(respx_mock, name):
    cancel = threading.Event()
    route = mock_run(respx_mock, name)
    src = make(name, queries=("Product Manager", "b", "c"), cancel=cancel)
    res = search(src, progress=lambda done, total, n: cancel.set() if done == 1 else None)
    assert route.call_count == 1 and res.complete is False and res.error is None and res.jobs


@pytest.mark.parametrize("name", BOARDS)
def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch, name):
    module, fixture = BOARDS[name][2], BOARDS[name][3]
    real = module.parse_item
    bad = fixture[1]

    def flaky(item, query, area):
        if item is bad or item == bad:
            raise ValueError("bad item")
        return real(item, query, area)

    mock_run(respx_mock, name)
    everything = [j.job_id for j in search(make(name)).jobs]
    monkeypatch.setattr(module, "parse_item", flaky)
    res = search(make(name))
    assert res.complete is True and res.billed == len(fixture)
    skipped = real(bad, "q", "a").job_id
    assert [j.job_id for j in res.jobs] == [i for i in everything if i != skipped] and res.jobs


@pytest.mark.parametrize("name", BOARDS)
def test_unexpected_response_shape_is_an_error(respx_mock, name):
    mock_run(respx_mock, name, json_body={"oops": 1})
    res = search(make(name))
    assert res.complete is False and res.error.kind == "other"


# --- Welcome to the Jungle ---


def test_wttj_payload_country_code_and_date(respx_mock):
    route = mock_run(respx_mock, "wttj", json_body=[])
    src = make("wttj", locations=("it",), max_results_per_search=7)
    search(src, window=2 * DAY)
    assert bodies(route) == [
        {
            "query": "Product Manager",
            "searchOnlyInJobTitle": True,
            "sortBy": "date",
            "datePosted": "3",
            "includeDetails": True,
            "maxItems": 7,
            "countryCode": "IT",
        }
    ]


def test_wttj_payload_place_remote_and_nowhere(respx_mock):
    route = mock_run(respx_mock, "wttj", json_body=[])
    for location in ("Milan", "Remote", ""):
        src = make("wttj", locations=(location,) if location else ())
        search(src)
    sent = bodies(route)
    assert sent[0]["location"] == "Milan" and "countryCode" not in sent[0]
    assert sent[1]["remote"] == ["fulltime"] and "location" not in sent[1]
    assert not {"location", "countryCode", "remote"} & set(sent[2])


@pytest.mark.parametrize(
    ("days", "value"), [(0.5, "1"), (1, "1"), (2, "3"), (4, "7"), (10, "14"), (15, "30"), (30, "30"), (31, "any")]
)
def test_wttj_date_posted(days, value):
    assert date_posted(int(days * DAY)) == value


def test_wttj_parse_full():
    job = wttj.parse_item(WTTJ[0], "Product Manager", "Italy")
    assert job.pk == "wttj:6f1c2a90-0000-4000-8000-000000000001"
    assert (job.title, job.company) == ("Junior Product Manager", "Fjord Labs")
    assert job.location == "Milan, IT; Turin, IT (Partly remote)"
    assert job.url.startswith("https://www.welcometothejungle.com/en/companies/fjord-labs/jobs/")
    assert job.description == (
        "Shape the roadmap with our users & engineers.\n\n- Run discovery interviews\n\n- Write specs"
        "\n\nOne year of product experience."
    )
    assert job.posted == "2026-11-08"
    assert (job.keyword, job.area) == ("Product Manager", "Italy")
    assert job.employment_type == "Full time"
    assert job.salary == "EUR 32,000–38,000 a year"


def test_wttj_parse_fallbacks_and_odd_fields():
    job = wttj.parse_item(WTTJ[1], "q", "a")
    assert job.job_id == "product-owner-intern_paris_BIRCH_y2"  # the slug from the link
    assert job.location == "Remote" and job.posted == "" and job.salary == ""
    assert job.description == "Help the team ship a better onboarding."
    assert job.employment_type == "Internship"
    assert wttj.parse_item(WTTJ[3], "q", "a") is None  # no id and no link
    odd = wttj.parse_item(WTTJ[5], "q", "a")
    assert (odd.url, odd.location, odd.company, odd.posted) == ("", "", "", "")
    assert odd.employment_type == "7"
    assert odd.salary == "EUR from 45,000 a month"


def test_wttj_long_window_is_filtered_here(respx_mock):
    route = mock_run(respx_mock, "wttj", json_body=[
        {"id": "new", "publishedAt": "2026-10-01T00:00:00Z"},
        {"id": "old", "publishedAt": "2026-09-01T00:00:00Z"},
        {"id": "undated"},
    ])
    res = search(make("wttj"), window=45 * DAY)
    assert bodies(route)[0]["datePosted"] == "any"
    assert [j.job_id for j in res.jobs] == ["new", "undated"] and res.billed == 3


def test_wttj_short_window_trusts_the_actor(respx_mock):
    mock_run(respx_mock, "wttj", json_body=[{"id": "old", "publishedAt": "2026-01-01T00:00:00Z"}])
    assert [j.job_id for j in search(make("wttj"), window=7 * DAY).jobs] == ["old"]


def test_wttj_description_is_cut():
    job = wttj.parse_item({"id": "1", "description": "x" * 7000}, "q", "a")
    assert len(job.description) == 6000


# --- Wellfound ---


@pytest.mark.parametrize(
    ("text", "slug"), [("Berlin", "berlin"), (" New York ", "new-york"), ("San Francisco Bay Area", "san-francisco-bay-area"),
                       ("Zürich", "z-rich"), ("", "")],
)
def test_wellfound_location_slug(text, slug):
    assert location_slug(text) == slug


def test_wellfound_payload(respx_mock):
    route = mock_run(respx_mock, "wellfound", json_body=[])
    src = make("wellfound", locations=("New York", "Remote"), max_results_per_search=12)
    for area in src.areas():
        src.search_area(area, 2 * DAY + 30, lambda *a: None)
    search(make("wellfound", locations=()))
    sent = bodies(route)
    assert sent[0] == {
        "query": "Product Manager",
        "maxResults": 12,
        "maxAgeMinutes": 2 * 24 * 60 + 1,
        "enrichDetail": False,
        "location": ["new-york"],
    }
    assert sent[1]["remote"] is True and "location" not in sent[1]
    assert "location" not in sent[2] and "remote" not in sent[2]


def test_wellfound_parse_full():
    job = wellfound.parse_item(WELLFOUND[0], "Product Manager", "Berlin")
    assert job.pk == "wellfound:5550001"
    assert (job.title, job.company) == ("Founding Product Manager", "Example Robotics")
    assert job.location == "Berlin, Munich (Hybrid)"
    assert job.url == "https://wellfound.com/jobs/5550001-founding-product-manager"
    assert job.description == "Founding PM\n\nOwn the product from zero to one."
    assert job.posted == "2026-11-10"
    assert job.employment_type == "Full time"
    assert job.salary == "€55k – €70k • 0.1% – 0.5%"  # the board's own text: it names the currency


def test_wellfound_parse_structured_salary_and_fallbacks():
    job = wellfound.parse_item(WELLFOUND[1], "q", "a")
    assert job.salary == "EUR 1,500–2,000 a month"
    assert job.location == "Remote" and job.posted == "2026-11-09"
    assert job.description == "Analyse funnels and build dashboards."
    assert job.employment_type == "Internship"
    from_link = wellfound.parse_item(WELLFOUND[2], "q", "a")
    assert from_link.job_id == "5550003" and from_link.location == "London"  # "yes" is not a remote flag
    assert wellfound.parse_item(WELLFOUND[4], "q", "a") is None


def test_wellfound_salary_without_currency_or_text():
    assert wellfound.parse_item({"id": "1", "salaryMin": 50000}, "q", "a").salary == "from 50,000"


# --- Y Combinator ---


def test_yc_payload(respx_mock):
    route = mock_run(respx_mock, "yc", json_body=[])
    src = make("yc", locations=("Europe", "Remote"), max_results_per_search=500)
    for area in src.areas():
        src.search_area(area, 15 * DAY, lambda *a: None)
    search(make("yc", queries=("  ",), locations=()), window=3600)
    sent = bodies(route)
    assert sent[0] == {
        "schemaVersion": "nomad-agent-simple-inventory-search-v2",
        "queries": ["Product Manager"],
        "locations": ["Europe"],
        "locationTypes": [],
        "employmentTypes": [],
        "postedWithin": "15d",
        "maxItems": 200,  # the actor's ceiling
        "dedupe": {"enabled": False, "key": ""},
    }
    assert sent[1]["locations"] == [] and sent[1]["locationTypes"] == ["remote"]
    assert sent[2]["queries"] == [] and sent[2]["locations"] == [] and sent[2]["postedWithin"] == "1d"


def test_yc_parse_full():
    job = yc.parse_item(YC[0], "Product Manager", "Europe")
    assert job.pk == "yc:120001"
    assert (job.title, job.company) == ("Product Manager", "Example Robotics")
    assert job.location == "Amsterdam, North Holland, Netherlands (Hybrid)"
    assert job.url == "https://www.workatastartup.com/jobs/120001"
    assert job.description == "Own the planning product."
    assert job.posted == "2026-11-07"
    assert job.employment_type == "Full-time"
    assert job.salary == "€60K - €80K"


def test_yc_parse_fallbacks():
    job = yc.parse_item(YC[1], "q", "a")
    assert job.location == "Europe (Remote)" and job.employment_type == "Full-time, Contract" and job.salary == ""
    assert yc.parse_item(YC[2], "q", "a").location == "Remote"  # not "Remote (Remote)"
    assert yc.parse_item(YC[3], "q", "a") is None


def test_yc_keeps_only_titles_that_name_the_query(respx_mock):
    mock_run(respx_mock, "yc")
    res = search(make("yc", queries=("product manager",)))
    # "Founding Engineer" only mentions a product manager in its description
    assert [j.job_id for j in res.jobs] == ["120001", "120003"] and res.billed == 4


# --- ATS index ---


@pytest.mark.parametrize(
    ("days", "time_range", "posted_within"),
    [(1, "24h", None), (3, "7d", None), (7, "7d", None), (14, "14d", None), (15, "all", 15), (60, "all", 60)],
)
def test_atsindex_window(respx_mock, days, time_range, posted_within):
    route = mock_run(respx_mock, "atsindex", json_body=[])
    search(make("atsindex"), window=days * DAY)
    body = bodies(route)[0]
    assert body["timeRange"] == time_range and body.get("postedWithinDays") == posted_within


def test_atsindex_payload_locations(respx_mock):
    route = mock_run(respx_mock, "atsindex", json_body=[])
    src = make("atsindex", locations=("IT", "uk", "Europe"), max_results_per_search=40)
    for area in src.areas():
        src.search_area(area, DAY, lambda *a: None)
    search(make("atsindex", queries=(" ",), locations=()), window=DAY)
    sent = bodies(route)
    assert sent[0] == {
        "keywords": ["Product Manager"],
        "timeRange": "24h",
        "includeDescription": True,
        "maxJobs": 40,
        "countries": ["IT"],
    }
    assert sent[1]["countries"] == ["GB"]
    assert sent[2]["locations"] == ["Europe"] and "countries" not in sent[2]
    assert sent[3]["keywords"] == [] and not {"locations", "countries"} & set(sent[3])


def test_atsindex_parse_full():
    job = atsindex.parse_item(ATS[0], "Product Manager", "Italy")
    assert job.pk == "atsindex:personio:fjord labs:88001"
    assert (job.title, job.company) == ("Junior Product Manager", "Fjord Labs")
    assert job.location == "Milan (Hybrid)"
    assert job.url == "https://fjord-labs.jobs.personio.de/job/88001"
    assert job.description == "Shape the roadmap of our planning tools. R&D budget included."
    assert job.posted == "2026-11-09"
    assert job.employment_type == "Full time"
    assert job.salary == "EUR 35,000–42,000 a year"


def test_atsindex_parse_fallbacks():
    job = atsindex.parse_item(ATS[1], "q", "a")
    assert job.job_id == "teamtailor:tt-77" and job.company == "example robotics"
    assert job.location == "Remote"  # not "Remote (Remote)"
    assert job.salary == "Competitive pay plus stock options"
    assert job.posted == "2026-11-11"  # when the index first saw it
    assert job.employment_type == "" and job.description == ""
    odd = atsindex.parse_item(ATS[2], "q", "a")
    assert odd.job_id == "lone-1" and odd.location == "Turin, Italy (Remote)" and odd.salary == "" and odd.url == ""
    assert atsindex.parse_item(ATS[3], "q", "a") is None


# --- settings defaults ---


def test_default_actors_and_prices():
    assert (WttjSettings().actor, WttjSettings().price_per_result) == ("clearpath~welcome-to-the-jungle-jobs-api", 0.003)
    assert (WellfoundSettings().actor, WellfoundSettings().price_per_result) == ("blackfalcondata~wellfound-scraper", 0.003)
    assert (YcSettings().actor, YcSettings().price_per_result) == ("nomad-agent~ycombinator-was-scraper", 0.0001)
    assert (AtsIndexSettings().actor, AtsIndexSettings().price_per_result) == ("jtpalms~ats-jobs-index", 0.005)
