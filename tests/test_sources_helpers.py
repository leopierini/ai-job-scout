from datetime import date

import httpx
import pytest

from jobscout.errors import SearchError
from jobscout.sources import (
    fetch_json,
    get_public,
    html_to_text,
    is_public_web_url,
    posted_date,
    salary_text,
    within_window,
)

TODAY = date(2026, 11, 12)
DAY = 86400


@pytest.mark.parametrize(
    ("args", "text"),
    [
        ((38000, 45000, "eur", "year"), "EUR 38,000–45,000 a year"),
        ((45000, 38000, "EUR", "yearly"), "EUR 38,000–45,000 a year"),  # swapped
        ((50, 65, "EUR", "year"), "EUR 50,000–65,000 a year"),  # written in thousands
        ((None, 3000, "EUR", "monthly"), "EUR up to 3,000 a month"),
        ((30, None, "GBP", "hour"), "GBP from 30 an hour"),
        (("42,000", "42000", "", ""), "42,000"),
        ((None, None, "EUR", "year"), ""),
        ((0, -1, "EUR", "year"), ""),
        ((True, "abc", "EUR", "year"), ""),
    ],
)
def test_salary_text(args, text):
    assert salary_text(*args) == text


def test_within_window_keeps_the_first_day_and_undated_jobs():
    assert within_window("2026-11-09", 3 * DAY, TODAY)
    assert not within_window("2026-11-08", 3 * DAY, TODAY)
    assert within_window("2026-11-11", 60, TODAY)  # less than a day rounds up to one
    assert within_window("", DAY, TODAY) and within_window("soon", DAY, TODAY)


def test_posted_date_reads_iso_text_and_epochs():
    assert posted_date("2026-11-01T10:00:00Z") == "2026-11-01"
    assert posted_date(1762905600) == posted_date(1762905600000) == "2025-11-12"
    assert posted_date("2 days ago") == posted_date(None) == posted_date(True) == ""


def test_html_to_text_keeps_structure_and_drops_scripts():
    text = html_to_text("<h2>About</h2><p>Build &amp; ship.</p><ul><li>Python</li></ul><script>x()</script>")
    assert text.splitlines()[0] == "About"
    assert "Build & ship." in text and "- Python" in text and "x()" not in text
    assert html_to_text("plain &amp; simple") == "plain & simple"
    assert html_to_text(None) == ""


def test_fetch_json_errors_never_name_the_address():
    def answer(status):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status)))

    url = "https://jobs.example-board.com/api?secret=1"
    for status, words in ((404, "wasn't found"), (429, "slow down"), (403, "HTTP 403")):
        with pytest.raises(SearchError) as caught:
            fetch_json(answer(status), "GET", url, "Example Board", sleep=lambda _s: None)
        assert words in str(caught.value) and "example-board.com" not in str(caught.value)


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://jobs.example-board.com/jobs", True),
        ("http://203.0.113.9/jobs", False),  # documentation range: not global
        ("https://8.8.8.8/", True),
        ("http://localhost:8765/api", False),
        ("http://127.0.0.1/", False),
        ("http://192.168.1.1/admin", False),
        ("http://10.0.0.5/", False),
        ("http://169.254.169.254/latest", False),
        ("http://[::1]/", False),
        ("http://router.local/", False),
        ("http://intranet/", False),
        ("ftp://jobs.example-board.com/", False),
    ],
)
def test_is_public_web_url(url, public):
    assert is_public_web_url(url) is public


def test_get_public_refuses_a_redirect_to_a_private_address():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SearchError) as caught:
        get_public(client, "https://careers.example-labs.com/", "The careers page of Example Labs", headers={})
    assert seen == ["https://careers.example-labs.com/"]  # the private address is never requested
    assert "192.168" not in str(caught.value) and "own computer or network" in str(caught.value)


def test_get_public_follows_public_redirects():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = get_public(client, "https://careers.example-labs.com/old", "Example Labs", headers={})
    assert response.text == "ok" and str(response.url) == "https://careers.example-labs.com/new"
