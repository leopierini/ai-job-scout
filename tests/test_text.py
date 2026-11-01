from jobscout._text import plural


def test_plural():
    assert plural(1, "duplicate") == "1 duplicate" and plural(2, "search", "searches") == "2 searches"
    assert plural(0, "posting") == "0 postings" and plural(1, "search", "searches") == "1 search"
