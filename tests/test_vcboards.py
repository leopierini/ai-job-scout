"""VC portfolio job boards on Getro and Consider. Every board and company here is fictional."""

import json
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobscout.errors import SearchError
from jobscout.settings import VcBoardsSettings
from jobscout.sources import DESCRIPTION_LIMIT
from jobscout.sources.vcboards import (
    PAUSE_SECONDS,
    USER_AGENT,
    VcBoardsSource,
    board_label,
    detect_board,
    load_presets,
    parse_consider_job,
    parse_getro_job,
)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 12)
WEEK = 7 * 86400
GETRO_BOARD = "https://jobs.example-ventures.com"
CONSIDER_BOARD = "https://jobs.example-capital.com"
GETRO_API = "https://api.getro.com/api/v2/collections/4242/search/jobs"
CONSIDER_API = f"{CONSIDER_BOARD}/api-boards/search-jobs"
CSRF = "test-csrf-token-0001"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict:
    return json.loads(fixture_text(name))


def epoch(days_ago: int) -> int:
    day = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), UTC)
    return int(day.timestamp())


def getro_job(n: int, days_ago: int = 1, **extra) -> dict:
    job = {
        "id": n,
        "title": f"Software Engineer {n}",
        "url": f"https://jobs.ashbyhq.com/kestrel-payments/{n}",
        "slug": f"{n}-software-engineer",
        "created_at": epoch(days_ago),
        "locations": ["Milan, Italy"],
        "organization": {"name": "Kestrel Payments", "slug": "kestrel-payments"},
    }
    job.update(extra)
    return job


def consider_job(n: int, days_ago: int = 1, **extra) -> dict:
    job = {
        "jobId": f"c-{n}",
        "title": f"Data Engineer {n}",
        "url": f"https://fjord-labs.example.com/jobs/{n}",
        "timeStamp": (TODAY - timedelta(days=days_ago)).isoformat() + "T08:00:00Z",
        "companyName": "Fjord Labs",
        "locations": ["Oslo, NO"],
    }
    job.update(extra)
    return job


class FakeBoards:
    """A tiny fake of both platforms. It records every request and answers from the given pages."""

    def __init__(self, *, getro_pages=None, getro_count=None, consider_pages=None, page_html=None) -> None:
        self.requests: list[httpx.Request] = []
        self.getro_pages = getro_pages if getro_pages is not None else [fixture_json("vcboards_getro_jobs.json")]
        self.getro_count = getro_count
        self.consider_pages = consider_pages if consider_pages is not None else [fixture_json("vcboards_consider_jobs.json")]
        self.page_html = page_html or {}
        self.fail: dict[str, httpx.Response | Exception] = {}  # "page"/"getro"/"consider" -> canned failure

    def bodies(self, url: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if str(r.url) == url]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "GET":
            return self._page(request)
        if url == GETRO_API:
            return self._getro(request)
        if url == CONSIDER_API:
            return self._consider(request)
        return httpx.Response(404)

    def _failure(self, kind: str) -> httpx.Response | None:
        failure = self.fail.get(kind)
        if isinstance(failure, Exception):
            raise failure
        return failure

    def _page(self, request: httpx.Request) -> httpx.Response:
        if (failure := self._failure("page")) is not None:
            return failure
        host = request.url.host
        if host in self.page_html:
            return httpx.Response(200, text=self.page_html[host])
        if host == "jobs.example-ventures.com":
            return httpx.Response(200, text=fixture_text("vcboards_getro_board.html"))
        if host == "jobs.example-capital.com":
            return httpx.Response(
                200,
                text=fixture_text("vcboards_consider_board.html"),
                headers=[("Set-Cookie", "session=abc123; Path=/"), ("Set-Cookie", "session.sig=sig456; Path=/")],
            )
        return httpx.Response(404)

    def _getro(self, request: httpx.Request) -> httpx.Response:
        if (failure := self._failure("getro")) is not None:
            return failure
        if request.headers.get("Origin") is None or request.headers.get("Referer") is None:
            return httpx.Response(406)
        body = json.loads(request.content)
        if not isinstance(body.get("filters"), dict):
            return httpx.Response(400, json={"error": ["filters: must be a hash"]})
        page = body["page"]
        data = self.getro_pages[page] if page < len(self.getro_pages) else {"results": {"jobs": [], "count": 0}}
        if self.getro_count is not None:
            data = {"results": {"jobs": data["results"]["jobs"], "count": self.getro_count}}
        return httpx.Response(200, json=data)

    def _consider(self, request: httpx.Request) -> httpx.Response:
        if (failure := self._failure("consider")) is not None:
            return failure
        if request.headers.get("x-csrf-token") != CSRF or "session=abc123" not in request.headers.get("Cookie", ""):
            return httpx.Response(412, json={"error": "INVALID_CSRF"})
        body = json.loads(request.content)
        sequence = body["meta"].get("sequence")
        index = int(sequence.removeprefix("seq-")) if sequence else 0
        return httpx.Response(200, json=self.consider_pages[index])


