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
    rows = [_li(str(4400001000 + i)) for i in range(51)]
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


# --- review fixes: only a clear answer archives ---

LEVER_FEED = "https://api.lever.co/v0/postings/fjord?mode=json"
RECRUITEE_FEED = "https://fjord.recruitee.com/api/offers/"


def test_missing_apply_button_key_is_not_closed():
    assert linkedin_verdicts([{"type": "job", "jobId": "1", "jobState": "open", "isClosed": False}]) == {"1": False}


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.lever.co/fjord",  # the board itself, no job id
        "https://jobs.ashbyhq.com/fjordlabs?utm_source=newsletter",
        "https://click.tracker.example/r/abc?u=https://boards.greenhouse.io/nordwind/jobs/4242",  # a tracking link
    ],
)
def test_board_urls_without_a_job_id_are_kept(respx_mock, url):
    respx_mock.get(LEVER_FEED).respond(200, json=[{"id": "cccc-3333", "text": "PM"}])
    respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [{"id": "aaaa-1111", "title": "PM"}]})
    respx_mock.get(GREENHOUSE_FEED).respond(200, json={"jobs": [{"id": 999, "title": "PM"}]})
    with httpx.Client() as client:
        result = _check([make_row(source="wellfound", job_id="w1", url=url)], client)
    assert result.closed == {}


@pytest.mark.parametrize("listed,closed", [(4242, False), (999, True)])
def test_greenhouse_id_in_the_query(respx_mock, listed, closed):
    respx_mock.get(GREENHOUSE_FEED).respond(200, json={"jobs": [{"id": listed, "title": "PM"}]})
    row = make_row(source="atsindex", job_id="gh:4242", url="https://boards.greenhouse.io/nordwind?gh_jid=4242")
    with httpx.Client() as client:
        assert bool(_check([row], client).closed) is closed


def test_recruitee_jobs_match_by_slug(respx_mock):
    respx_mock.get(RECRUITEE_FEED).respond(200, json={"offers": [
        {"id": 77, "title": "PM", "slug": "product-manager", "careers_url": "https://careers.fjord.example/o/product-manager"},
    ]})  # fmt: skip
    still = make_row(source="vcboards", job_id="r1", url="https://fjord.recruitee.com/o/product-manager")
    gone = make_row(source="vcboards", job_id="r2", url="https://fjord.recruitee.com/l/en/o/old-role")
    with httpx.Client() as client:
        assert _check([still, gone], client).closed == {gone.pk: BOARD_REASON}


def test_careers_rows_use_their_stored_id(respx_mock):
    respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [{"id": "aaaa-1111", "title": "PM"}]})
    still = make_row(source="careers", job_id="ashby:fjordlabs:aaaa-1111", url="https://jobs.ashbyhq.com/fjordlabs")
    gone = make_row(source="careers", job_id="ashby:fjordlabs:bbbb-2222", url="https://jobs.ashbyhq.com/fjordlabs")
    with httpx.Client() as client:
        assert _check([still, gone], client).closed == {gone.pk: BOARD_REASON}


def test_unlisted_ashby_job_still_counts_as_open(respx_mock):
    respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [
        {"id": "aaaa-1111", "title": "PM"}, {"id": "bbbb-2222", "title": "APM", "isListed": False},
    ]})  # fmt: skip
    with httpx.Client() as client:
        assert _check([_ashby("bbbb-2222")], client).closed == {}


def test_a_failed_batch_does_not_stop_the_others(respx_mock):
    respx_mock.post(url__regex=RUN).mock(side_effect=[
        httpx.Response(400, json={"error": {"type": "run-failed", "message": "Actor run did not succeed"}}),
        httpx.Response(200, json=[_job("4400000099", isClosed=True, jobState="closed")]),
    ])  # fmt: skip
    rows = [_li(str(4400000000 + i)) for i in range(100)]
    with httpx.Client() as client:
        result = _check(rows, client)
    assert result.closed == {rows[-1].pk: LINKEDIN_REASON}
    assert result.actor_runs == 2  # the failed run may have been billed its start
    assert "50 LinkedIn jobs" in result.note


def test_an_apify_limit_stops_every_batch(respx_mock):
    route = respx_mock.post(url__regex=RUN).respond(403, text='{"error":{"message":"Monthly usage hard limit exceeded"}}')
    with httpx.Client() as client:
        result = _check([_li(str(4400000000 + i)) for i in range(100)], client)
    assert route.call_count == 1 and result.closed == {}


def test_progress_is_reported(respx_mock):
    respx_mock.post(url__regex=RUN).respond(200, json=[])
    respx_mock.get(ASHBY_FEED).respond(200, json={"jobs": [{"id": "aaaa-1111", "title": "PM"}]})
    seen = []
    rows = [_li(str(4400000000 + i)) for i in range(60)] + [_ashby("aaaa-1111")]
    with httpx.Client() as client:
        check_closed(rows, ApifyClient("tok", client, sleep=lambda _s: None), client, threading.Event(),
                     progress=lambda done, total: seen.append((done, total)))  # fmt: skip
    assert seen == [(50, 61), (60, 61), (61, 61)]
