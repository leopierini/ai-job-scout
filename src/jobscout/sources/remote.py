"""Helpers shared by the remote job boards (Remotive, Himalayas, Remote OK).

Most "remote" jobs on these boards only take people who live in one country or region, usually the US. The
app keeps the ones a person in Europe can apply for: `open_to_europe` reads a board's location restriction
("USA only", "Europe, USA", "Worldwide", a list of countries) and says whether Europe is in it.

Remotive and Remote OK have no keyword search worth using (one feed of recent jobs each), so their jobs are
matched to the run's searches here, by the words in the title (`matching_query`), and both are read
through `FeedSource`.
"""

import re
import threading
import time
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

import httpx

from jobscout.errors import SearchError
from jobscout.models import Job
from jobscout.normalize import fold
from jobscout.sources import USER_AGENT, AreaResult, SearchArea, fetch_json, within_window

if TYPE_CHECKING:
    from jobscout.settings import RemoteOkSettings, RemotiveSettings

# Whole words or phrases, folded (lower case, no accents). A place that is also a US state or city part
# ("Georgia", "Jersey" as in New Jersey) is left out on purpose.
EUROPE_WORDS = frozenset({
    "europe", "european", "eu", "eea", "emea", "cet", "cest", "dach", "benelux", "nordics", "nordic",
    "scandinavia", "baltics", "balkans",
    "albania", "andorra", "austria", "belarus", "belgium", "bosnia", "bosnia and herzegovina", "bulgaria",
    "croatia", "cyprus", "czechia", "czech republic", "denmark", "estonia", "faroe islands", "finland",
    "france", "germany", "deutschland", "gibraltar", "greece", "hungary", "iceland", "ireland",
    "isle of man", "italy", "italia", "kosovo", "latvia", "liechtenstein", "lithuania", "luxembourg",
    "malta", "moldova", "monaco", "montenegro", "netherlands", "the netherlands", "holland",
    "north macedonia", "macedonia", "norway", "poland", "portugal", "romania", "san marino", "serbia",
    "slovakia", "slovenia", "spain", "espana", "sweden", "switzerland", "ukraine", "united kingdom", "uk",
    "great britain", "britain", "england", "scotland", "wales", "northern ireland", "vatican",
    "holy see", "aland islands",
})  # fmt: skip

# Places outside Europe. Only used to tell "Worldwide" or "Remote" apart from "US Remote" or "Anywhere in
# Canada": a location that names neither Europe nor anywhere is dropped whether it is listed here or not.
ELSEWHERE_WORDS = frozenset({
    "us", "u.s", "u.s.", "usa", "united states", "america", "americas", "north america", "south america",
    "latin america", "central america", "latam", "canada", "mexico", "brazil", "argentina", "colombia",
    "chile", "peru", "apac", "asia", "asia pacific", "africa", "middle east", "mena", "australia",
    "new zealand", "oceania", "anz", "india", "pakistan", "philippines", "singapore", "japan", "china",
    "hong kong", "korea", "south korea", "taiwan", "indonesia", "vietnam", "malaysia", "thailand",
    "israel", "uae", "united arab emirates", "saudi arabia", "egypt", "nigeria", "kenya", "south africa",
    "turkey", "russia", "est", "edt", "pst", "pdt", "mst",
    "new south wales", "new england",
})  # fmt: skip

# Places outside Europe whose names hold a European one ("Wales" in New South Wales): hidden before Europe is
# looked for, so they don't read as Europe.
NOT_EUROPE_PHRASES = frozenset({"new south wales", "new england"})

# Words that say a job takes people anywhere. "Remote" only counts when nothing else names a place: "Remote -
# California" or "Toronto (Remote)" is not open to Europe.
ANYWHERE_WORDS = frozenset({
    "worldwide", "anywhere", "global", "globally", "international", "everywhere", "remote", "any location",
    "fully remote", "100% remote", "no restriction", "no restrictions", "work from anywhere",
})  # fmt: skip

# Words that may stand next to the words above without naming a place ("Remote only", "Worldwide, async").
FILLER_WORDS = frozenset({
    "only", "in", "from", "the", "and", "or", "work", "based", "friendly", "first", "async", "ok", "position", "world",
})  # fmt: skip


def _pattern(words: frozenset[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))  # longest first
    return re.compile(rf"(?<![\w.])(?:{alternatives})(?![\w])")


_EUROPE, _ELSEWHERE, _ANYWHERE = _pattern(EUROPE_WORDS), _pattern(ELSEWHERE_WORDS), _pattern(ANYWHERE_WORDS)
_NOT_EUROPE, _FILLER = _pattern(NOT_EUROPE_PHRASES), _pattern(FILLER_WORDS)


def _text(restriction: str | list[str] | None) -> str:
    if isinstance(restriction, list):
        return fold(", ".join(x for x in restriction if isinstance(x, str)))
    return fold(restriction or "")


def _europe_text(text: str) -> str:
    """`text` with the places outside Europe that hold a European name blanked out."""
    return _NOT_EUROPE.sub(" ", text)


def open_to_europe(restriction: str | list[str] | None) -> bool:
    """True when a person living in Europe may apply: no restriction, "Worldwide", or a place in Europe.

    `restriction` is the board's own text ("USA only", "LATAM, Europe, USA") or list of countries. A
    restriction that names Europe or a European country is kept even when it names other places too; one
    that only names other places ("US Only", "Americas", "APAC", "Anywhere in Canada") is not, nor is a place
    this list doesn't know ("Newark", "Remote - Newark"): it can't be told apart from a city in the US.
    """
    text = _text(restriction)
    if not text.strip(" ,;/-()"):
        return True
    if _EUROPE.search(_europe_text(text)):
        return True
    if not _ANYWHERE.search(text) or _ELSEWHERE.search(text):
        return False
    rest = _FILLER.sub(" ", _ANYWHERE.sub(" ", text))
    return not re.search(r"[^\W\d_]", rest)  # nothing but "anywhere" words left: no other place named