def make_source(fake: FakeBoards, *, cancel: threading.Event | None = None, sleeps: list | None = None, **settings):
    client = httpx.Client(transport=httpx.MockTransport(fake))
    sleeps = sleeps if sleeps is not None else []
    s = VcBoardsSettings(enabled=True, **settings)
    return VcBoardsSource(s, client, cancel or threading.Event(), today=lambda: TODAY, sleep=sleeps.append)


def run(source: VcBoardsSource, index: int = 0, window: int = WEEK, calls: list | None = None):
    area = source.areas()[index]
    calls = calls if calls is not None else []
    return source.search_area(area, window, lambda done, total, n: calls.append((done, total, n)))


# --- presets and areas ---------------------------------------------------------------------------


def test_presets_load_with_known_platforms():
    presets = load_presets()
    assert len(presets) >= 20
    names = [p["name"] for p in presets]
    assert len(set(names)) == len(names)
    for preset in presets:
        assert preset["url"].startswith("https://")
        assert preset["platform"] in ("getro", "consider")
        assert preset["country"]
    assert {"Index Ventures", "Point Nine", "Notion Capital", "360 Capital"} <= set(names)


def test_presets_are_copies():
    load_presets()[0]["name"] = "Changed"
    assert load_presets()[0]["name"] != "Changed"


def test_board_label_uses_preset_name_else_host():
    assert board_label("https://jobs.pointnine.com/jobs") == "Point Nine"
    assert board_label("https://www.jobs.pointnine.com") == "Point Nine"
    assert board_label("https://JOBS.360CAP.VC/") == "360 Capital"
    assert board_label("https://jobs.example-ventures.com/jobs") == "jobs.example-ventures.com"


def test_areas_one_per_board_host():
    fake = FakeBoards()
    source = make_source(
        fake,
        boards=[GETRO_BOARD, f"{GETRO_BOARD}/jobs", "https://jobs.pointnine.com/jobs"],
        queries=["ai engineer", "data"],
    )
    areas = source.areas()
    assert [a.key for a in areas] == ["vcboards:jobs.example-ventures.com", "vcboards:jobs.pointnine.com"]
    assert [a.label for a in areas] == ["jobs.example-ventures.com", "Point Nine"]
    assert all(a.source == "vcboards" and a.searches == 2 for a in areas)
    assert make_source(fake, boards=[GETRO_BOARD]).areas()[0].searches == 1


# --- detection -----------------------------------------------------------------------------------


def test_detects_getro_and_consider_pages():
    getro = detect_board(fixture_text("vcboards_getro_board.html"), GETRO_BOARD)
    assert (getro.platform, getro.board_id) == ("getro", "4242")
    consider = detect_board(fixture_text("vcboards_consider_board.html"), CONSIDER_BOARD, "session=abc")
    assert (consider.platform, consider.board_id, consider.csrf) == ("consider", "example-capital", CSRF)
    assert consider.is_parent and consider.cookie == "session=abc"


@pytest.mark.parametrize(
    "page",
    [
        "<html><body>Our portfolio is hiring</body></html>",
        '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}}}</script>',
        '<script id="__NEXT_DATA__">{"props":{"pageProps":{"network":{"id":"../../x"}}}}</script>',
        "<script>window.serverInitialData = {\"board\": {\"id\": \"x\"}};</script>",  # no csrf token
        "<script>window.serverInitialData = {broken</script>",
    ],
)
def test_detection_rejects_other_pages(page):
    assert detect_board(page, GETRO_BOARD) is None


