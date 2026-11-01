"""Small helpers for user-facing wording."""


def plural(n: int, one: str, many: str | None = None) -> str:
    """"1 search", "3 searches": the count followed by the noun in the right number."""
    return f"{n} {one if n == 1 else (many if many is not None else one + 's')}"
