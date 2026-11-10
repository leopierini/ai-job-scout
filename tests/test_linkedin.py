import json
import threading
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from jobscout.errors import ApifyError
from jobscout.http import request_json
from jobscout.settings import LinkedInSettings, Region
from jobscout.sources import AreaResult, SearchArea
from jobscout.sources.apify import ApifyClient
from jobscout.sources.linkedin import LinkedInSource, parse_item, search_url

ITEMS = json.loads((Path(__file__).parent / "fixtures" / "linkedin_items.json").read_text(encoding="utf-8"))
RUN = r".*acts/curious_coder~linkedin-jobs-scraper/run-sync-get-dataset-items.*"
LIMIT_COPY = "Apify monthly limit reached. It resets on your billing day. Add credit or wait."


def make_source(keywords=("Product Manager",), cancel=None, sleeps=None, **kw):
    kw.setdefault("actor", "curious_coder~linkedin-jobs-scraper")
    settings = LinkedInSettings(
        keywords=list(keywords),
        regions=[Region(name="United Kingdom", geo_id="101165590"), Region(name="Switzerland", geo_id="106693272")],
        **kw,
    )
    sleeps = [] if sleeps is None else sleeps
    apify = ApifyClient("tok-secret-123", httpx.Client(), sleep=sleeps.append)
    return LinkedInSource(settings, apify, cancel or threading.Event())


def first_area(src):
    return src.areas()[0]


# --- search_url / parse_item ---


def test_search_url_has_no_experience_filter():
    u = search_url("Product Manager", "101165590", 86400, "DD")
    assert "f_TPR=r86400" in u and "geoId=101165590" in u and "sortBy=DD" in u and "f_E" not in u
    assert "keywords=Product%20Manager" in u


def test_search_url_omits_empty_sort():
    assert "sortBy" not in search_url("PM", "1", 60, "")


def test_parse_item_full():
    job = parse_item(ITEMS[0], "Product Manager", "United Kingdom")
    assert job.pk == "linkedin:9001"
    assert (job.title, job.company, job.location) == ("Product Analyst Intern", "Fjord Labs", "Oslo, Norway")
    assert job.url == "https://www.linkedin.com/jobs/view/9001/"
    assert job.posted == "2026-10-28"
    assert (job.keyword, job.area) == ("Product Manager", "United Kingdom")
    assert (job.recruiter, job.recruiter_title) == ("Ingrid Example", "Talent Partner")
    assert job.recruiter_url == "https://www.linkedin.com/in/ingrid-example"
    assert job.employment_type == "Internship"


def test_parse_item_fallback_id_and_bad_date():
    from_link = parse_item(ITEMS[1], "k", "a")
    assert from_link.job_id == "123"
    relative = parse_item(ITEMS[2], "k", "a")
    assert relative.posted == ""
    assert parse_item({"title": "No id", "link": "https://example.com/x"}, "k", "a") is None
    assert parse_item({"id": "5", "postedAt": "2026-13-45"}, "k", "a").posted == ""


def test_parse_item_coerces_every_field_to_text():
    job = parse_item(
        {"id": 77, "title": {"text": "PM"}, "companyName": None, "location": ["Oslo"], "link": 5,
         "descriptionText": {"html": "x"}, "postedAt": 20261028, "jobPosterName": False, "employmentType": 1.5},
        "k", "a",
    )  # fmt: skip
    assert job.job_id == "77"
    for field in ("title", "company", "location", "description", "posted", "recruiter", "recruiter_title"):
        assert getattr(job, field) == "", field
    assert (job.url, job.employment_type) == ("", "1.5")
    assert parse_item({"id": {"x": 1}, "link": ["https://www.linkedin.com/jobs/view/9/"]}, "k", "a") is None


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("javascript:alert(1)", ""),
        ("JavaScript:alert(1)", ""),
        ("data:text/html,<b>x</b>", ""),
        ("/jobs/view/5/", ""),
        ("https://www.linkedin.com/jobs/view/5/", "https://www.linkedin.com/jobs/view/5/"),
        ("http://example.com/x", "http://example.com/x"),
    ],
)
def test_parse_item_keeps_only_web_links(link, expected):
    job = parse_item({"id": "5", "link": link, "jobPosterProfileUrl": link}, "k", "a")
    assert job.url == expected
    assert job.recruiter_url == expected


def test_an_item_that_cannot_be_parsed_is_skipped_alone(respx_mock, monkeypatch):
    from jobscout.sources import linkedin

    real = linkedin.parse_item

    def flaky(item, keyword, area):
        if item.get("id") == "9001":
            raise ValueError("bad item")
        return real(item, keyword, area)

    monkeypatch.setattr(linkedin, "parse_item", flaky)
    respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["123", "9003"] and res.complete is True and res.billed == 4


def test_parse_item_truncates_description():
    job = parse_item({"id": "1", "descriptionText": "x" * 7000}, "k", "a")
    assert len(job.description) == 6000


# --- areas ---