def test_unknown_platform_fails_that_board_only():
    fake = FakeBoards(page_html={"jobs.example-ventures.com": "<html><body>Hiring</body></html>"})
    result = run(make_source(fake, boards=[GETRO_BOARD]))
    assert not result.complete and result.jobs == []
    assert isinstance(result.error, SearchError)
    assert str(result.error) == "This board isn't on Getro or Consider, so it can't be read."
    assert len(fake.requests) == 1  # the page only, no search


def test_page_fetched_once_per_board_per_run():
    fake = FakeBoards()
    source = make_source(fake, boards=[GETRO_BOARD], queries=["engineer", "analyst"])
    run(source)
    run(source)  # the same board again in this run: no second page fetch
    pages = [r for r in fake.requests if r.method == "GET"]
    assert len(pages) == 1
    assert str(pages[0].url) == f"{GETRO_BOARD}/jobs"
    assert pages[0].headers["User-Agent"] == USER_AGENT


# --- Getro ---------------------------------------------------------------------------------------


def test_getro_request_shape():
    fake = FakeBoards()
    sleeps: list[float] = []
    result = run(make_source(fake, sleeps=sleeps, boards=[GETRO_BOARD], queries=["AI engineer"], locations=["Italy", "Remote"]))
    assert result.complete and result.error is None and result.billed == 0
    search = [r for r in fake.requests if r.method == "POST"]
    assert len(search) == 1
    request = search[0]
    assert str(request.url) == GETRO_API
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Origin"] == GETRO_BOARD
    assert request.headers["Referer"] == f"{GETRO_BOARD}/"
    assert request.headers["User-Agent"] == USER_AGENT
    assert json.loads(request.content) == {
        "hitsPerPage": 20,
        "page": 0,
        "filters": {"searchable_locations": ["Italy", "Remote"]},
        "query": "AI engineer",
    }
    assert sleeps == [PAUSE_SECONDS]  # one pause, between the page and the search


def test_getro_without_query_or_locations_sends_an_empty_filters_object():
    fake = FakeBoards()
    run(make_source(fake, boards=[GETRO_BOARD]))
    assert fake.bodies(GETRO_API) == [{"hitsPerPage": 20, "page": 0, "filters": {}, "query": ""}]


def test_getro_pages_by_index_until_count():
    pages = [{"results": {"jobs": [getro_job(100 * p + i) for i in range(20 if p < 2 else 5)]}} for p in range(3)]
    fake = FakeBoards(getro_pages=pages, getro_count=45)
    calls: list = []
    sleeps: list[float] = []
    result = run(make_source(fake, sleeps=sleeps, boards=[GETRO_BOARD], max_results_per_board=500), calls=calls)
    assert [b["page"] for b in fake.bodies(GETRO_API)] == [0, 1, 2]
    assert len(result.jobs) == 45
    assert sleeps == [PAUSE_SECONDS] * 3
    assert calls[0] == (0, 1, 0) and calls[-1] == (1, 1, 45)


def test_getro_stops_at_the_cap():
    pages = [{"results": {"jobs": [getro_job(100 * p + i) for i in range(20)]}} for p in range(5)]
    fake = FakeBoards(getro_pages=pages, getro_count=100)
    result = run(make_source(fake, boards=[GETRO_BOARD], max_results_per_board=25))
    assert [b["page"] for b in fake.bodies(GETRO_API)] == [0, 1]
    assert len(result.jobs) == 25


def test_getro_parsing():
    fake = FakeBoards()
    result = run(make_source(fake, boards=[GETRO_BOARD], queries=["engineer"]))
    jobs = {j.job_id: j for j in result.jobs}
    assert set(jobs) == {"getro:5001", "getro:5002"}  # 5003 is older than the window
    ml = jobs["getro:5001"]
    assert ml.source == "vcboards"
    assert ml.title == "Machine Learning Engineer"
    assert ml.company == "Kestrel Payments"
    assert ml.location == "Milan, Italy; Remote"
    assert ml.url == "https://jobs.ashbyhq.com/kestrel-payments/ml-engineer"
    assert ml.posted == "2026-11-10"
    assert ml.salary == "EUR 44,200–66,400 a year"
    assert ml.keyword == "engineer" and ml.area == "jobs.example-ventures.com"
    assert "Kestrel Payments (series b, Fintech, Payments)" in ml.description
    assert "Seniority: entry level" in ml.description and "Skills: Python, PyTorch" in ml.description
    analyst = jobs["getro:5002"]
    assert analyst.url == f"{GETRO_BOARD}/companies/fjord-labs/jobs/5002-data-analyst"  # no ATS link: the board's page
    assert analyst.location == "Oslo, Norway"
    assert analyst.salary == ""
    assert analyst.description.startswith("Company: Fjord Labs\n")  # stage "other" says nothing


