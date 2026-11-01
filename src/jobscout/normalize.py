"""Text normalisation, dedup keys and excluded-place matching."""

import re
import unicodedata


def fold(text: str) -> str:
    """Strip accents, casefold, collapse whitespace and trim."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def city_of(location: str) -> str:
    """The folded first comma-separated part of a location ("" when empty)."""
    return fold(location.split(",", 1)[0])


def dedup_key(title: str, company: str, location: str) -> str:
    return f"{fold(title)}|{fold(company)}|{city_of(location)}"


class PlaceMatcher:
    """Finds which configured place name (country or city) appears in a location."""

    def __init__(self, countries: list[str], cities: list[str]) -> None:
        self._originals: dict[str, str] = {}
        for name in [*countries, *cities]:
            folded = fold(name)
            if folded:
                self._originals.setdefault(folded, name)
        self._pattern: re.Pattern[str] | None = None
        if self._originals:
            # Longest first so "republic of austria" wins over a shorter overlapping name.
            names = sorted(self._originals, key=len, reverse=True)
            alternatives = "|".join(re.escape(n) for n in names)
            self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")

    def match(self, location: str) -> str | None:
        if self._pattern is None or not location:
            return None
        found = self._pattern.search(fold(location))
        return self._originals[found.group(0)] if found else None
