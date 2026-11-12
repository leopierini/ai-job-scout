"""What the startup job boards searched through an Apify actor share (Welcome to the Jungle, Wellfound, ...).

Each board runs every query once in each location, or once with no location when none is set; this is what
`costs.board_searches` counts. A board module supplies the actor input for one search and the parsing of one item.
"""

import math
import re
import threading
from collections.abc import Callable
from datetime import date

from jobscout.errors import ApifyError
from jobscout.models import Job
from jobscout.settings import ApifyBoardSettings
from jobscout.sources import AreaResult, SearchArea, as_str, within_window
from jobscout.sources.apify import ApifyClient

ALL_LOCATIONS = "All locations"
_REMOTE_WORDS = frozenset({"remote", "anywhere", "worldwide remote"})
# Two-letter words people type that are not the ISO code of a country.
_NOT_COUNTRY_CODES = {"UK": "GB"}
_NOT_COUNTRIES = frozenset({"EU"})


def window_days(window_seconds: int) -> int:
    """Whole days that cover the window, at least one."""
    return max(1, math.ceil(window_seconds / 86400))


def country_code(location: str) -> str:
    """The ISO 3166-1 alpha-2 code when the location is one (e.g. "it" or "IT"; "UK" means GB), otherwise ""."""
    text = location.strip().upper()
    if len(text) != 2 or not text.isalpha() or text in _NOT_COUNTRIES:
        return ""
    return _NOT_COUNTRY_CODES.get(text, text)


def is_remote(location: str) -> bool:
    """True when the location asks for remote jobs rather than a place."""
    return location.strip().casefold() in _REMOTE_WORDS


def first_text(item: dict, *keys: str) -> str:
    """The first of these fields that holds usable text, trimmed."""
    return next((text for key in keys if (text := as_str(item.get(key)).strip())), "")


def text_list(value: object, limit: int = 3) -> list[str]:
    """Up to `limit` distinct non-empty strings from a list field; a plain string becomes a one-item list."""
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for v in values:
        text = as_str(v).strip()
        if text and text not in out:
            out.append(text)
    return out[:limit]


def with_work_policy(place: str, policy: str) -> str:
    """A location with its work policy, e.g. "Milan, IT (Remote)", or just the policy when there is no place."""
    if not policy or policy.casefold() in place.casefold():  # never "Remote (Remote)"
        return place
    return f"{place} ({policy})" if place else policy


def humanize(value: object) -> str:
    """A machine label made readable: "full_time" or "FULL-TIME" becomes "Full time"."""
    text = re.sub(r"[_\s]+", " ", as_str(value)).strip()
    if not text:
        return ""
    if text.isupper() or text.islower():
        text = text.replace("-", " ").lower()
        return text[0].upper() + text[1:]
    return text


class ApifyBoardSource:
    """A board searched through an Apify actor. Subclasses set `name` and implement `payload` and `parse`."""

    name = ""

    def __init__(
        self,
        settings: ApifyBoardSettings,
        apify: ApifyClient,
        cancel: threading.Event,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._settings = settings
        self._apify = apify
        self._cancel = cancel
        self._today = today  # injectable so tests can pin the date the window is counted from

    # --- what each board supplies ---

    def payload(self, query: str, location: str, window_seconds: int) -> dict:
        """The actor input for one query in one location ("" for no location)."""
        raise NotImplementedError

    def parse(self, item: dict, query: str, area: str) -> Job | None:
        """One actor item as a Job, or None if it has no usable id."""
        raise NotImplementedError

    def window_filtered(self, window_seconds: int) -> bool:
        """True when the actor itself keeps only jobs posted in the window; otherwise results are filtered here."""
        return True

    def keep(self, job: Job, query: str) -> bool:
        """A last check on a parsed job, e.g. that its title matches the query."""
        return True

    # --- shared ---

    def areas(self) -> list[SearchArea]:
        s = self._settings
        searches = len(s.queries)
        if not s.locations:
            return [SearchArea(f"{self.name}:all", ALL_LOCATIONS, self.name, searches, {"location": ""})]
        return [
            SearchArea(f"{self.name}:{location}", location, self.name, searches, {"location": location})
            for location in s.locations
        ]

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """Run every query in this location. Stops at the first Apify error or when cancelled."""
        s = self._settings
        location = area.params.get("location", "")
        filter_here = not self.window_filtered(window_seconds)
        today = self._today()
        total = len(s.queries)
        jobs: dict[str, Job] = {}
        billed = 0
        error: ApifyError | None = None
        complete = True
        progress(0, total, 0)
        for done, query in enumerate(s.queries):
            if self._cancel.is_set():
                complete = False
                break
            try:
                items = self._apify.run_actor(s.actor, self.payload(query, location, window_seconds))
            except ApifyError as exc:
                error, complete = exc, False
                break
            billed += len(items)  # Apify bills every returned item, duplicates included
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = self.parse(item, query, area.label)
                except Exception:  # noqa: BLE001 - one malformed item must not lose the area's paid results
                    continue
                if job is None or job.job_id in jobs or not self.keep(job, query):
                    continue
                if filter_here and not within_window(job.posted, window_seconds, today):
                    continue
                jobs[job.job_id] = job
            progress(done + 1, total, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=billed, complete=complete, error=error)