def test_areas_one_per_region():
    src = make_source(keywords=("a", "b", "c"))
    areas = src.areas()
    assert [a.key for a in areas] == ["linkedin:United Kingdom", "linkedin:Switzerland"]
    assert areas[0] == SearchArea("linkedin:United Kingdom", "United Kingdom", "linkedin", 3,
                                  {"region": "United Kingdom", "geo_id": "101165590"})


def test_area_dedups_within_area_and_counts_billed(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(keywords=("Product Manager", "Data Analyst"))
    calls = []
    res = src.search_area(first_area(src), 86400, lambda *a: calls.append(a))
    assert isinstance(res, AreaResult)
    assert res.billed == 8  # every returned item, duplicates included, over two searches
    assert [j.job_id for j in res.jobs] == ["9001", "123", "9003"]
    assert res.jobs[0].keyword == "Product Manager"  # first sighting wins
    assert res.complete is True and res.error is None
    assert route.call_count == 2
    assert calls == [(0, 2, 0), (1, 2, 3), (2, 2, 3)]


def test_request_payload(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    src = make_source(cap_per_keyword=7, scrape_company=False)
    src.search_area(first_area(src), 3600, lambda *a: None)
    body = json.loads(route.calls[0].request.content)
    assert body["count"] == 7 and body["scrapeCompany"] is False
    assert body["urls"] == [search_url("Product Manager", "101165590", 3600, "DD")]


def test_token_in_header_not_url(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    src = make_source()
    src.search_area(first_area(src), 86400, lambda *a: None)
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer tok-secret-123"
    assert "tok-secret-123" not in str(request.url)
    assert str(request.url).endswith("run-sync-get-dataset-items?timeout=300")


def test_gateway_errors_are_not_retried(respx_mock):
    """A 5xx after the run started could bill the same search twice."""
    for status in (500, 502, 503, 504):
        route = respx_mock.post(url__regex=RUN).respond(status)
        sleeps = []
        src = make_source(sleeps=sleeps)
        res = src.search_area(first_area(src), 86400, lambda *a: None)
        assert route.call_count == 1 and sleeps == [] and res.error.kind == "other"
        respx_mock.reset()


def test_retry_on_429_then_success(respx_mock):
    route = respx_mock.post(url__regex=RUN).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=ITEMS)]
    )
    sleeps = []
    src = make_source(sleeps=sleeps)
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert route.call_count == 2 and sleeps == [1]
    assert res.complete is True and len(res.jobs) == 3


def test_apify_limit_error(respx_mock):
    respx_mock.post(url__regex=r".*run-sync.*").respond(403, text="Monthly usage hard limit exceeded")
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.complete is False and res.error.kind == "limit"
    assert str(res.error) == LIMIT_COPY


def test_apify_auth_error(respx_mock):
    respx_mock.post(url__regex=RUN).respond(401, text="bad token tok-secret-123")
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.error.kind == "auth"
    assert str(res.error) == "Apify rejected the token. Check the token and try again."


def test_other_http_error_keeps_jobs_gathered_so_far(respx_mock):
    respx_mock.post(url__regex=RUN).mock(side_effect=[httpx.Response(200, json=ITEMS), httpx.Response(400)])
    src = make_source(keywords=("a", "b", "c"))
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.complete is False and res.error.kind == "other"
    assert "tok-secret-123" not in str(res.error)
    assert len(res.jobs) == 3 and res.billed == 4  # the area stopped after the failing search


def test_403_without_limit_is_other(respx_mock):
    respx_mock.post(url__regex=RUN).respond(403, text="Forbidden")
    src = make_source()
    assert src.search_area(first_area(src), 86400, lambda *a: None).error.kind == "other"


def test_transport_error_is_other_and_hides_token(respx_mock):
    respx_mock.post(url__regex=RUN).mock(side_effect=httpx.ConnectError("boom tok-secret-123"))
    sleeps = []
    src = make_source(sleeps=sleeps)
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert res.error.kind == "other" and "tok-secret-123" not in str(res.error)
    assert str(res.error) == "Apify couldn't be reached. Check your internet connection and try again."
    assert sleeps == [1, 2]


def test_unexpected_response_shape_is_other(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json={"oops": 1})
    src = make_source()
    assert src.search_area(first_area(src), 86400, lambda *a: None).error.kind == "other"


def test_cancel_stops_between_searches(respx_mock):
    cancel = threading.Event()
    route = respx_mock.post(url__regex=RUN).respond(200, json=ITEMS)
    src = make_source(keywords=("a", "b", "c"), cancel=cancel)
    res = src.search_area(first_area(src), 86400, lambda done, total, n: cancel.set() if done == 1 else None)
    assert route.call_count == 1
    assert res.complete is False and res.error is None
    assert len(res.jobs) == 3