_UPPER = frozenset({"eu", "eea", "emea", "cet", "cest", "dach", "uk"})
_SMALL = frozenset({"and", "of", "the"})
_ALIASES = {
    "european": "europe", "nordic": "nordics", "italia": "italy", "deutschland": "germany", "espana": "spain",
    "holland": "netherlands", "the netherlands": "netherlands", "czech republic": "czechia",
    "macedonia": "north macedonia", "bosnia": "bosnia and herzegovina", "holy see": "vatican",
}  # fmt: skip


def _display(word: str) -> str:
    word = _ALIASES.get(word, word)
    if word in _UPPER:
        return word.upper()
    parts = word.split(" ")
    return " ".join(p if i and p in _SMALL else p.capitalize() for i, p in enumerate(parts))


def remote_location(restriction: str | list[str] | None) -> str:
    """How a remote job's place is shown: "Remote (Italy, Spain)", "Remote (Europe)" or "Remote (Worldwide)".

    Only the European places are named, so a job open to Europe and the US isn't dropped by an excluded
    country such as the United States. More than three of them read as Europe.
    """
    named = list(dict.fromkeys(_display(m.group(0)) for m in _EUROPE.finditer(_europe_text(_text(restriction)))))
    if not named:
        return "Remote (Worldwide)"
    return f"Remote ({', '.join(named)})" if len(named) <= 3 else "Remote (Europe)"


# Words a search may hold that a title needn't ("Head of Product" matches "Product Head").
_QUERY_FILLER = frozenset({"of", "and", "the", "for", "a", "an", "in", "at", "to"})


def _words(text: str) -> set[str]:
    """Folded words, with apostrophes dropped and a plural or possessive "s" taken off ("founder's" → founder).

    A hyphenated word counts both split and joined: "Full-Stack" gives full, stack and fullstack.
    """
    folded = fold(text).replace("'", "").replace("’", "")
    joined = re.findall(r"\w+(?:-\w+)+", folded)
    out = set()
    for word in re.findall(r"\w+", folded) + [w.replace("-", "") for w in joined]:
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return out


def matching_query(title: str, queries: list[str]) -> str | None:
    """The first search whose every word is in the job's title, in any order; None when none is."""
    have = _words(title)
    for query in queries:
        want = _words(query) - _QUERY_FILLER
        if want and want <= have:
            return query
    return None


class FeedSource:
    """A board read as one feed of recent remote jobs, once a run, and filtered here.

    Each job is kept when its title matches one of the searches, it is open to people in Europe and it was
    posted within the run's window. Subclasses say where the feed is and how to read one of its jobs.
    """

    name = ""
    label = ""
    url = ""
    retries = 3  # attempts for the one request
    publish_delay_days = 0  # how long after its posted date a job shows up in the feed
    min_hours_between_reads = 0  # a board that asks to be read only a few times a day

    def __init__(
        self,
        settings: "RemotiveSettings | RemoteOkSettings",
        client: httpx.Client,
        cancel: threading.Event,
        *,
        today: Callable[[], date] = date.today,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = client
        self._cancel = cancel
        self._today = today
        self._sleep = sleep

    def areas(self) -> list[SearchArea]:
        return [SearchArea(f"{self.name}:all", self.label, self.name, 1, {})]

    def items(self, data: object) -> list | None:
        """The jobs in the feed's answer, or None if it isn't one."""
        raise NotImplementedError

    def title(self, item: dict) -> str:
        raise NotImplementedError

    def restriction(self, item: dict) -> str | list[str] | None:
        raise NotImplementedError

    def parse_item(self, item: dict, query: str, area: str) -> Job | None:
        raise NotImplementedError

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        """Read the feed once and keep the jobs that match. Stops at an error or when cancelled."""
        progress(0, 1, 0)
        if self._cancel.is_set():
            return AreaResult(jobs=[], billed=0, complete=False)
        # The window runs from the last read (plus an overlap): a short one means the feed was read recently.
        # Not reading leaves the area incomplete, so the next read's window still covers this gap.
        if self.min_hours_between_reads and window_seconds < self.min_hours_between_reads * 3600:
            wait = SearchError(
                f"{self.label} asks to be read only a few times a day, and was read under "
                f"{self.min_hours_between_reads} hours ago. It is read again on a later run."
            )
            return AreaResult(jobs=[], billed=0, complete=False, error=wait)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            data = fetch_json(
                self._client, "GET", self.url, self.label, headers=headers, sleep=self._sleep, retries=self.retries
            )
        except SearchError as exc:
            return AreaResult(jobs=[], billed=0, complete=False, error=exc)
        items = self.items(data)
        if items is None:
            unreadable = SearchError(f"{self.label} sent an answer we couldn't read.")
            return AreaResult(jobs=[], billed=0, complete=False, error=unreadable)
        today = self._today()
        window = window_seconds + self.publish_delay_days * 86400
        jobs: dict[str, Job] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                query = matching_query(self.title(item), self._settings.queries)
                if query is None or not open_to_europe(self.restriction(item)):
                    continue
                job = self.parse_item(item, query, area.label)
            except Exception:  # noqa: BLE001 - one malformed item must not lose the others
                continue
            if job is not None and within_window(job.posted, window, today):
                jobs.setdefault(job.job_id, job)
        progress(1, 1, len(jobs))
        return AreaResult(jobs=list(jobs.values()), billed=0, complete=True)
