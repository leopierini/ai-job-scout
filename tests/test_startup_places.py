import pytest
from pydantic import ValidationError

from jobscout.costs import estimate
from jobscout.settings import Settings
from jobscout.startup_places import apply_shared, catalog, european, formats


def test_catalog_starts_with_europe_and_remote_then_countries():
    keys = list(catalog())
    assert keys[:2] == ["europe", "remote"]
    assert catalog()["it"] == "Italy" and catalog()["gb"] == "United Kingdom"
    assert "it" in european() and "us" not in european() and "europe" not in european()


def test_each_source_gets_its_own_format():
    f = formats(["it", "gb", "remote"])
    assert f["builtineurope"] == ["italy", "united kingdom"]
    assert f["jobgether"] == ["italy", "united-kingdom", "anywhere"]
    assert f["vcboards"] == ["Italy", "United Kingdom", "Remote"]
    assert f["wttj"] == ["IT", "GB", "Remote"]
    assert f["wellfound"] == f["yc"] == ["Italy", "United Kingdom", "Remote"]
    assert f["atsindex"] == ["IT", "GB", "Remote"]


def test_career_pages_are_not_given_places():
    assert "careers" not in formats(["it"])
    s = apply_shared(Settings(startup_places=["it"]))
    assert s.careers.locations == []


def test_built_in_europe_only_gets_european_countries():
    assert formats(["us", "it", "remote"])["builtineurope"] == ["italy"]
    assert formats(["us"])["builtineurope"] == []  # all of Europe


def test_europe_is_spelled_out_where_a_source_has_no_europe():
    f = formats(["europe", "us", "remote"])
    assert f["vcboards"][:2] == ["Austria", "Belgium"] and f["vcboards"][-2:] == ["United States", "Remote"]
    assert "Italy" in f["vcboards"] and len(f["vcboards"]) == len(european()) + 2
    assert f["builtineurope"] == f["wttj"] == []


def test_europe_and_a_european_country_search_once():
    f = formats(["europe", "it", "us"])
    assert f["yc"] == ["United States", "Europe"] and f["wellfound"] == ["United States", "Europe"]
    assert f["atsindex"] == ["US", "Europe"] and f["jobgether"] == ["united-states", "europe"]


def test_unknown_and_repeated_keys_are_ignored_by_formats():
    assert formats(["it", "it", "atlantis"])["wttj"] == ["IT"]


def test_saved_places_are_normalised_and_checked():
    assert Settings(startup_places=[" IT", "it", "Remote"]).startup_places == ["it", "remote"]
    with pytest.raises(ValidationError, match="Unknown place: atlantis"):
        Settings(startup_places=["atlantis"])


def test_a_source_with_its_own_places_keeps_them():
    s = Settings(startup_places=["it"])
    s.wttj.locations = ["FR"]
    out = apply_shared(s)
    assert out.wttj.locations == ["FR"] and out.yc.locations == ["Italy"]
    assert out.builtineurope.countries == ["italy"]
    assert s.yc.locations == []  # the saved settings are not changed


def test_no_shared_places_changes_nothing():
    s = Settings()
    assert apply_shared(s) is s


def test_estimate_counts_one_search_per_shared_place():
    s = Settings(startup_places=["it", "de", "remote"])
    s.linkedin.enabled = False
    s.yc.enabled = True
    s.yc.queries = ["engineer"]
    assert estimate(s).board_searches == 3


def test_api_lists_the_places(client):
    places = client.get("/api/startup-places").json()
    assert places[0] == {"key": "europe", "name": "Europe"} and {"key": "it", "name": "Italy"} in places


def test_shared_searches_fill_each_search_based_source():
    s = apply_shared(Settings(startup_queries=["AI engineer", "ML engineer"]))
    for name in ("builtineurope", "jobgether", "vcboards", "wttj", "wellfound", "yc", "atsindex"):
        assert getattr(s, name).queries == ["AI engineer", "ML engineer"], name
    assert s.yc.locations == [] and s.builtineurope.countries == []  # no shared places: places untouched


def test_a_source_with_its_own_searches_keeps_them():
    s = Settings(startup_queries=["AI engineer"])
    s.wellfound.queries = ["founding engineer"]
    out = apply_shared(s)
    assert out.wellfound.queries == ["founding engineer"] and out.jobgether.queries == ["AI engineer"]
    assert s.jobgether.queries == []  # the saved settings are not changed


def test_career_pages_are_not_given_searches():
    s = apply_shared(Settings(startup_queries=["AI engineer"], startup_places=["it"]))
    assert s.careers.title_words == [] and s.careers.locations == []


def test_saved_searches_are_trimmed_and_deduplicated():
    s = Settings(startup_queries=[" AI engineer ", "AI engineer", "", "  ", "ML engineer"])
    assert s.startup_queries == ["AI engineer", "ML engineer"]


def test_estimate_counts_the_shared_searches():
    s = Settings(startup_queries=["AI engineer", "ML engineer"], startup_places=["it", "remote"])
    s.linkedin.enabled = False
    s.yc.enabled = True
    s.wttj.enabled = True
    s.wttj.queries = ["data engineer"]  # its own: one query
    assert estimate(s).board_searches == 2 * 2 + 1 * 2


def test_shared_searches_round_trip_through_the_api(client):
    headers = {"X-Requested-By": "ai-job-scout"}
    body = client.get("/api/settings").json()
    body["startup_queries"] = [" AI engineer", "AI engineer", "ML engineer"]
    assert client.put("/api/settings", json=body, headers=headers).status_code == 200
    assert client.get("/api/settings").json()["startup_queries"] == ["AI engineer", "ML engineer"]
