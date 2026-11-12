"""Companies' own job boards: Ashby, Greenhouse, Lever, Recruitee, Workable, Personio, Teamtailor. STUB: to be implemented."""

import threading
import time
from collections.abc import Callable
from datetime import date

import httpx

from jobscout.settings import CareerPagesSettings
from jobscout.sources import AreaResult, SearchArea


class CareerPagesSource:
    name = "careers"

    def __init__(
        self,
        settings: CareerPagesSettings,
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
        return []

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        return AreaResult(jobs=[], billed=0, complete=True)