def test_getro_hidden_salary_and_long_fields():
    job = parse_getro_job(
        getro_job(
            9,
            compensation_amount_min_cents=5000000,
            compensation_currency="EUR",
            compensation_public=False,
            locations=[f"City {i}" for i in range(10)],
            description="<p>Build <b>models</b>.</p>" + "x" * 9000,
        ),
        board_url=GETRO_BOARD,
        keyword="",
        area="Example",
    )
    assert job.salary == ""
    assert job.location.endswith("City 5 and 4 more")
    assert job.description.startswith("Build models.") and len(job.description) == DESCRIPTION_LIMIT


# --- Consider ------------------------------------------------------------------------------------


def test_consider_csrf_cookie_flow_and_request_shape():
    fake = FakeBoards()
    result = run(make_source(fake, boards=[CONSIDER_BOARD], queries=["AI"], locations=["Italy"]))
    assert result.complete and result.error is None
    page, search = fake.requests
    assert page.method == "GET" and str(page.url) == f"{CONSIDER_BOARD}/jobs"
    assert str(search.url) == CONSIDER_API
    assert search.headers["x-csrf-token"] == CSRF
    assert "session=abc123" in search.headers["Cookie"] and "session.sig=sig456" in search.headers["Cookie"]
    assert search.headers["Accept"] == "application/json"
    assert search.headers["User-Agent"] == USER_AGENT
    assert json.loads(search.content) == {
        "meta": {"size": 100},
        "board": {"id": "example-capital", "isParent": True},
        "query": {"titlePrefix": "AI", "locations": ["Italy"]},
        "grouped": False,
    }


def test_consider_without_filters_sends_an_empty_query():
    fake = FakeBoards()
    run(make_source(fake, boards=[CONSIDER_BOARD]))
    assert fake.bodies(CONSIDER_API)[0]["query"] == {}  # an empty locations list would match nothing


def test_consider_follows_the_cursor():
    pages = [
        {"jobs": [consider_job(i) for i in range(100)], "meta": {"sequence": "seq-1"}, "total": 150},
        {"jobs": [consider_job(100 + i) for i in range(50)], "meta": {"sequence": "seq-2"}, "total": 150},
    ]
    fake = FakeBoards(consider_pages=pages)
    result = run(make_source(fake, boards=[CONSIDER_BOARD], max_results_per_board=1000))
    bodies = fake.bodies(CONSIDER_API)
    assert [b["meta"] for b in bodies] == [{"size": 100}, {"size": 100, "sequence": "seq-1"}]
    assert len(result.jobs) == 150  # stopped once the total was read, though a cursor came back


def test_consider_stops_without_cursor_or_on_a_repeated_one():
    pages = [
        {"jobs": [consider_job(1)], "meta": {"sequence": "seq-1"}},
        {"jobs": [consider_job(2)], "meta": {"sequence": "seq-1"}},  # the same cursor again
    ]
    fake = FakeBoards(consider_pages=pages)
    result = run(make_source(fake, boards=[CONSIDER_BOARD]))
    assert len(fake.bodies(CONSIDER_API)) == 2
    assert len(result.jobs) == 2


def test_consider_cap_sets_page_size():
    pages = [{"jobs": [consider_job(i) for i in range(30)], "meta": {"sequence": "seq-1"}, "total": 900}]
    fake = FakeBoards(consider_pages=pages)
    result = run(make_source(fake, boards=[CONSIDER_BOARD], max_results_per_board=30))
    assert [b["meta"] for b in fake.bodies(CONSIDER_API)] == [{"size": 30}]
    assert len(result.jobs) == 30


def test_consider_parsing():
    fake = FakeBoards()
    result = run(make_source(fake, boards=[CONSIDER_BOARD]))
    jobs = {j.job_id: j for j in result.jobs}
    assert set(jobs) == {"consider:c-101", "consider:c-102"}  # c-103 is older than the window
    ai = jobs["consider:c-101"]
    assert ai.title == "AI Engineer" and ai.company == "Kestrel Payments"
    assert ai.url == "https://kestrel-payments.example.com/careers/ai-engineer"  # the apply link first
    assert ai.location == "Turin, IT; Remote"
    assert ai.posted == "2026-11-10"
    assert ai.salary == "EUR 38,000–45,000 a year"
    assert "Company: Kestrel Payments (40 people, Fintech)" in ai.description
    assert "Seniority: Junior" in ai.description and "Experience: at least 1 year\n" in ai.description
    designer = jobs["consider:c-102"]
    assert designer.url == "https://fjord-labs.example.com/jobs/designer"
    assert designer.salary == ""


