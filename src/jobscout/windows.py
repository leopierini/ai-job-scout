"""How far back to scrape for each area."""

import math
from datetime import datetime

_INDEED_FROM_DAYS = (1, 3, 7, 14)


def window_seconds(
    last_start: datetime | None,
    now: datetime,
    default_days: int,
    overlap_minutes: int,
    override_days: int | None = None,
) -> int:
    """Seconds to look back: an override, the default for a new area, or time since the last run plus overlap."""
    if override_days is not None:
        return override_days * 86400
    if last_start is None:
        return default_days * 86400
    overlap = overlap_minutes * 60
    # If last_start is in the future (clock skew) the elapsed time is negative: fall back to the overlap.
    return max(overlap, int((now - last_start).total_seconds()) + overlap)


def indeed_from_days(seconds: int) -> int:
    """Smallest Indeed `fromDays` value (1, 3, 7 or 14) that covers the window."""
    days = math.ceil(seconds / 86400)
    return next((d for d in _INDEED_FROM_DAYS if d >= days), _INDEED_FROM_DAYS[-1])
