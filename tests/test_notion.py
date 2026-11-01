import json
from datetime import datetime

import httpx
import pytest
from conftest import make_row

from jobscout.errors import ConfigError, NotionError
from jobscout.models import STATUS_LABELS, Status
from jobscout.outputs.notion import PROPERTIES, NotionClient, parse_page_id

TOKEN = "secret_tok-123"
API = "https://api.notion.com/v1"
DB_ID = "0123456789abcdef0123456789abcdef"
PAGE_ID = "fedcba9876543210fedcba9876543210"


def make_client(sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return NotionClient(TOKEN, httpx.Client(), sleep=sleeps.append)


def body(call):
    return json.loads(call.request.content)


# --- create_database ---


def test_create_database_schema_has_status_options(respx_mock):
    route = respx_mock.post(f"{API}/databases").respond(200, json={"id": "db-new"})
    assert make_client().create_database("page-123") == "db-new"
    sent = body(route.calls[0])
    assert sent["parent"] == {"type": "page_id", "page_id": "page-123"}
    assert sent["title"][0]["text"]["content"] == "AI Job Scout"
    props = sent["properties"]
    assert props == PROPERTIES
    assert [o["name"] for o in props["Status"]["select"]["options"]] == list(STATUS_LABELS.values())
    assert [o["name"] for o in props["Source"]["select"]["options"]] == ["LinkedIn", "Indeed"]
    assert "title" in props["Title"] and "number" in props["Fit Score"] and "multi_select" in props["Flags"]
    for name in ["Company", "Location", "Reason", "Keyword", "Recruiter", "Recruiter Title", "Job ID"]:
        assert "rich_text" in props[name]
    for name in ["Posted", "Starts", "Deadline"]:
        assert "date" in props[name]
    for name in ["Job URL", "Recruiter Contact"]:
        assert "url" in props[name]
    assert len(props) == 17


def test_create_database_custom_title(respx_mock):
    route = respx_mock.post(f"{API}/databases").respond(200, json={"id": "db-new"})
    make_client().create_database("page-123", title="My jobs")
    assert body(route.calls[0])["title"][0]["text"]["content"] == "My jobs"


# --- create_page ---


def test_create_page_maps_every_property(respx_mock):
    route = respx_mock.post(f"{API}/pages").respond(200, json={"id": "page-new"})
    row = make_row(
        flags=["Needs German", "Pay, unclear", "x" * 150],
        start_date="2027-05-01",
        deadline="2026-12-01",
        posted="2026-10-20",
        recruiter="Ada Example",
        recruiter_title="Talent partner",
        recruiter_url="https://example.com/in/ada",
        status=Status.INTERESTED,
        reason="r" * 2500,
    )
    assert make_client().create_page(DB_ID, row) == "page-new"
    sent = body(route.calls[0])
    assert sent["parent"] == {"database_id": DB_ID}
    p = sent["properties"]

    def text(name):
        return p[name]["rich_text"][0]["text"]["content"]

    assert p["Title"]["title"][0]["text"]["content"] == row.title
    assert text("Company") == "Fjord Labs" and text("Location") == "Oslo, Norway"
    assert text("Keyword") == "data analyst intern" and text("Job ID") == "linkedin:100"
    assert text("Recruiter") == "Ada Example" and text("Recruiter Title") == "Talent partner"
    assert len(text("Reason")) == 2000
    assert p["Fit Score"] == {"number": 80}
    assert [o["name"] for o in p["Flags"]["multi_select"]] == ["Needs German", "Pay unclear", "x" * 100]
    assert p["Status"] == {"select": {"name": "Interested"}}
    assert p["Source"] == {"select": {"name": "LinkedIn"}}
    assert p["Posted"] == {"date": {"start": "2026-10-20"}}
    assert p["Starts"] == {"date": {"start": "2027-05-01"}}
    assert p["Deadline"] == {"date": {"start": "2026-12-01"}}
    assert p["Job URL"] == {"url": row.url}
    assert p["Recruiter Contact"] == {"url": "https://example.com/in/ada"}


def test_create_page_skips_non_iso_dates(respx_mock):
    route = respx_mock.post(f"{API}/pages").respond(200, json={"id": "p"})
    row = make_row(posted="3 days ago", start_date="September", deadline="2026-13-45")
    make_client().create_page(DB_ID, row)
    p = body(route.calls[0])["properties"]
    assert "Posted" not in p and "Starts" not in p and "Deadline" not in p


def test_create_page_skips_empty_optionals(respx_mock):
    route = respx_mock.post(f"{API}/pages").respond(200, json={"id": "p"})
    row = make_row(url="", score=None, recruiter_url="", source="indeed", flags=[])
    make_client().create_page(DB_ID, row)
    p = body(route.calls[0])["properties"]
    assert "Job URL" not in p and "Recruiter Contact" not in p and "Fit Score" not in p
    assert p["Flags"] == {"multi_select": []}
    assert p["Recruiter"] == {"rich_text": []}
    assert p["Source"] == {"select": {"name": "Indeed"}}
    assert p["Status"] == {"select": {"name": "To review"}}


def test_create_page_sample_source_is_capitalized(respx_mock):
    route = respx_mock.post(f"{API}/pages").respond(200, json={"id": "p"})
    make_client().create_page(DB_ID, make_row(source="sample"))
    assert body(route.calls[0])["properties"]["Source"] == {"select": {"name": "Sample"}}


def test_create_page_does_not_retry_read_timeout(respx_mock):
    route = respx_mock.post(f"{API}/pages").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(NotionError):
        make_client().create_page(DB_ID, make_row())
    assert route.call_count == 1


# --- update_status / archive_page ---


def test_update_status(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    make_client().update_status(PAGE_ID, Status.NOT_INTERESTED)
    assert body(route.calls[0]) == {"properties": {"Status": {"select": {"name": "Not interested"}}}}


def test_update_page_sends_score_fields_and_status(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    row = make_row(
        score=91, reason="Strong fit.", flags=["Asks for SQL", "Asks for SQL"], status=Status.INTERESTED,
        start_date="2027-01-04", deadline="2026-12-01",
    )
    assert make_client().update_page(PAGE_ID, row) is None
    assert body(route.calls[0]) == {
        "properties": {
            "Fit Score": {"number": 91},
            "Reason": {"rich_text": [{"text": {"content": "Strong fit."}}]},
            "Flags": {"multi_select": [{"name": "Asks for SQL"}]},
            "Status": {"select": {"name": "Interested"}},
            "Starts": {"date": {"start": "2027-01-04"}},
            "Deadline": {"date": {"start": "2026-12-01"}},
        }
    }


def test_update_page_clears_values_that_are_gone(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    make_client().update_page(PAGE_ID, make_row(score=None, reason="", start_date="soon", deadline=""))
    props = body(route.calls[0])["properties"]
    assert props["Fit Score"] == {"number": None} and props["Reason"] == {"rich_text": []}
    assert props["Starts"] == {"date": None} and props["Deadline"] == {"date": None}


def test_archive_page(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    assert make_client().archive_page(PAGE_ID) is None
    assert body(route.calls[0]) == {"archived": True}


# --- read_statuses ---


def page(page_id, label, edited="2026-11-01T10:15:00.000Z"):
    select = {"name": label} if label else None
    return {"id": page_id, "last_edited_time": edited, "properties": {"Status": {"select": select}}}


def test_read_statuses_paginates(respx_mock):
    route = respx_mock.post(f"{API}/databases/{DB_ID}/query").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [page("p1", "Applied"), page("p2", "Not a label"), page("p3", None)],
                    "has_more": True,
                    "next_cursor": "cur-2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [page("p4", "Offer", "2026-11-02T08:00:00.500Z")],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    result = make_client().read_statuses(DB_ID)
    assert result == {
        "p1": (Status.APPLIED, datetime(2026, 11, 1, 10, 15)),
        "p4": (Status.OFFER, datetime(2026, 11, 2, 8, 0, 0, 500000)),
    }
    assert result["p1"][1].tzinfo is None
    assert route.call_count == 2
    assert "start_cursor" not in body(route.calls[0])
    assert body(route.calls[1])["start_cursor"] == "cur-2"
    assert body(route.calls[0])["page_size"] == 100


def test_read_statuses_converts_offsets_to_naive_utc(respx_mock):
    respx_mock.post(f"{API}/databases/{DB_ID}/query").respond(
        200,
        json={"results": [page("p1", "To review", "2026-11-01T12:00:00+02:00")], "has_more": False},
    )
    assert make_client().read_statuses(DB_ID)["p1"] == (Status.TO_REVIEW, datetime(2026, 11, 1, 10, 0))


# --- headers, pacing, errors ---


def test_notion_version_header(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    make_client().archive_page(PAGE_ID)
    headers = route.calls[0].request.headers
    assert headers["Notion-Version"] == "2022-06-28"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"


def test_pauses_between_calls_but_not_before_the_first(respx_mock):
    respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(200, json={})
    sleeps = []
    client = make_client(sleeps)
    client.archive_page(PAGE_ID)
    assert sleeps == []
    client.archive_page(PAGE_ID)
    client.update_status(PAGE_ID, Status.OFFER)
    assert sleeps == [0.35, 0.35]


def test_retries_rate_limit(respx_mock):
    route = respx_mock.patch(f"{API}/pages/{PAGE_ID}").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={})]
    )
    sleeps = []
    make_client(sleeps).archive_page(PAGE_ID)
    assert route.call_count == 2 and sleeps == [1]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "Notion rejected the token. Check the token and try again."),
        (404, "Notion can't see that page or database. Share it with your integration."),
        (400, "Notion request failed (HTTP 400)."),
        (500, "Notion request failed (HTTP 500)."),
    ],
)
def test_http_errors_become_notion_errors(respx_mock, status, expected):
    respx_mock.patch(f"{API}/pages/{PAGE_ID}").respond(status, text=f"nope {TOKEN}")
    with pytest.raises(NotionError) as err:
        make_client().archive_page(PAGE_ID)
    assert str(err.value) == expected
    assert TOKEN not in str(err.value)


