import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobscout.settings import CareerPagesSettings
from jobscout.sources import careers
from jobscout.sources.careers import (
    UNKNOWN_SYSTEM,
    CareerPagesSource,
    board_from_url,
    company_label,
    find_board,
)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 12)
WEEK = 7 * 86400

ASHBY = "https://api.ashbyhq.com/posting-api/job-board/example-labs?includeCompensation=true"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/kestrelpayments/jobs?content=true&pay_transparency=true"
LEVER = "https://api.lever.co/v0/postings/fjord-labs-example?mode=json"
LEVER_EU = "https://api.eu.lever.co/v0/postings/fjord-labs-example?mode=json"
RECRUITEE = "https://brightwater.recruitee.com/api/offers/"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/northwind-robotics?details=true"
PERSONIO = "https://alpenglow-example.jobs.personio.de/xml"
TEAMTAILOR = "https://careers.lumenhealth.example/jobs.rss"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Server:
    """A fake web: answers each known URL and records every request; an unknown URL fails the test."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            raise AssertionError(f"unexpected request {request.url}")
        if isinstance(answer, list):  # answered in turn
            answer = answer.pop(0)
        if callable(answer):
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, content=answer)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def make_source(pages, routes=None, cancel=None, **settings):
    server = Server(routes or {})
    sleeps: list[float] = []
    source = CareerPagesSource(
        CareerPagesSettings(enabled=True, pages=list(pages), **settings),
        httpx.Client(transport=httpx.MockTransport(server)),
        cancel or threading.Event(),
        today=lambda: TODAY,
        sleep=sleeps.append,
    )
    return source, server, sleeps


def search(source, window=WEEK, area=0):
    calls = []
    result = source.search_area(source.areas()[area], window, lambda *a: calls.append(a))
    return result, calls


def by_title(result):
    return {job.title: job for job in result.jobs}


# --- areas and labels ---


def test_areas_one_per_page_labelled_from_the_address_without_any_request():
    source, server, _ = make_source(
        [
            "https://jobs.ashbyhq.com/example-labs",
            "https://JOBS.ashbyhq.com/example-labs/",  # the same page again
            "https://boards.greenhouse.io/kestrelpayments",
            "https://jobs.eu.lever.co/fjord-labs-example",
            "https://brightwater.recruitee.com/",
            "https://apply.workable.com/northwind-robotics/",
            "https://alpenglow-example.jobs.personio.de",
            "https://lumen-health-example.teamtailor.com/jobs",
            "https://careers.kestrel-payments.example/open-roles",
        ]
    )
    areas = source.areas()
    assert [a.label for a in areas] == [
        "Example Labs",
        "Kestrelpayments",
        "Fjord Labs Example",
        "Brightwater",
        "Northwind Robotics",
        "Alpenglow Example",
        "Lumen Health Example",
        "Kestrel Payments",
    ]
    assert areas[0].key == "careers:https://jobs.ashbyhq.com/example-labs"
    assert areas[-1].key == "careers:https://careers.kestrel-payments.example/open-roles"
    assert all(a.source == "careers" and a.searches == 1 for a in areas)
    assert server.requests == []


def test_company_label_keeps_mixed_case_and_strips_host_prefixes():
    assert company_label("https://jobs.ashbyhq.com/ExampleLabs") == "ExampleLabs"
    assert company_label("https://www.kestrel.example/careers") == "Kestrel"
    assert company_label("https://careers.lumenhealth.example/jobs.rss") == "Lumenhealth"


# --- telling the job system from an address ---


@pytest.mark.parametrize(
    ("url", "ats", "token", "feed"),
    [
        ("https://jobs.ashbyhq.com/example-labs/0f1e", "ashby", "example-labs", ASHBY),
        ("https://boards.greenhouse.io/kestrelpayments", "greenhouse", "kestrelpayments", GREENHOUSE),
        ("https://job-boards.greenhouse.io/kestrelpayments/jobs/41", "greenhouse", "kestrelpayments", GREENHOUSE),
        ("https://job-boards.eu.greenhouse.io/kestrelpayments", "greenhouse", "kestrelpayments", GREENHOUSE),
        (
            "https://boards.greenhouse.io/embed/job_board?for=kestrelpayments",
            "greenhouse",
            "kestrelpayments",
            GREENHOUSE,
        ),
        ("https://jobs.lever.co/fjord-labs-example", "lever", "fjord-labs-example", LEVER),
        ("https://jobs.eu.lever.co/fjord-labs-example/a1b2", "lever", "fjord-labs-example", LEVER_EU),
        ("https://brightwater.recruitee.com/o/junior", "recruitee", "brightwater", RECRUITEE),
        ("https://apply.workable.com/northwind-robotics/j/9F8E", "workable", "northwind-robotics", WORKABLE),
        ("https://alpenglow-example.jobs.personio.de/", "personio", "alpenglow-example", PERSONIO),
        ("https://alpenglow-example.jobs.personio.com", "personio", "alpenglow-example", "https://alpenglow-example.jobs.personio.com/xml"),
        ("https://lumen-example.teamtailor.com/jobs", "teamtailor", "lumen-example", "https://lumen-example.teamtailor.com/jobs.rss"),
        ("https://careers.lumenhealth.example/jobs.rss", "teamtailor", "careers.lumenhealth.example", TEAMTAILOR),
    ],
)
def test_board_from_url(url, ats, token, feed):
    board = board_from_url(url)
    assert (board.ats, board.token, board.feed_url) == (ats, token, feed)


@pytest.mark.parametrize(
    "url",
    ["https://careers.kestrel.example/", "https://apply.workable.com/j/9F8E7D6C5B", "https://www.recruitee.com/"],
)
def test_board_from_url_needs_the_page_for_anything_else(url):
    assert board_from_url(url) is None


def test_find_board_in_page_html():
    assert find_board('<a href="https://jobs.ashbyhq.com\\/example-labs">').feed_url == ASHBY  # JSON-escaped
    embed = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=kestrelpayments"></script>'
    assert find_board(embed).token == "kestrelpayments"
    assert find_board(
        '<iframe src="https://job-boards.greenhouse.io/embed/job_board?b=1&amp;for=kestrelpayments">'
    ).token == ("kestrelpayments")
    assert find_board('<a href="https://jobs.eu.lever.co/fjord-labs-example">').feed_url == LEVER_EU
    assert find_board('<a href="https://www.workable.com/">Powered by</a>') is None  # the system's own site
    # A site built on Teamtailor is its own board.
    page = '<link href="//assets-aws.teamtailor-cdn.com/assets/careersite.css"><a href="//app.teamtailor.com/x">'
    board = find_board(page, "https://careers.lumenhealth.example/")
    assert board.feed_url == TEAMTAILOR
    assert find_board(page) is None  # without the page address there is no origin to read


# --- each system's feed ---


def test_ashby():
    source, server, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: fixture("careers_ashby.json")})
    result, calls = search(source)
    assert server.urls == [ASHBY]
    assert server.requests[0].headers["User-Agent"].startswith("ai-job-scout")
    assert (result.complete, result.billed, result.error) == (True, 0, None)
    assert calls == [(0, 1, 0), (1, 1, 2)]
    jobs = by_title(result)
    assert set(jobs) == {"Product Manager, Payments", "Data Analyst Intern"}  # the unlisted job is left out
    pm = jobs["Product Manager, Payments"]
    assert pm.source == "careers"
    assert pm.job_id == "ashby:example-labs:0f1e2d3c-0000-4000-8000-000000000001"
    assert pm.company == "Example Labs" and pm.area == "Example Labs"
    assert pm.location == "Berlin; Lisbon"
    assert pm.url == "https://jobs.ashbyhq.com/example-labs/0f1e2d3c-0000-4000-8000-000000000001"
    assert pm.posted == "2026-11-10"
    assert pm.employment_type == "Full-time"
    assert pm.salary == "EUR 60,000–75,000 a year"
    assert pm.description.startswith("About the role\n\nOwn the checkout of Example Labs.")
    assert pm.keyword == ""
    intern = jobs["Data Analyst Intern"]
    assert intern.location == "Remote"
    assert intern.salary == "€1.5K per month"  # Ashby's own summary when it gives no amounts
    assert intern.employment_type == "Internship"
    assert intern.description == "Analyse product data."


def test_greenhouse():
    page = "https://boards.greenhouse.io/kestrelpayments"
    source, server, _ = make_source([page], {GREENHOUSE: fixture("careers_greenhouse.json")})
    result, _ = search(source)
    assert server.urls == [GREENHOUSE]  # one request for the whole board, pay ranges included
    jobs = by_title(result)
    backend = jobs["Senior Backend Engineer"]
    assert backend.job_id == "greenhouse:kestrelpayments:4100001"
    assert backend.company == "Kestrel Payments"  # from the feed
    assert backend.location == "Amsterdam, Netherlands"
    assert backend.url == "https://job-boards.greenhouse.io/kestrelpayments/jobs/4100001"
    assert backend.posted == "2026-11-08"
    assert backend.salary == "EUR 85,000–100,000"
    assert backend.description == "Build the ledger at Kestrel & friends.\n\n- Go"
    assert jobs["Support Specialist"].salary == ""


def test_lever_us_and_eu_hosts():
    source, server, _ = make_source(["https://jobs.lever.co/fjord-labs-example"], {LEVER: fixture("careers_lever.json")})
    result, _ = search(source)
    assert server.urls == [LEVER]
    jobs = by_title(result)
    growth = jobs["Growth Marketing Manager"]
    assert growth.job_id == "lever:fjord-labs-example:a1b2c3d4-0000-4000-8000-00000000000a"
    assert growth.company == "Fjord Labs Example"
    assert growth.location == "Milan, Italy; Rome, Italy"
    assert growth.posted == "2026-11-11"  # from epoch milliseconds
    assert growth.employment_type == "Full-time"
    assert growth.salary == "EUR 45,000–55,000 a year"
    assert "Grow the Fjord Labs Example user base." in growth.description
    assert "What you'll do\n- Run campaigns" in growth.description
    assert growth.description.endswith("We offer a learning budget.")
    assoc = jobs["Customer Success Associate"]
    assert assoc.location == "Anywhere (remote)"
    assert assoc.salary == "Paid internship"

    source, server, _ = make_source(["https://jobs.eu.lever.co/fjord-labs-example"], {LEVER_EU: fixture("careers_lever.json")})
    result, _ = search(source)
    assert server.urls == [LEVER_EU] and len(result.jobs) == 2


def test_recruitee():
    source, server, _ = make_source(
        ["https://brightwater.recruitee.com"], {RECRUITEE: fixture("careers_recruitee.json")}
    )
    result, _ = search(source)
    assert server.urls == [RECRUITEE]
    jobs = by_title(result)
    ds = jobs["Junior Data Scientist"]
    assert ds.job_id == "recruitee:brightwater:7700001"
    assert ds.company == "Brightwater Analytics"
    assert ds.location == "Utrecht, Netherlands"
    assert ds.url == "https://careers.brightwater.example/o/junior-data-scientist"
    assert ds.posted == "2026-11-09"
    assert ds.employment_type == "Fulltime permanent"
    assert ds.salary == "EUR 3,200–4,000 a month"
    assert ds.description == "Model things.\n\n- Python"
    writer = jobs["Content Writer"]
    assert writer.location == "Remote" and writer.salary == ""


def test_workable():
    page = "https://apply.workable.com/northwind-robotics/"
    source, server, _ = make_source([page], {WORKABLE: fixture("careers_workable.json")})
    result, _ = search(source)
    assert server.urls == [WORKABLE]
    jobs = by_title(result)
    eng = jobs["Robotics Engineer"]
    assert eng.job_id == "workable:northwind-robotics:9F8E7D6C5B"
    assert eng.company == "Northwind Robotics"  # from the feed
    assert eng.location == "Barcelona, Spain"
    assert eng.url == "https://apply.workable.com/j/9F8E7D6C5B"
    assert eng.posted == "2026-11-07" and eng.employment_type == "Full-time" and eng.salary == ""
    assert jobs["Technical Writer"].location == "Porto, Portugal (remote)"


def test_personio():
    source, server, _ = make_source(
        ["https://alpenglow-example.jobs.personio.de/"], {PERSONIO: fixture("careers_personio.xml")}
    )
    result, _ = search(source)
    assert server.urls == [PERSONIO]
    jobs = by_title(result)
    po = jobs["Product Owner (d/f/m)"]
    assert po.job_id == "personio:alpenglow-example:3100001"
    assert po.company == "Alpenglow Example Software GmbH"
    assert po.location == "Munich; Vienna"
    assert po.url == "https://alpenglow-example.jobs.personio.de/job/3100001"
    assert po.posted == "2026-11-10"
    assert po.employment_type == "Full-time, permanent"
    assert po.description == "Your role\nOwn the roadmap & talk to customers.\n\nYour profile\n- Curious"
    student = jobs["Working Student Marketing"]
    assert student.company == "Alpenglow Example" and student.employment_type == "Part-time, working_student"


def test_teamtailor_feed_address():
    source, server, _ = make_source([TEAMTAILOR], {TEAMTAILOR: fixture("careers_teamtailor.rss")})
    result, _ = search(source)
    assert server.urls == [TEAMTAILOR]
    jobs = by_title(result)
    fe = jobs["Frontend Developer"]
    assert fe.job_id == "teamtailor:careers.lumenhealth.example:5550001"
    assert fe.company == "Lumen Health"  # the feed's title
    assert fe.location == "Turin, Italy (remote)"
    assert fe.url == "https://careers.lumenhealth.example/jobs/5550001-frontend-developer"
    assert fe.posted == "2026-11-10"
    assert fe.description == "Build the patient app."
    ops = jobs["Clinical Operations Lead"]
    assert ops.location == "Bologna, Italy"
    assert ops.posted == "2026-11-10"  # 01:30 at +02:00 is still the 10th in UTC


# --- telling the system from a company's own careers page ---


def test_detects_the_board_from_the_page_and_pauses_between_requests():
    page = "https://www.kestrel-payments.example/careers"
    html = '<html><script src="https://boards.greenhouse.io/embed/job_board/js?for=kestrelpayments"></script></html>'
    source, server, sleeps = make_source([page], {page: html.encode(), GREENHOUSE: fixture("careers_greenhouse.json")})
    result, _ = search(source)
    assert server.urls == [page, GREENHOUSE]
    assert sleeps == [careers.PAUSE_SECONDS]
    assert result.complete and len(result.jobs) == 2
    assert result.jobs[0].area == "Kestrel Payments"


def test_detects_a_teamtailor_site_on_its_own_domain():
    page = "https://careers.lumenhealth.example/"
    html = b'<link rel="stylesheet" href="//assets-aws.teamtailor-cdn.com/assets/careersite.css">'
    source, server, _ = make_source([page], {page: html, TEAMTAILOR: fixture("careers_teamtailor.rss")})
    result, _ = search(source)
    assert server.urls == [page, TEAMTAILOR]
    assert len(result.jobs) == 2


def test_follows_redirects_of_the_careers_page():
    page = "https://kestrel.example/jobs"
    final = "https://www.kestrel.example/jobs/"
    html = b'<a href="https://jobs.ashbyhq.com/example-labs">Open roles</a>'
    routes = {page: httpx.Response(301, headers={"Location": final}), final: html, ASHBY: fixture("careers_ashby.json")}
    source, server, _ = make_source([page], routes)
    result, _ = search(source)
    assert server.urls == [page, final, ASHBY]
    assert len(result.jobs) == 2


def test_unknown_system_is_an_error_for_that_page_only():
    page = "https://careers.kestrel.example/"
    source, server, _ = make_source([page], {page: b"<html><p>We are hiring! Email us.</p></html>"})
    result, _ = search(source)
    assert server.urls == [page]
    assert (result.jobs, result.complete) == ([], False)
    assert str(result.error) == UNKNOWN_SYSTEM


# --- filters and window ---


def test_title_words_keep_whole_words_and_set_the_keyword():
    source, _, _ = make_source(
        ["https://jobs.ashbyhq.com/example-labs"],
        {ASHBY: fixture("careers_ashby.json")},
        title_words=["Product Manager", "intern", "manag"],
    )
    result, _ = search(source)
    assert {j.title: j.keyword for j in result.jobs} == {
        "Product Manager, Payments": "Product Manager",
        "Data Analyst Intern": "intern",
    }

    source, _, _ = make_source(
        ["https://jobs.ashbyhq.com/example-labs"], {ASHBY: fixture("careers_ashby.json")}, title_words=["intern"]
    )
    result, _ = search(source)
    assert [j.title for j in result.jobs] == ["Data Analyst Intern"]


def test_locations_match_words_remote_and_blank():
    routes = {LEVER: fixture("careers_lever.json"), GREENHOUSE: fixture("careers_greenhouse.json")}
    pages = ["https://jobs.lever.co/fjord-labs-example", "https://boards.greenhouse.io/kestrelpayments"]
    source, _, _ = make_source(pages, routes, locations=["rome"])
    result, _ = search(source)
    assert [j.title for j in result.jobs] == ["Growth Marketing Manager"]  # "Rome, Italy" among its locations

    source, _, _ = make_source(pages, routes, locations=["Remote"])
    result, _ = search(source)
    assert [j.title for j in result.jobs] == ["Customer Success Associate"]  # flagged remote
    result, _ = search(source, area=1)
    assert [j.title for j in result.jobs] == ["Support Specialist"]  # "Remote, Europe" in the text

    source, _, _ = make_source(pages, routes, locations=["amster"])  # a part of a word is no match
    result, _ = search(source, area=1)
    assert result.jobs == []


def test_location_filter_keeps_jobs_without_a_location():
    feed = (
        b'{"jobs": [{"id": "x1", "title": "Founding Engineer", "jobUrl": "https://jobs.ashbyhq.com/example-labs/x1"}]}'
    )
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: feed}, locations=["Milan"])
    result, _ = search(source)
    assert [j.title for j in result.jobs] == ["Founding Engineer"]
    assert result.jobs[0].location == "" and result.jobs[0].posted == ""


def test_location_filter_keeps_a_remote_job_without_a_location():
    feed = b'{"jobs": [{"id": "r1", "title": "Designer", "isRemote": true}]}'
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: feed}, locations=["Milan"])
    result, _ = search(source)
    assert [j.location for j in result.jobs] == ["Remote"]


def test_every_open_posting_is_kept_whatever_its_date():
    feed = b"""{"jobs": [
        {"id": "new", "title": "New", "publishedAt": "2026-11-11T08:00:00Z"},
        {"id": "edge", "title": "Edge", "publishedAt": "2026-11-05T08:00:00Z"},
        {"id": "old", "title": "Old", "publishedAt": "2026-10-01T08:00:00Z"},
        {"id": "undated", "title": "Undated"}
    ]}"""
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: feed})
    result, _ = search(source, window=WEEK)
    assert sorted(j.title for j in result.jobs) == ["Edge", "New", "Old", "Undated"]


# --- failures ---


@pytest.mark.parametrize("declaration", [b"<!DOCTYPE rss [", b'<!ENTITY boom "boom">'])
def test_xml_with_a_doctype_or_entity_is_refused(declaration):
    feed = b'<?xml version="1.0"?>' + declaration + b"<rss><channel><item><title>&boom;</title></item></channel></rss>"
    source, _, _ = make_source([TEAMTAILOR], {TEAMTAILOR: feed})
    result, _ = search(source)
    assert (result.jobs, result.complete) == ([], False)
    assert "document type" in str(result.error)


def test_unreadable_feeds_are_errors():
    source, _, _ = make_source([TEAMTAILOR], {TEAMTAILOR: b"<html><body>Not a feed"})
    result, _ = search(source)
    assert not result.complete and "couldn't read" in str(result.error)
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: b"<html>maintenance</html>"})
    result, _ = search(source)
    assert not result.complete and "couldn't read" in str(result.error)


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx.Response(404), "The job board of Example Labs wasn't found (HTTP 404). Check the address."),
        ([httpx.Response(503)] * 3, "The job board of Example Labs answered with an error (HTTP 503)."),
        (
            [httpx.Response(429)] * 3,
            "The job board of Example Labs asked us to slow down. It will be retried next run.",
        ),
        (httpx.Response(403), "The job board of Example Labs answered with an error (HTTP 403)."),
        (
            [httpx.ConnectError("boom")] * 3,
            "The job board of Example Labs couldn't be reached. Check your internet connection.",
        ),
        (
            [httpx.ReadTimeout("slow")] * 3,
            "The job board of Example Labs took too long to answer. It will be retried next run.",
        ),
    ],
)
def test_http_failures_give_an_incomplete_result_without_the_address(answer, message):
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: answer})
    result, _ = search(source)
    assert (result.jobs, result.complete, result.billed) == ([], False, 0)
    assert str(result.error) == message
    assert "://" not in message and "ashbyhq" not in message and "example-labs" not in message


def test_a_passing_failure_is_retried():
    source, server, sleeps = make_source(
        ["https://jobs.ashbyhq.com/example-labs"], {ASHBY: [httpx.Response(502), fixture("careers_ashby.json")]}
    )
    result, _ = search(source)
    assert result.complete and len(result.jobs) == 2
    assert len(server.requests) == 2 and sleeps == [1]


def test_careers_page_failure_names_the_page():
    page = "https://careers.kestrel.example/"
    source, _, _ = make_source([page], {page: httpx.Response(404)})
    result, _ = search(source)
    assert str(result.error) == "The careers page of Kestrel wasn't found (HTTP 404). Check the address."


def test_cancel_stops_before_any_request():
    cancel = threading.Event()
    cancel.set()
    source, server, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {}, cancel=cancel)
    result, _ = search(source)
    assert (result.jobs, result.complete, result.error) == ([], False, None)
    assert server.requests == []


def test_cancel_after_the_careers_page_skips_the_feed():
    cancel = threading.Event()
    page = "https://careers.kestrel.example/"

    def page_then_cancel():
        cancel.set()
        return httpx.Response(200, content=b'<a href="https://jobs.lever.co/fjord-labs-example">Jobs</a>')

    source, server, _ = make_source([page], {page: page_then_cancel}, cancel=cancel)
    result, _ = search(source)
    assert server.urls == [page]
    assert (result.jobs, result.complete) == ([], False)


def test_one_malformed_item_does_not_lose_the_others(monkeypatch):
    real = careers._lever

    def fussy(job, feed):
        if job.get("id") == "a1b2c3d4-0000-4000-8000-00000000000a":
            raise KeyError("boom")
        return real(job, feed)

    monkeypatch.setattr(careers, "_lever", fussy)
    source, _, _ = make_source(["https://jobs.lever.co/fjord-labs-example"], {LEVER: fixture("careers_lever.json")})
    result, _ = search(source)
    assert result.complete
    assert [j.title for j in result.jobs] == ["Customer Success Associate"]


def test_items_without_id_or_title_are_skipped():
    feed = b'{"jobs": [{"id": "", "title": "No id"}, {"id": "y", "title": ""}, {"id": "z", "title": "Kept"}, 42]}'
    source, _, _ = make_source(["https://jobs.ashbyhq.com/example-labs"], {ASHBY: feed})
    result, _ = search(source)
    assert [j.job_id for j in result.jobs] == ["ashby:example-labs:z"]


def test_pauses_between_areas():
    source, _, sleeps = make_source(
        ["https://jobs.ashbyhq.com/example-labs", "https://jobs.lever.co/fjord-labs-example"],
        {ASHBY: fixture("careers_ashby.json"), LEVER: fixture("careers_lever.json")},
    )
    search(source, area=0)
    assert sleeps == []
    search(source, area=1)
    assert sleeps == [careers.PAUSE_SECONDS]
