import csv
import json

import httpx
import pytest
from conftest import make_row

from jobscout.errors import WebhookError
from jobscout.models import Status
from jobscout.outputs.csv_out import CSV_COLUMNS, export_csv
from jobscout.outputs.webhook import WebhookOutput, job_payload

URL = "https://hooks.example.com/jobs/abc"


def test_export_csv_columns_order_and_bom(tmp_path):
    path = tmp_path / "jobs.csv"
    row = make_row(
        flags=["Asks for SQL", "Needs German"],
        start_date="2027-05-01",
        deadline="2026-12-01",
        recruiter="Ada Example",
        recruiter_title="Talent partner",
        recruiter_url="https://example.com/in/ada",
        title='Analyst, "Junior"',
    )
    assert export_csv([row], path) == 1
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CSV_COLUMNS
    assert rows[0][0] == "Fit score" and rows[0][-1] == "Job ID"
    record = dict(zip(rows[0], rows[1], strict=True))
    assert record["Fit score"] == "80"
    assert record["Title"] == 'Analyst, "Junior"'
    assert record["Status"] == "To review"
    assert record["Flags"] == "Asks for SQL; Needs German"
    assert record["Starts"] == "2027-05-01" and record["Deadline"] == "2026-12-01"
    assert record["Recruiter"] == "Ada Example" and record["Recruiter contact"] == "https://example.com/in/ada"
    assert record["Job URL"] == row.url and record["Job ID"] == row.pk
    assert not list(tmp_path.glob("*.tmp"))


def test_export_csv_sorted_by_score(tmp_path):
    path = tmp_path / "jobs.csv"
    rows = [make_row(job_id="1", score=70), make_row(job_id="2", score=95), make_row(job_id="3", score=None)]
    assert export_csv(rows, path) == 3
    with path.open(encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    assert [r["Job ID"] for r in records] == ["linkedin:2", "linkedin:1", "linkedin:3"]
    assert [r["Fit score"] for r in records] == ["95", "70", ""]


def test_export_csv_replaces_existing_file(tmp_path):
    path = tmp_path / "jobs.csv"
    path.write_text("old", encoding="utf-8")
    assert export_csv([], path) == 0
    assert path.read_text(encoding="utf-8-sig").strip() == ",".join(CSV_COLUMNS)


def test_job_payload_uses_status_label():
    row = make_row(status=Status.NOT_INTERESTED, flags=["x"], recruiter="Ada Example", recruiter_title="TP")
    payload = job_payload(row)
    assert payload["status"] == "Not interested"
    assert payload["id"] == row.pk and payload["job_id"] == row.job_id and payload["source"] == "linkedin"
    assert payload["score"] == 80 and payload["flags"] == ["x"]
    assert set(payload) == {
        "id", "source", "job_id", "title", "company", "location", "salary", "url", "score", "reason", "flags",
        "status", "start_date", "deadline", "posted", "recruiter", "recruiter_title", "recruiter_url",
        "keyword",
    }


def test_webhook_posts_payload(respx_mock):
    route = respx_mock.post(URL).respond(200, json={"ok": True})
    row = make_row()
    out = WebhookOutput(URL, httpx.Client())
    assert out.name == "webhook"
    out.deliver(row)
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == job_payload(row)


def test_webhook_accepts_empty_body(respx_mock):
    respx_mock.post(URL).respond(204)
    WebhookOutput(URL, httpx.Client()).deliver(make_row())


def test_webhook_error_raises(respx_mock):
    route = respx_mock.post(URL).respond(400, text="bad")
    with pytest.raises(WebhookError) as err:
        WebhookOutput(URL, httpx.Client()).deliver(make_row())
    assert "400" in str(err.value) and "abc" not in str(err.value) and "hooks.example.com" not in str(err.value)
    assert route.call_count == 1


def test_webhook_unreachable_raises_without_url(respx_mock):
    sleeps = []
    route = respx_mock.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(WebhookError) as err:
        WebhookOutput(URL, httpx.Client(), sleep=sleeps.append).deliver(make_row())
    assert str(err.value) == "The webhook couldn't be reached. Check the URL and that the service is running."
    assert "ConnectError" not in str(err.value) and "hooks.example.com" not in str(err.value)
    assert route.call_count == 3 and sleeps == [1, 2]


def test_webhook_does_not_retry_read_timeout(respx_mock):
    sleeps = []
    route = respx_mock.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WebhookError) as err:
        WebhookOutput(URL, httpx.Client(), sleep=sleeps.append).deliver(make_row())
    assert str(err.value) == "The webhook took too long to answer. Try again."
    assert "ReadTimeout" not in str(err.value) and "hooks.example.com" not in str(err.value)
    assert route.call_count == 1 and sleeps == []


def test_webhook_invalid_url_raises_without_url():
    bad = "http://hooks.example.com:notaport/secret-token"
    with pytest.raises(WebhookError) as err:
        WebhookOutput(bad, httpx.Client()).deliver(make_row())
    assert "secret-token" not in str(err.value) and "hooks.example.com" not in str(err.value)


def test_export_csv_prefixes_formula_cells(tmp_path):
    path = tmp_path / "jobs.csv"
    row = make_row(
        title="=HYPERLINK(\"http://evil.example\")",
        company="+cmd",
        location="-1+1",
        reason="@SUM(A1)",
        recruiter="Ada Example",
        score=80,
    )
    export_csv([row], path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        record = next(csv.DictReader(f))
    assert record["Title"] == "'=HYPERLINK(\"http://evil.example\")"
    assert record["Company"] == "'+cmd"
    assert record["Location"] == "'-1+1"
    assert record["Reason"] == "'@SUM(A1)"
    assert record["Recruiter"] == "Ada Example"
    assert record["Fit score"] == "80"


def test_webhook_unusual_failure_has_no_class_name(respx_mock):
    respx_mock.post(URL).mock(side_effect=httpx.DecodingError("bad body"))
    with pytest.raises(WebhookError) as err:
        WebhookOutput(URL, httpx.Client()).deliver(make_row())
    assert str(err.value) == "The webhook request failed. Check the URL and try again."