def test_read_timeout_is_not_retried_and_is_other(respx_mock):
    route = respx_mock.post(url__regex=RUN).mock(side_effect=httpx.ReadTimeout("slow tok-secret-123"))
    sleeps = []
    src = make_source(sleeps=sleeps)
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert route.call_count == 1 and sleeps == []  # a timed-out run may already be billed
    assert res.complete is False and res.error.kind == "other"
    assert str(res.error) == "Apify took too long to answer. This search was skipped and will be retried next run."
    assert "tok-secret-123" not in str(res.error)


def test_connect_error_is_still_retried(respx_mock):
    route = respx_mock.post(url__regex=RUN).mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=ITEMS)]
    )
    src = make_source()
    assert len(src.search_area(first_area(src), 86400, lambda *a: None).jobs) == 3
    assert route.call_count == 2


def test_non_dict_items_are_skipped(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=[None, "oops", 5, ["x"], *ITEMS])
    src = make_source()
    res = src.search_area(first_area(src), 86400, lambda *a: None)
    assert [j.job_id for j in res.jobs] == ["9001", "123", "9003"]
    assert res.billed == 8 and res.complete is True


# --- request_json ---


def test_request_json_read_timeout_retried_by_default_but_not_when_disabled(respx_mock):
    route = respx_mock.get("https://example.com/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    sleeps = []
    with httpx.Client() as client:
        with pytest.raises(httpx.ReadTimeout):
            request_json(client, "GET", "https://example.com/slow", sleep=sleeps.append)
        assert route.call_count == 3 and sleeps == [1, 2]
        route.reset()
        sleeps.clear()
        with pytest.raises(httpx.ReadTimeout):
            request_json(client, "GET", "https://example.com/slow", retry_on_timeout=False, sleep=sleeps.append)
        assert route.call_count == 1 and sleeps == []


def test_request_json_empty_or_non_json_body_is_none(respx_mock):
    respx_mock.get("https://example.com/empty").respond(200, content=b"")
    respx_mock.get("https://example.com/text").respond(200, text="ok")
    with httpx.Client() as client:
        assert request_json(client, "GET", "https://example.com/empty") is None
        assert request_json(client, "GET", "https://example.com/text") is None


def test_request_json_still_checks_status_when_body_is_empty(respx_mock):
    respx_mock.get("https://example.com/gone").respond(404, content=b"")
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        request_json(client, "GET", "https://example.com/gone")


def test_request_json_retries_server_errors_by_default(respx_mock):
    route = respx_mock.get("https://example.com/x").mock(side_effect=[httpx.Response(503), httpx.Response(200, json=[])])
    with httpx.Client() as client:
        assert request_json(client, "GET", "https://example.com/x", sleep=lambda s: None) == []
    assert route.call_count == 2


def test_request_json_does_not_retry_client_errors(respx_mock):
    route = respx_mock.get("https://example.com/x").respond(404)
    sleeps = []
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        request_json(client, "GET", "https://example.com/x", sleep=sleeps.append)
    assert route.call_count == 1 and sleeps == []


def test_request_json_backoff_and_final_error(respx_mock):
    route = respx_mock.get("https://example.com/x").respond(429)
    sleeps = []
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError) as err:
        request_json(client, "GET", "https://example.com/x", retries=3, sleep=sleeps.append)
    assert err.value.response.status_code == 429
    assert route.call_count == 3 and sleeps == [1, 2]


# --- regions ---


def test_regions_file_entries_are_verified():
    data = json.loads(files("jobscout.data").joinpath("linkedin_regions.json").read_text(encoding="utf-8"))
    assert {"91000002", "101165590", "106693272"} <= {r["geo_id"] for r in data}
    assert all(r["verified_on"] for r in data)
    assert all(set(r) == {"name", "geo_id", "verified_on"} for r in data)
    assert len({r["geo_id"] for r in data}) == len(data)


def test_meka_actor_gets_its_own_limit_field(respx_mock):
    route = respx_mock.post(url__regex=r".*acts/meka.im~linkedin-jobs-scraper/run-sync-get-dataset-items.*").respond(
        200, json=ITEMS
    )
    src = make_source(actor="meka.im~linkedin-jobs-scraper", cap_per_keyword=50, scrape_company=False)
    res = src.search_area(first_area(src), 3600, lambda *a: None)
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "urls": [search_url("Product Manager", "101165590", 3600, "DD")],
        "scrapeCompany": False,
        "limitPerSource": 50,
    }
    assert [j.job_id for j in res.jobs] == ["9001", "123", "9003"]  # same item fields as the original actor


def test_an_unknown_actor_gets_the_original_limit_field(respx_mock):
    run = r".*acts/someone~other-scraper/run-sync-get-dataset-items.*"
    route = respx_mock.post(url__regex=run).respond(200, json=[])
    src = make_source(actor="someone~other-scraper", cap_per_keyword=5)
    src.search_area(first_area(src), 3600, lambda *a: None)
    body = json.loads(route.calls[0].request.content)
    assert body["count"] == 5 and "limitPerSource" not in body


def test_default_actor_is_meka():
    assert LinkedInSettings().actor == "meka.im~linkedin-jobs-scraper"