# Some Consider boards send each location as the Python form of a list of places (seen live on
# 2026-11-13); the place below is fictional but the shape is the one those boards send.
PACKED_PLACE = (
    "[{'id': 9001, 'name': 'Port Example City', 'location': 'Port Example, Examplestan', "
    "'location_key': 'port example, examplestan', 'country': 'Examplestan', 'country_code': 'EX', "
    "'state': 'Example State', 'city': 'Port Example', 'remote': False, 'workplace': 'office'}]"
)


@pytest.mark.parametrize(
    ("locations", "expected"),
    [
        ([PACKED_PLACE], "Port Example City"),
        ([PACKED_PLACE, PACKED_PLACE.replace("Port Example City", "Lakeside")], "Port Example City; Lakeside"),
        (['[{"id": 9001, "name": "Port Example City"}]'], "Port Example City"),  # the same, as JSON
        ([[{"id": 9001, "name": "Port Example City"}]], "Port Example City"),  # a list inside the list
        ([["Port Example", ["Lakeside"]], "Port Example"], "Port Example; Lakeside"),  # lists of lists
        ([{"id": 9001, "city": "Port Example"}], "Port Example"),  # an object named only by its city
        (["[Remote] Port Example"], "[Remote] Port Example"),  # brackets in a real name stay
    ],
)
def test_consider_locations_in_every_shape_become_names(locations, expected):
    job = parse_consider_job(consider_job(1, locations=locations), board_url=CONSIDER_BOARD, keyword="", area="X")
    assert job.location == expected


def test_consider_unreadable_packed_location_falls_back_to_normalized_ones():
    truncated = PACKED_PLACE[:60]  # cut short, so it can't be read
    item = consider_job(
        1,
        locations=[truncated, "{'id': 9001}"],  # neither gives a name
        normalizedLocations=[{"id": "Port Example", "label": "Port Example, EX", "value": "Port Example"}],
    )
    job = parse_consider_job(item, board_url=CONSIDER_BOARD, keyword="", area="X")
    assert job.location == "Port Example, EX"
    assert "{" not in job.description and "[" not in job.location


def test_consider_packed_locations_through_the_search():
    page = {"jobs": [consider_job(1, locations=[PACKED_PLACE])], "meta": {}, "total": 1}
    result = run(make_source(FakeBoards(consider_pages=[page]), boards=[CONSIDER_BOARD]))
    assert [j.location for j in result.jobs] == ["Port Example City"]


def test_consider_job_without_links_points_at_the_board():
    job = parse_consider_job(consider_job(5, url="javascript:alert(1)"), board_url=CONSIDER_BOARD, keyword="", area="X")
    assert job.url == CONSIDER_BOARD


# --- shared behaviour ----------------------------------------------------------------------------


def test_window_keeps_jobs_without_a_date():
    pages = [{"results": {"jobs": [getro_job(1, created_at=None), getro_job(2, days_ago=30)], "count": 2}}]
    result = run(make_source(FakeBoards(getro_pages=pages), boards=[GETRO_BOARD]))
    assert [j.job_id for j in result.jobs] == ["getro:1"]


def test_dedup_across_queries():
    fake = FakeBoards()
    calls: list = []
    result = run(make_source(fake, boards=[GETRO_BOARD], queries=["engineer", "analyst"]), calls=calls)
    assert len(fake.bodies(GETRO_API)) == 2
    assert sorted(j.job_id for j in result.jobs) == ["getro:5001", "getro:5002"]
    assert result.jobs[0].keyword == "engineer"  # the first query that found a job keeps it
    assert calls[-1] == (2, 2, 2)


def test_malformed_items_do_not_lose_the_others():
    items = ["not a job", None, {"title": "No id"}, getro_job(7, organization="Fjord Labs", locations=5), getro_job(8)]
    pages = [{"results": {"jobs": items, "count": 5}}]
    result = run(make_source(FakeBoards(getro_pages=pages), boards=[GETRO_BOARD]))
    assert sorted(j.job_id for j in result.jobs) == ["getro:7", "getro:8"]
    assert result.complete


