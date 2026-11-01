"""Job sources: what they search and what they return."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from jobscout.errors import ApifyError
from jobscout.models import Job


@dataclass
class SearchArea:
    """One unit of searching with its own time window, e.g. a LinkedIn region."""

    key: str
    label: str
    source: str
    searches: int
    params: dict = field(default_factory=dict)


@dataclass
class AreaResult:
    jobs: list[Job]
    billed: int
    complete: bool
    error: ApifyError | None = None


def as_str(value: object) -> str:
    """Text from an actor field: strings as they are, numbers as text, anything else (None, lists, dicts) empty."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def web_url(value: object) -> str:
    """A link from an actor field, kept only if it is an http or https address; anything else is empty."""
    text = as_str(value).strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return ""
    return text if scheme in ("http", "https") else ""


class Source(Protocol):
    name: str

    def areas(self) -> list[SearchArea]: ...

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """Search one area. `progress(done_searches, total_searches, postings_so_far)`."""
        ...