def test_network_errors_become_notion_errors(respx_mock):
    respx_mock.patch(f"{API}/pages/{PAGE_ID}").mock(side_effect=httpx.ConnectError(f"boom {TOKEN}"))
    with pytest.raises(NotionError) as err:
        make_client().archive_page(PAGE_ID)
    assert str(err.value) == "Notion couldn't be reached. Check your internet connection and try again."
    assert "ConnectError" not in str(err.value) and TOKEN not in str(err.value)


def test_notion_timeout_message(respx_mock):
    respx_mock.patch(f"{API}/pages/{PAGE_ID}").mock(side_effect=httpx.ReadTimeout(f"slow {TOKEN}"))
    with pytest.raises(NotionError) as err:
        make_client().archive_page(PAGE_ID)
    assert str(err.value) == "Notion took too long to answer. Try again."


# --- parse_page_id ---

DASHED = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.mark.parametrize(
    "value",
    [
        f"https://www.notion.so/My-Job-Tracker-{DB_ID}",
        f"https://www.notion.so/{DB_ID}",
        f"https://www.notion.so/myteam/My-Job-Tracker-{DB_ID}?pvs=4",
        f"https://www.notion.so/myteam/{DB_ID}?v=fedcba9876543210fedcba9876543210&pvs=4",
        f"https://myteam.notion.site/My-Job-Tracker-{DB_ID}",
        f"  {DB_ID}  ",
        DB_ID.upper(),
        DASHED,
    ],
)
def test_parse_page_id_from_url(value):
    assert parse_page_id(value) == DASHED


@pytest.mark.parametrize(
    "value",
    ["", "hello", "https://www.notion.so/My-Tracker", "https://example.com/abc", DB_ID[:-1], DB_ID + "0"],
)
def test_parse_page_id_rejects_garbage(value):
    with pytest.raises(ConfigError):
        parse_page_id(value)
