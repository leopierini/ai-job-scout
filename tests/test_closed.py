import json
import threading

import httpx
import pytest
from conftest import make_row

from jobscout.closed import (
    BOARD_REASON,
    CLOSED_ACTOR,
    LINKEDIN_REASON,
    check_closed,
    linkedin_job_id,
    linkedin_verdicts,
)
from jobscout.sources.apify import ApifyClient

RUN = rf".*acts/{CLOSED_ACTOR}/run-sync-get-dataset-items.*"


def _li(job_id: str, **kw):
    url = f"https://uk.linkedin.com/jobs/view/product-intern-at-fjord-labs-{job_id}?position=1&refId=x"
    return make_row(source="linkedin", job_id=job_id, url=url, **kw)


def _job(job_id: str, **kw):
    return {"type": "job", "status": "ok", "jobId": job_id, "jobState": "open", "isClosed": False,
            "applyButton": "offsite", **kw}  # fmt: skip


def _check(rows, client):
    return check_closed(rows, ApifyClient("tok", client, sleep=lambda _s: None), client, threading.Event())


def test_linkedin_job_id():
    assert linkedin_job_id("https://uk.linkedin.com/jobs/view/associate-pm-at-acme-4478176641?position=5") == "4478176641"
    assert linkedin_job_id("https://www.linkedin.com/jobs/view/4478176641/") == "4478176641"
    assert linkedin_job_id("https://example.com/jobs/view/4478176641") is None
    assert linkedin_job_id("https://www.linkedin.com/company/fjord-labs") is None


def test_linkedin_verdicts():
    items = [
        _job("1", isClosed=True, jobState="closed"),
        _job("2", jobState="closed"),
        _job("3", applyButton=None),
        _job("4"),
        {"type": "status", "status": "not_found", "input": "https://www.linkedin.com/jobs/view/4400000005"},
        {"type": "status", "status": "blocked", "input": "https://www.linkedin.com/jobs/view/4400000006"},
        {"type": "status", "status": "failed", "input": "4400000007"},
        {"type": "status", "status": "not_found", "input": "4400000008"},
        {"type": "summary", "closed": 2},
    ]
    assert linkedin_verdicts(items) == {"1": True, "2": True, "3": True, "4": False, "4400000005": True, "4400000008": True}


def test_check_closed_linkedin(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[_job("11", isClosed=True, jobState="closed"), _job("12")])
    rows = [_li("11"), _li("12")]
    with httpx.Client() as client:
        result = _check(rows, client)
    assert result.closed == {rows[0].pk: LINKEDIN_REASON}
    assert (result.linkedin_jobs, result.actor_runs, result.note) == (2, 1, "")
    payload = json.loads(route.calls[0].request.content)
    assert payload["jobUrls"] == ["https://www.linkedin.com/jobs/view/11", "https://www.linkedin.com/jobs/view/12"]
    assert payload["exactPostedDate"] is False
    assert payload["includeDescriptionHtml"] is False
    assert payload["includeDescriptionMarkdown"] is False


def test_check_closed_batches(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(200, json=[])
    rows = [_li(str(1000 + i)) for i in range(101)]
    with httpx.Client() as client:
        result = _check(rows, client)
    assert route.call_count == 2
    assert result.actor_runs == 2
    assert result.closed == {}


def test_check_closed_apify_limit(respx_mock):
    respx_mock.post(url__regex=RUN).respond(403, text='{"error":{"type":"platform-feature-disabled","message":"Monthly usage hard limit exceeded"}}')
    with httpx.Client() as client:
        result = _check([_li("21")], client)
    assert result.closed == {}
    assert "limit" in result.note.lower()


ASHBY_FEED = "https://api.ashbyhq.com/posting-api/job-board/fjordlabs?includeCompensation=true"
GREENHOUSE_FEED = "https://boards-api.greenhouse.io/v1/boards/nordwind/jobs?content=true&pay_transparency=true"


def _ashby(uuid: str, **kw):
    return make_row(source="atsindex", job_id=f"ashby:{uuid}", url=f"https://jobs.ashbyhq.com/fjordlabs/{uuid}", **kw)


def test_check_closed_board(respx_mock):
    ashby = respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [
        {"id": "aaaa-1111", "title": "Product Intern", "jobUrl": "https://jobs.ashbyhq.com/fjordlabs/aaaa-1111"},
    ]})  # fmt: skip
    respx_mock.get(GREENHOUSE_FEED).respond(200, json={"jobs": [
        {"id": 4242, "title": "APM", "absolute_url": "https://job-boards.greenhouse.io/nordwind/jobs/4242"},
    ]})  # fmt: skip
    gone, still, gh = (
        _ashby("bbbb-2222"),
        _ashby("aaaa-1111"),
        make_row(source="vcboards", job_id="getro:9", url="https://job-boards.greenhouse.io/nordwind/jobs/4242?gh_src=x"),
    )
    with httpx.Client() as client:
        result = _check([gone, still, gh], client)
    assert result.closed == {gone.pk: BOARD_REASON}
    assert ashby.call_count == 1


@pytest.mark.parametrize("answer", [dict(status_code=200, json={"jobs": []}), dict(status_code=500, text="oops")])
def test_board_with_no_clear_answer_keeps_the_job(respx_mock, answer):
    respx_mock.get(ASHBY_FEED).respond(**answer)
    with httpx.Client() as client:
        result = _check([_ashby("bbbb-2222")], client)
    assert result.closed == {}


def test_other_sources_are_not_checked(respx_mock):
    rows = [
        make_row(source="indeed", job_id="i1", url="https://uk.indeed.com/viewjob?jk=abc"),
        make_row(source="wttj", job_id="w1", url="https://www.welcometothejungle.com/en/companies/fjord/jobs/pm"),
    ]
    with httpx.Client() as client:
        result = _check(rows, client)
    assert result.closed == {} and result.actor_runs == 0
    assert not respx_mock.calls


def test_without_apify_only_boards_are_checked(respx_mock):
    respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [{"id": "aaaa-1111", "title": "PM"}]})
    gone = _ashby("bbbb-2222")
    with httpx.Client() as client:
        result = check_closed([_li("31"), gone], None, client, threading.Event())
    assert result.closed == {gone.pk: BOARD_REASON}
    assert result.actor_runs == 0
