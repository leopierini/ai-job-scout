import json
import threading
from pathlib import Path

import httpx
import pytest

from jobscout.settings import IndeedSettings
from jobscout.sources import AreaResult, SearchArea
from jobscout.sources.apify import ApifyClient
from jobscout.sources.indeed import IndeedSource, parse_item, query_terms, title_matches

ITEMS = json.loads((Path(__file__).parent / "fixtures" / "indeed_items.json").read_text(encoding="utf-8"))
RUN = r".*acts/borderline~indeed-scraper/run-sync-get-dataset-items.*"


def make_source(queries=("Product Manager",), cancel=None, **kw):
    kw.setdefault("countries", ["ch", "uk"])
    settings = IndeedSettings(enabled=True, queries=list(queries), **kw)
    apify = ApifyClient("tok-secret-123", httpx.Client(), sleep=lambda s: None)
    return IndeedSource(settings, apify, cancel or threading.Event())


def first_area(src):
    return src.areas()[0]


def bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


# --- query_terms / title_matches ---


def test_query_terms():
    assert query_terms('title:("Product Manager" OR "Product Owner" OR Intern)') == [
        "product manager",
        "product owner",
        "intern",
    ]


def test_query_terms_bare_words_operators_and_duplicates():
    assert query_terms("Title:Intern AND (Trainee OR intern) NOT Senior") == ["intern", "trainee", "senior"]
    assert query_terms("") == [] and query_terms('title:() OR AND') == []


def test_title_matches_whole_words_and_phrases():
    terms = ["product manager", "intern"]
    assert title_matches("Associate Product Manager (m/f/d)", terms)
    assert title_matches("Praktikum: INTERN, Data", terms)
    assert not title_matches("Internal Auditor", terms)  # not a whole word
    assert not title_matches("Product Marketing Manager", terms)
    assert title_matches("Café Manager", ["cafe manager"])  # accents folded
    assert not title_matches("Anything", [])


