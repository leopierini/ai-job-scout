from datetime import datetime, timedelta

import httpx
import pytest

from jobscout.costs import CostEstimate, apify_credit, billed_cost, estimate
from jobscout.settings import Region, Settings
from jobscout.windows import indeed_from_days, window_seconds

NOW = datetime(2026, 11, 1, 12, 0)
LIMITS_URL = "https://api.apify.com/v2/users/me/limits"


def test_window_no_record():
    assert window_seconds(None, NOW, 15, 60) == 15 * 86400


def test_window_since_last():
    assert window_seconds(NOW - timedelta(days=2), NOW, 15, 60) == 2 * 86400 + 3600


def test_window_override():
    assert window_seconds(NOW - timedelta(days=2), NOW, 15, 60, override_days=7) == 7 * 86400


def test_window_override_wins_without_record():
    assert window_seconds(None, NOW, 15, 60, override_days=3) == 3 * 86400


def test_window_never_below_overlap_when_last_start_is_in_the_future():
    assert window_seconds(NOW + timedelta(hours=2), NOW, 15, 60) == 3600


def test_window_returns_int():
    assert isinstance(window_seconds(NOW - timedelta(seconds=90), NOW, 15, 60), int)


def test_indeed_from_days():
    assert [indeed_from_days(d * 86400) for d in (1, 2, 4, 8, 30)] == [1, 3, 7, 14, 14]


def test_indeed_from_days_rounds_partial_days_up():
    assert indeed_from_days(86400 + 1) == 3
    assert indeed_from_days(0) == 1


def test_estimate_linkedin_only():
    s = Settings()
    s.linkedin.keywords = ["a", "b", "c"]
    s.linkedin.regions = [Region(name="UK", geo_id="1"), Region(name="CH", geo_id="2")]
    e = estimate(s)
    assert (e.linkedin_searches, e.linkedin_max_results, e.indeed_searches) == (6, 150, 0)
    assert e.max_usd == pytest.approx(150 * 0.0003)


def test_estimate_with_indeed():
    s = Settings()
    s.linkedin.enabled = False
    s.indeed.enabled = True
    s.indeed.countries = ["gb", "ch"]
    s.indeed.queries = ["analyst", "marketing", "sales"]  # job_types default: 2
    e = estimate(s)
    assert (e.linkedin_searches, e.linkedin_max_results) == (0, 0)
    # The default actor runs each query once for every job type.
    assert (e.indeed_searches, e.indeed_max_results) == (6, 120)
    assert e.max_usd == pytest.approx(120 * 0.00005)


def test_estimate_with_an_indeed_actor_that_splits_job_types():
    s = Settings()
    s.linkedin.enabled = False
    s.indeed.enabled = True
    s.indeed.actor = "borderline~indeed-scraper"
    s.indeed.price_per_result = 0.005
    s.indeed.countries = ["gb", "ch"]
    s.indeed.queries = ["analyst", "marketing", "sales"]  # job_types default: 2
    e = estimate(s)
    assert (e.indeed_searches, e.indeed_max_results) == (12, 240)
    assert e.max_usd == pytest.approx(240 * 0.005)


def test_estimate_empty_settings_is_free():
    assert estimate(Settings()) == CostEstimate(0, 0, 0, 0, 0.0)


def test_estimate_with_apify_boards_and_free_sources():
    s = Settings()
    s.linkedin.enabled = False
    s.wttj.enabled = True
    s.wttj.queries = ["ai engineer", "product engineer"]
    s.wttj.locations = ["IT", "FR", "DE"]
    s.wttj.price_per_result = 0.001
    s.yc.enabled = True
    s.yc.queries = ["engineer"]  # no locations: one search
    s.yc.price_per_result = 0.0001
    s.wellfound.queries = ["engineer"]  # off: not counted
    s.careers.enabled = True  # free: not counted
    s.careers.pages = ["https://jobs.ashbyhq.com/example-labs"]
    e = estimate(s)
    assert (e.board_searches, e.board_max_results) == (7, 175)
    assert (e.searches, e.max_results) == (7, 175)
    assert e.max_usd == pytest.approx(150 * 0.001 + 25 * 0.0001)
    s.wellfound.enabled = True  # a start fee for each search
    s.wellfound.price_per_result = 0.0
    assert estimate(s).max_usd == pytest.approx(e.max_usd + 0.005)


def test_billed_cost():
    s = Settings()
    assert billed_cost({"linkedin": 100, "indeed": 10}, s) == pytest.approx(100 * 0.0003 + 10 * 0.00005)
    s.wttj.price_per_result = 0.002
    assert billed_cost({"wttj": 10, "careers": 99}, s) == pytest.approx(10 * 0.002)  # free sources cost nothing
    assert billed_cost({}, s) == 0.0


def _limits(max_usd, used):
    return {"data": {"limits": {"maxMonthlyUsageUsd": max_usd}, "current": {"monthlyUsageUsd": used}}}


def test_apify_credit_remaining(respx_mock):
    route = respx_mock.get(LIMITS_URL).respond(200, json=_limits(5.0, 1.25))
    with httpx.Client() as client:
        assert apify_credit("tok", client) == pytest.approx(3.75)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"
    assert "tok" not in str(route.calls.last.request.url)


def test_apify_credit_never_negative(respx_mock):
    respx_mock.get(LIMITS_URL).respond(200, json=_limits(5.0, 6.0))
    with httpx.Client() as client:
        assert apify_credit("tok", client) == 0.0


def test_apify_credit_none_on_error(respx_mock):
    respx_mock.get(LIMITS_URL).respond(500)
    with httpx.Client() as client:
        assert apify_credit("tok", client) is None


def test_apify_credit_none_on_network_error(respx_mock):
    respx_mock.get(LIMITS_URL).mock(side_effect=httpx.ConnectError("boom"))
    with httpx.Client() as client:
        assert apify_credit("tok", client) is None


@pytest.mark.parametrize("body", [{}, {"data": {}}, {"data": {"limits": {}, "current": {}}}, {"data": []}, [1], {"data": _limits(1, 1)["data"] | {"current": {"monthlyUsageUsd": "x"}}}])
def test_apify_credit_none_on_unexpected_shape(respx_mock, body):
    respx_mock.get(LIMITS_URL).respond(200, json=body)
    with httpx.Client() as client:
        assert apify_credit("tok", client) is None


def test_apify_credit_none_on_non_json(respx_mock):
    respx_mock.get(LIMITS_URL).respond(200, text="not json")
    with httpx.Client() as client:
        assert apify_credit("tok", client) is None
