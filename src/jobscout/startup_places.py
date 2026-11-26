"""One "What and where to search" for the startup sources, written in each source's own format.

The Startups tab keeps a single list of searches (`Settings.startup_queries`, such as "AI engineer") and a
single list of places (`Settings.startup_places`): country codes such as "it", plus "europe" and "remote".
Each source names places differently (Built in Europe wants "italy", Welcome to the Jungle "IT", Jobgether
"italy" or "anywhere", ...), so `apply_shared` fills each source's own place field from that list, and its
own searches with the shared ones. A source whose own field is already set keeps it. Remotive and Remote OK
have no places, only searches: each is one feed of remote jobs, kept to the ones open to people in Europe.

Career pages are left out of both on purpose. Their place filter matches words in each job's location, and
job boards mostly write a city ("Milan") rather than the country, so a country would drop most of their jobs.
They have no searches either, only a filter on whole words in the job title, so a phrase such as
"AI engineer" would also drop most of their jobs.
"""

import json
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobscout.settings import Settings

EUROPE, REMOTE = "europe", "remote"


@cache
def _places() -> list[dict]:
    data = json.loads(files("jobscout").joinpath("data", "startup_places.json").read_text(encoding="utf-8"))
    return data["places"]


def catalog() -> dict[str, str]:
    """Place key → English name, in display order."""
    return {p["key"]: p["name"] for p in _places()}


def european() -> list[str]:
    """The keys of the European countries in the catalog."""
    return [p["key"] for p in _places() if p.get("europe")]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


# Where Jobgether's slug isn't the English name (checked 2026-11-26).
_JOBGETHER_SLUGS = {"lt": "republic-of-lithuania"}


def jobgether_slug(key: str) -> str:
    """Jobgether's location slug for a country in the catalog, such as "italy" or "united-kingdom"."""
    return _JOBGETHER_SLUGS.get(key) or _slug(catalog()[key])


def jobgether_europe() -> list[str]:
    """Jobgether's slugs for the European countries in the catalog."""
    return [jobgether_slug(k) for k in european()]


def formats(keys: list[str]) -> dict[str, list[str]]:
    """Each source's places for these keys. Unknown keys are ignored; an empty list means anywhere."""
    names, in_europe = catalog(), set(european())
    countries = [k for k in dict.fromkeys(keys) if k in names and k not in (EUROPE, REMOTE)]
    europe, remote = EUROPE in keys, REMOTE in keys
    # With Europe picked, a European country would only repeat part of the same search: leave it out.
    beyond = [k for k in countries if not (europe and k in in_europe)]
    # Sources with no "Europe" of their own get Europe as its countries.
    spelled_out = list(dict.fromkeys((european() if europe else []) + countries))
    remote_word = ["Remote"] if remote else []
    remote_slug = ["anywhere"] if remote else []
    return {
        # Built in Europe only lists jobs in Europe and has no remote filter: no country means all of Europe.
        "builtineurope": [] if europe else [names[k].lower() for k in countries if k in in_europe],
        # Jobgether's own "europe" finds only jobs open across all of Europe: the source sends its countries instead.
        "jobgether": [jobgether_slug(k) for k in beyond] + (["europe"] if europe else []) + remote_slug,
        # Himalayas searches one country at a time, worldwide jobs included, and keeps only jobs open to Europe:
        # Europe (or Remote alone) is one search with no country, and countries outside Europe are left out.
        "himalayas": [] if europe else [k.upper() for k in countries if k in in_europe],
        "vcboards": [names[k] for k in spelled_out] + remote_word,
        # Welcome to the Jungle is a European board: Europe is one search with no place, remote jobs included.
        "wttj": [] if europe else [k.upper() for k in countries] + remote_word,
        "wellfound": [names[k] for k in beyond] + (["Europe"] if europe else []) + remote_word,
        "yc": [names[k] for k in beyond] + (["Europe"] if europe else []) + remote_word,
        "atsindex": [k.upper() for k in beyond] + (["Europe"] if europe else []) + remote_word,
    }


_FIELDS = {"builtineurope": "countries", "himalayas": "countries"}  # every other source calls it `locations`
# Sources that take the shared searches but have no places: one feed each, kept to jobs open to Europe.
_SEARCH_ONLY = ("remotive", "remoteok")


def apply_shared(s: "Settings") -> "Settings":
    """A copy of the settings where each startup source without its own searches or places uses
    `startup_queries` and `startup_places`. The same settings when neither is set."""
    if not s.startup_places and not s.startup_queries:
        return s
    out = s.model_copy(deep=True)
    for source, places in formats(s.startup_places).items():
        section = getattr(out, source)
        field = _FIELDS.get(source, "locations")
        if s.startup_places and not getattr(section, field):
            setattr(section, field, places)
        if not section.queries:
            section.queries = list(s.startup_queries)
    for source in _SEARCH_ONLY:
        section = getattr(out, source)
        if not section.queries:
            section.queries = list(s.startup_queries)
    return out
