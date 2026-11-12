from jobscout.costs import estimate
from jobscout.settings import Settings
from jobscout.startup_places import apply_places, catalog, formats


def test_catalog_starts_with_europe_and_remote_then_countries():
    keys = list(catalog())
    assert keys[:2] == ["europe", "remote"]
    assert catalog()["it"] == "Italy" and catalog()["gb"] == "United Kingdom"


def test_each_source_gets_its_own_format():
    f = formats(["it", "gb", "remote"])
    assert f["builtineurope"] == ["italy", "united kingdom"]
    assert f["jobgether"] == ["italy", "united-kingdom", "anywhere"]
    assert f["vcboards"] == f["careers"] == ["Italy", "United Kingdom", "Remote"]
    assert f["wttj"] == ["IT", "GB", "Remote"]
    assert f["wellfound"] == f["yc"] == ["Italy", "United Kingdom", "Remote"]
    assert f["atsindex"] == ["IT", "GB", "Remote"]


def test_europe_means_everywhere_on_european_boards():
    f = formats(["europe", "it"])
    assert f["builtineurope"] == f["vcboards"] == f["careers"] == f["wttj"] == []
    assert f["jobgether"] == ["italy", "europe"]
    assert f["yc"] == ["Italy", "Europe"] and f["atsindex"] == ["IT", "Europe"]


def test_unknown_and_repeated_keys_are_ignored():
    assert formats(["it", "it", "atlantis"])["wttj"] == ["IT"]


def test_a_source_with_its_own_places_keeps_them():
    s = Settings(startup_places=["it"])
    s.wttj.locations = ["FR"]
    out = apply_places(s)
    assert out.wttj.locations == ["FR"] and out.yc.locations == ["Italy"]
    assert out.builtineurope.countries == ["italy"]
    assert s.yc.locations == []  # the saved settings are not changed


def test_no_shared_places_changes_nothing():
    s = Settings()
    assert apply_places(s) is s


def test_estimate_counts_one_search_per_shared_place():
    s = Settings(startup_places=["it", "de", "remote"])
    s.linkedin.enabled = False
    s.yc.enabled = True
    s.yc.queries = ["engineer"]
    assert estimate(s).board_searches == 3


def test_api_lists_the_places(client):
    places = client.get("/api/startup-places").json()
    assert places[0] == {"key": "europe", "name": "Europe"} and {"key": "it", "name": "Italy"} in places
