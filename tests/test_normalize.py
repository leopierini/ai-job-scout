from jobscout.normalize import PlaceMatcher, TermMatcher, city_of, dedup_key, fold


def test_fold_strips_accents_case_and_whitespace():
    assert fold("  Zürich \t  AG\n") == "zurich ag"
    assert fold("Straße") == "strasse"
    assert fold("") == ""


def test_dedup_key_cross_source_same_city():
    a = dedup_key("Product Manager", "Fjord Labs", "Amsterdam, North Holland, Netherlands")
    b = dedup_key("product manager ", "FJORD LABS", "Amsterdam")
    assert a == b


def test_dedup_key_format():
    assert dedup_key("Product Manager", "Fjord Labs", "Amsterdam, NL") == "product manager|fjord labs|amsterdam"


def test_dedup_key_different_city_differs():
    assert dedup_key("Sales Associate", "Kestrel", "Berlin") != dedup_key("Sales Associate", "Kestrel", "Munich")


def test_city_and_exclusion_edge_cases():
    m = PlaceMatcher(["Austria", "Republic of Austria"], ["Zürich"])
    assert m.match("") is None
    assert m.match("Remote") is None
    assert m.match("Vienna, Austria") == "Austria"
    assert m.match("Vienna, Republic of Austria") == "Republic of Austria"  # the longest name wins
    assert m.match("Zurich, Switzerland") == "Zürich"      # accent-insensitive
    assert m.match("Austrian Alps Lodge, Italy") is None    # word boundary
    assert city_of("Zürich, Switzerland") == "zurich"
    assert PlaceMatcher([], []).match("Anywhere") is None


def test_city_of_empty():
    assert city_of("") == ""


def test_place_matcher_is_case_insensitive_and_escapes_names():
    m = PlaceMatcher(["Republic of Austria"], ["St. Gallen"])
    assert m.match("VIENNA, REPUBLIC OF AUSTRIA") == "Republic of Austria"
    assert m.match("St. Gallen") == "St. Gallen"
    assert m.match("Stx Gallen") is None


def test_term_matcher_matches_whole_words_ignoring_case_and_accents():
    m = TermMatcher(["Senior", "Head", "Ingénieur", "  "])
    assert m.match("SENIOR Product Manager") == "Senior"
    assert m.match("Head of Product") == "Head"
    assert m.match("Ingenieur Produit") == "Ingénieur"
    assert m.match("Seniority-free Product Manager") is None
    assert m.match("Headquarters Product Manager") is None
    assert m.match("") is None
    assert TermMatcher([]).match("Senior Product Manager") is None