def test_title_filter_drops_unrelated(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(queries=('title:("Product Manager" OR "Product Owner")',), job_types=["internship"])
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002"]  # "Warehouse Operative" dropped
    assert res.billed == 4  # still billed for every returned item


def test_empty_query_terms_let_everything_through(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(queries=("   ",), job_types=["internship"])
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002", "ind-003"]


# --- areas / payload ---


def test_areas_one_per_country():
    src = make_source(queries=("a", "b"), job_types=["internship", "fulltime"], countries=["ch", "uk"])
    areas = src.areas()
    assert [a.key for a in areas] == ["indeed:ch", "indeed:uk"]
    assert areas[0] == SearchArea("indeed:ch", "CH", "indeed", 4, {"country": "ch"})


def test_no_queries_means_no_calls(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(queries=())
    calls = []
    res = src.search_area(first_area(src), 86400, lambda *a: calls.append(a))
    assert route.call_count == 0 and calls == [(0, 0, 0)]
    assert res == AreaResult(jobs=[], billed=0, complete=True)


def test_one_call_per_job_type(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    src = make_source(queries=("Product Manager", "Product Owner"), job_types=["internship", "fulltime"])
    src.search_area(first_area(src), 86400, lambda *a: None)
    assert route.call_count == 4  # 2 queries x 2 job types
    sent = bodies(route)
    assert [(b["query"], b["jobType"]) for b in sent] == [
        ("Product Manager", "internship"),
        ("Product Manager", "fulltime"),
        ("Product Owner", "internship"),
        ("Product Owner", "fulltime"),
    ]
    assert all(isinstance(b["jobType"], str) for b in sent)


def test_payload_fields(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    src = make_source(max_rows_per_query=7, job_types=["internship"])
    src.search_area(first_area(src), 86400, lambda *a: None)
    assert bodies(route) == [
        {
            "country": "ch",
            "query": "Product Manager",
            "jobType": "internship",
            "fromDays": "1",
            "maxRows": 7,
            "enableUniqueJobs": True,
        }
    ]
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok-secret-123"


def test_from_days_mapping_in_payload(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    src = make_source(job_types=["internship"])
    area = first_area(src)
    for seconds in (2 * 86400, 5 * 86400, 30 * 86400):
        src.search_area(area, seconds, lambda *a: None)
    assert [b["fromDays"] for b in bodies(route)] == ["3", "7", "14"]


# --- parse_item ---


def test_parse_dict_location_epoch_date_email():
    job = parse_item(ITEMS[0], "Product Manager", "ch", "fulltime")
    assert job.pk == "indeed:ind-001"
    assert (job.title, job.company, job.location) == ("Product Manager Intern", "Birch Systems", "Zurich, ZH")
    assert job.posted == "2026-10-29"
    assert job.url == "https://ch.indeed.com/viewjob?jk=ind-001"
    assert (job.keyword, job.area) == ("Product Manager", "ch")
    assert job.recruiter_url == "mailto:talent@birch-systems.example"
    assert job.recruiter_title == "Email from Indeed" and job.recruiter == ""
    assert job.employment_type == "internship"

    alt = parse_item(ITEMS[1], "q", "ch", "fulltime")  # alternate field names, city + country, epoch date
    assert alt.job_id == "ind-002"
    assert (alt.title, alt.company, alt.location) == ("Junior Product Owner", "Fjord Labs", "Basel, Switzerland")
    assert alt.posted == "2026-10-29"
    assert alt.url == "https://ch.indeed.com/viewjob?jk=ind-002"
    assert alt.description == "Own the backlog for a small team."
    assert (alt.recruiter_title, alt.recruiter_url) == ("", "")
    assert alt.employment_type == "fulltime"  # falls back to the query's job type


def test_parse_location_fallbacks():
    assert parse_item({"id": "1", "location": {"formattedAddressLong": "1 Main St, Bern"}}, "q", "a", "t").location == (
        "1 Main St, Bern"
    )
    assert parse_item({"id": "1", "location": {}}, "q", "a", "t").location == ""
    assert parse_item({"id": "1", "location": None}, "q", "a", "t").location == ""


def test_parse_dates():
    def posted(value):
        return parse_item({"id": "1", "datePublished": value}, "q", "a", "t").posted

    assert posted("2026-10-29T08:15:00Z") == "2026-10-29"
    assert posted(1793232000000) == "2026-10-29"  # milliseconds
    assert posted("Just posted") == "" and posted("2026-13-45") == "" and posted(0) == "" and posted(None) == ""
    assert parse_item({"id": "1", "postingDateParsed": 1793232000}, "q", "a", "t").posted == "2026-10-29"


def test_parse_item_coerces_every_field_to_text():
    job = parse_item(
        {"jobKey": 42, "title": {"text": "PM"}, "companyName": ["Acme"], "location": {"formattedAddressShort": 3},
         "jobUrl": None, "description": {"html": "x"}, "datePublished": [], "jobType": ["fulltime"]},
        "q", "a", "internship",
    )  # fmt: skip
    assert job.job_id == "42"
    assert (job.title, job.company, job.location, job.url, job.description, job.posted) == ("", "", "3", "", "", "")
    assert job.employment_type == "internship"
    assert parse_item({"id": {"x": 1}}, "q", "a", "t") is None


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    from jobscout.sources import indeed

    real = indeed.parse_item

    def flaky(item, query, area, job_type):
        if item.get("jobKey") == "ind-001":
            raise ValueError("bad item")
        return real(item, query, area, job_type)

    monkeypatch.setattr(indeed, "parse_item", flaky)
    respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(job_types=["internship"])
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["ind-002"] and res.complete is True


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("javascript:alert(1)", ""),
        ("data:text/html,<b>x</b>", ""),
        ("https://www.indeed.com/viewjob?jk=5", "https://www.indeed.com/viewjob?jk=5"),
    ],
)
def test_parse_item_keeps_only_web_links(link, expected):
    assert parse_item({"id": "5", "jobUrl": link}, "q", "a", "t").url == expected
    assert parse_item({"id": "5", "url": link}, "q", "a", "t").url == expected


def test_parse_item_without_id_and_long_description():
    assert parse_item({"title": "No id"}, "q", "a", "t") is None
    assert len(parse_item({"id": 5, "description": "x" * 7000}, "q", "a", "t").description) == 6000
    assert parse_item({"id": 5}, "q", "a", "t").job_id == "5"
    assert parse_item({"id": "1", "emails": []}, "q", "a", "t").recruiter_url == ""


# --- search_area behaviour ---


def test_dedups_within_area_and_progress(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(queries=("Product Manager",), job_types=["internship", "fulltime"])
    calls = []
    res = src.search_area(first_area(src), 86400, lambda *a: calls.append(a))
    assert res.billed == 8  # every returned item, duplicates included, over two searches
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002"]  # bare words match: "product" is in both titles
    assert res.jobs[0].employment_type == "internship"  # first sighting wins
    assert res.complete is True and res.error is None
    assert calls == [(0, 2, 0), (1, 2, 2), (2, 2, 2)]


def test_non_dict_items_are_skipped(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=[None, "oops", 5, *ITEMS])
    src = make_source(job_types=["internship"])
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002"] and res.billed == 7


def test_apify_error_keeps_jobs_so_far(respx_mock):
    respx_mock.post(url__regex=RUN).mock(side_effect=[httpx.Response(200, json=ITEMS), httpx.Response(400)])
    src = make_source(queries=("Product Manager", "Product Owner", "Intern"), job_types=["internship"])
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.complete is False and res.error.kind == "other"
    assert "tok-secret-123" not in str(res.error)
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002"] and res.billed == 4


def test_limit_error(respx_mock):
    respx_mock.post(url__regex=RUN).respond(403, text="Monthly usage hard limit exceeded")
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.complete is False and res.error.kind == "limit"


def test_read_timeout_is_not_retried(respx_mock):
    route = respx_mock.post(url__regex=RUN).mock(side_effect=httpx.ReadTimeout("slow"))
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert route.call_count == 1 and res.error.kind == "other" and res.complete is False


def test_cancel_stops_between_searches(respx_mock):
    cancel = threading.Event()
    route = respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(queries=("Product Manager", "Product Owner"), job_types=["internship"], cancel=cancel)
    res = src.search_area(first_area(src), 86400, lambda done, total, n: cancel.set() if done == 1 else None)
    assert route.call_count == 1
    assert res.complete is False and res.error is None
    assert [j.job_id for j in res.jobs] == ["ind-001", "ind-002"]


# --- countries file ---


def test_countries_file():
    from importlib.resources import files

    data = json.loads(files("jobscout.data").joinpath("indeed_countries.json").read_text(encoding="utf-8"))
    assert data["_source"] and "apify.com" in data["_source"]
    codes = data["countries"]
    assert {"uk", "ch", "de", "us"} <= set(codes)
    assert len(set(codes)) == len(codes) and all(c == c.lower() and len(c) == 2 for c in codes)
