"""One "Where to search" for the startup sources, written in each source's own format.

The Startups tab keeps a single list of places (`Settings.startup_places`): country codes such as "it",
plus "europe" and "remote". Each source names places differently (Built in Europe wants "italy",
Welcome to the Jungle "IT", Jobgether "italy" or "anywhere", ...), so `apply_places` fills each
source's own place field from that list. A source whose own field is already set keeps it.

Career pages are left out on purpose: their filter matches words in each job's location, and job boards
mostly write a city ("Milan") rather than the country, so a country would drop most of their jobs.
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
    return {
        # Built in Europe only lists jobs in Europe and has no remote filter: no country means all of Europe.
        "builtineurope": [] if europe else [names[k].lower() for k in countries if k in in_europe],
        "jobgether": [_slug(names[k]) for k in beyond] + (["europe"] if europe else []) + (["anywhere"] if remote else []),
        "vcboards": [names[k] for k in spelled_out] + remote_word,
        # Welcome to the Jungle is a European board: Europe is one search with no place, remote jobs included.
        "wttj": [] if europe else [k.upper() for k in countries] + remote_word,
        "wellfound": [names[k] for k in beyond] + (["Europe"] if europe else []) + remote_word,
        "yc": [names[k] for k in beyond] + (["Europe"] if europe else []) + remote_word,
        "atsindex": [k.upper() for k in beyond] + (["Europe"] if europe else []) + remote_word,
    }


_FIELDS = {"builtineurope": "countries"}  # every other source calls it `locations`


def apply_places(s: "Settings") -> "Settings":
    """A copy of the settings where each startup source without its own places uses `startup_places`."""
    if not s.startup_places:
        return s
    out = s.model_copy(deep=True)
    for source, places in formats(s.startup_places).items():
        section = getattr(out, source)
        field = _FIELDS.get(source, "locations")
        if not getattr(section, field):
            setattr(section, field, places)
    return out
