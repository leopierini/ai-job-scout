"""Pipeline helpers for the search step: each area's window and a search that never raises.

Private to jobscout.pipeline.
"""

from collections.abc import Callable
from datetime import datetime

from jobscout.db import Database
from jobscout.errors import ApifyError
from jobscout.settings import WindowSettings
from jobscout.sources import AreaResult, SearchArea, Source
from jobscout.windows import window_seconds

SEARCH_FAILED = "The search failed unexpectedly."
STOP_ALL = ("limit", "auth")  # Apify errors that stop every remaining area, on every source


def area_window(
    db: Database, area: SearchArea, now: datetime, w: WindowSettings, override_days: int | None
) -> tuple[int, bool]:
    """Seconds to search back, and whether a complete search may commit the area's window.

    A manual window shorter than the automatic one would leave a gap if committed: it is not.
    """
    last = db.window_start(area.key)
    automatic = window_seconds(last, now, w.default_days, w.overlap_minutes)
    if override_days is None:
        return automatic, True
    seconds = window_seconds(last, now, w.default_days, w.overlap_minutes, override_days)
    return seconds, seconds >= automatic


def search(
    source: Source, area: SearchArea, seconds: int, progress: Callable[[int, int, int], None]
) -> AreaResult:
    """Search one area. Any failure becomes an incomplete result, so the other areas still run."""
    try:
        return source.search_area(area, seconds, progress)
    except ApifyError as exc:
        return AreaResult(jobs=[], billed=0, complete=False, error=exc)
    except Exception:  # noqa: BLE001 - one broken area must not stop the others; it is retried next run
        return AreaResult(jobs=[], billed=0, complete=False, error=ApifyError(SEARCH_FAILED))