def test_one_parse_crash_skips_only_that_job(monkeypatch):
    from jobscout.sources import vcboards

    real = vcboards.parse_getro_job

    def flaky(item, **kwargs):
        if item.get("id") == 5001:
            raise RuntimeError("boom")
        return real(item, **kwargs)

    monkeypatch.setattr(vcboards, "parse_getro_job", flaky)
    result = run(make_source(FakeBoards(), boards=[GETRO_BOARD]))
    assert [j.job_id for j in result.jobs] == ["getro:5002"]


def test_http_error_keeps_earlier_pages_and_hides_addresses():
    pages = [{"results": {"jobs": [getro_job(i) for i in range(20)]}}]
    fake = FakeBoards(getro_pages=pages, getro_count=60)
    source = make_source(fake, boards=[GETRO_BOARD])
    first_search = True

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal first_search
        if request.method == "POST" and not first_search:
            fake.requests.append(request)
            return httpx.Response(500)
        if request.method == "POST":
            first_search = False
        return fake(request)

    source._client = httpx.Client(transport=httpx.MockTransport(flaky))
    result = run(source)
    assert not result.complete
    assert len(result.jobs) == 20
    message = str(result.error)
    assert message == "The jobs.example-ventures.com board answered with an error (HTTP 500)."
    assert "://" not in message and "getro.com" not in message and "4242" not in message


def test_consider_rejected_token_is_an_error_without_the_token():
    fake = FakeBoards()
    fake.fail["consider"] = httpx.Response(412, json={"error": "INVALID_CSRF"})
    result = run(make_source(fake, boards=[CONSIDER_BOARD]))
    assert not result.complete and result.jobs == []
    assert "HTTP 412" in str(result.error) and CSRF not in str(result.error) and "abc123" not in str(result.error)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.Response(404), "The jobs.example-ventures.com board wasn't found (HTTP 404). Check the address."),
        (httpx.Response(403), "The jobs.example-ventures.com board answered with an error (HTTP 403)."),
        (httpx.Response(503), "The jobs.example-ventures.com board answered with an error (HTTP 503)."),
        (httpx.ConnectError("no route"), "The jobs.example-ventures.com board couldn't be reached. Check your internet connection."),
        (httpx.ReadTimeout("slow"), "The jobs.example-ventures.com board took too long to answer. It will be retried next run."),
    ],
)
def test_board_page_failures(failure, expected):
    fake = FakeBoards()
    fake.fail["page"] = failure
    result = run(make_source(fake, boards=[GETRO_BOARD]))
    assert not result.complete and result.jobs == []
    assert str(result.error) == expected


def test_board_page_retries_a_passing_failure():
    fake = FakeBoards()
    fake.fail["page"] = httpx.Response(503)
    sleeps: list[float] = []
    source = make_source(fake, sleeps=sleeps, boards=[GETRO_BOARD])
    real = fake._page

    def recover(request):
        if len([r for r in fake.requests if r.method == "GET"]) >= 2:
            fake.fail.pop("page", None)
        return real(request)

    fake._page = recover
    result = run(source)
    assert result.complete and len(result.jobs) == 2
    assert len([r for r in fake.requests if r.method == "GET"]) == 2
    assert sleeps == [1, PAUSE_SECONDS, PAUSE_SECONDS]  # back-off, then the pauses before the retry and the search


def test_cancel_between_requests():
    pages = [{"results": {"jobs": [getro_job(100 * p + i) for i in range(20)]}} for p in range(3)]
    fake = FakeBoards(getro_pages=pages, getro_count=60)
    cancel = threading.Event()
    source = make_source(fake, cancel=cancel, boards=[GETRO_BOARD], queries=["a", "b"])
    area = source.areas()[0]

    def progress(done, total, n):
        if n:
            cancel.set()

    result = source.search_area(area, WEEK, progress)
    assert not result.complete and result.error is None
    assert len(result.jobs) == 20
    assert len(fake.bodies(GETRO_API)) == 1


def test_cancelled_before_start_makes_no_request():
    fake = FakeBoards()
    cancel = threading.Event()
    cancel.set()
    result = run(make_source(fake, cancel=cancel, boards=[GETRO_BOARD]))
    assert not result.complete and fake.requests == []
