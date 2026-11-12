"""One "Where to search" for every startup source, written in each source's own format.

The Startups tab keeps a single list of places (`Settings.startup_places`): country codes such as "it",
plus "europe" and "remote". Each source names places differently (Built in Europe wants "italy",
Welcome to the Jungle "IT", Jobgether "italy" or "anywhere", ...), so `apply_places` fills each
source's own place field from that list. A source whose own field is already set keeps it.
"""

import json
from functools import cache
from importlib.resources import files

from jobscout.settings import Settings

EUROPE, REMOTE = "europe", "remote"


@cache
def catalog() -> dict[str, str]:
    """Place key → English name, in display order."""
    data = json.loads(files("jobscout").joinpath("data", "startup_places.json").read_text(encoding="utf-8"))
    return {p["key"]: p["name"] for p in data["places"]}


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _iso(key: str) -> str:
    return key.upper()


def formats(keys: list[str]) -> dict[str, list[str]]:
    """Each source's places for these keys. Unknown keys are ignored; an empty list means anywhere."""
    names = catalog()
    countries = [k for k in dict.fromkeys(keys) if k in names and k not in (EUROPE, REMOTE)]
    europe, remote = EUROPE in keys, REMOTE in keys
    country_names = [names[k] for k in countries]
    remote_word = ["Remote"] if remote else []
    return {
        # Every Built in Europe job is in Europe: Europe, or no country, means all of them.
        "builtineurope": [] if europe else [n.lower() for n in country_names],
        "jobgether": [_slug(n) for n in country_names] + (["europe"] if europe else []) + (["anywhere"] if remote else []),
        # The VC boards and career pages have no "Europe" filter: Europe keeps every place.
        "vcboards": [] if europe else country_names + remote_word,
        "careers": [] if europe else country_names + remote_word,
        # Welcome to the Jungle is a European board: Europe is one search with no place.
        "wttj": [] if europe else [_iso(k) for k in countries] + remote_word,
        "wellfound": country_names + (["Europe"] if europe else []) + remote_word,
        "yc": country_names + (["Europe"] if europe else []) + remote_word,
        "atsindex": [_iso(k) for k in countries] + (["Europe"] if europe else []) + remote_word,
    }


_FIELDS = {"builtineurope": "countries"}  # every other source calls it `locations`


def apply_places(s: Settings) -> Settings:
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
