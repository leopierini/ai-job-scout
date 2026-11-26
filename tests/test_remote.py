import pytest

from jobscout.sources.remote import matching_query, open_to_europe, remote_location


@pytest.mark.parametrize(
    "restriction",
    [
        "", "  ", None, [], "Worldwide", "Anywhere in the World", "Remote", "Global", "Europe", "EMEA", "EU",
        "European Union", "Italy", "italia", "Germany", "UK Only", "United Kingdom", "Czech Republic", "Nordics",
        "LATAM, Europe, USA, Canada, APAC", "Europe, USA, Canada, Australia, Singapore", "CET +/- 3 hours",
        ["Italy", "Spain"], ["United States", "Italy"], ["Åland Islands"],
    ],
)  # fmt: skip
def test_open_to_europe(restriction):
    assert open_to_europe(restriction)


@pytest.mark.parametrize(
    "restriction",
    [
        "USA", "US Only", "U.S. only", "USA only", "United States", "Americas", "LATAM", "Latin America",
        "APAC", "Asia", "Canada Only", "Americas, LATAM, Canada, USA timezones", "USA, Canada, USA timezones",
        "US Remote", "Anywhere in the US", "Remote, Canada", "Worldwide (PST hours)", "Philippines",
        "Newark", "New Jersey", "Atlanta, Georgia", ["United States"], ["Brazil", "Mexico"],
    ],
)  # fmt: skip
def test_not_open_to_europe(restriction):
    assert not open_to_europe(restriction)


@pytest.mark.parametrize(
    ("restriction", "shown"),
    [
        ("", "Remote (Worldwide)"),
        ("Worldwide", "Remote (Worldwide)"),
        ("Europe", "Remote (Europe)"),
        ("UK Only", "Remote (UK)"),
        ("European Union", "Remote (Europe)"),
        ("LATAM, Europe, USA, Canada, APAC", "Remote (Europe)"),  # the US is left out of the place shown
        (["Italy", "Netherlands"], "Remote (Italy, Netherlands)"),
        (["Bosnia and Herzegovina"], "Remote (Bosnia and Herzegovina)"),
        (["France", "Italy", "Netherlands", "Spain"], "Remote (Europe)"),
        (["Italy", "italia", "Italy"], "Remote (Italy)"),
    ],
)
def test_remote_location(restriction, shown):
    assert remote_location(restriction) == shown


@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Associate Product Manager", "Associate Product Manager"),
        ("Product Manager, Associate", "Associate Product Manager"),
        ("Senior Product Manager", "Product Manager"),
        ("Founders Associate", "Founder's Associate"),
        ("Founder’s Associate (Europe)", "Founder's Associate"),
        ("Product Manager, AI Platform", "AI Product"),
        ("Product Management Intern", "Product Intern"),
        ("APM – Growth", "APM"),
        ("Gestión de Producto", "gestion de producto"),
    ],
)
def test_matching_query(title, query):
    assert matching_query(title, [query]) == query


@pytest.mark.parametrize(
    ("title", "queries"),
    [
        ("Product Marketing Manager", ["Associate Product Manager"]),
        ("Production Manager", ["Product Manager"]),
        ("Sales Manager", ["Product Manager"]),
        ("Anything", []),
        ("Anything", ["  "]),
    ],
)
def test_matching_query_misses(title, queries):
    assert matching_query(title, queries) is None


def test_matching_query_gives_the_first_search_that_matches():
    assert matching_query("Associate Product Manager", ["Product Owner", "Product Manager", "APM"]) == "Product Manager"
