"""An index of many companies' own job boards, searched at once through an Apify actor. STUB: to be implemented."""

import threading
from collections.abc import Callable

from jobscout.settings import AtsIndexSettings
from jobscout.sources import AreaResult, SearchArea
from jobscout.sources.apify import ApifyClient


class AtsIndexSource:
    name = "atsindex"

    def __init__(self, settings: AtsIndexSettings, apify: ApifyClient, cancel: threading.Event) -> None:
        self._settings = settings
        self._apify = apify
        self._cancel = cancel

    def areas(self) -> list[SearchArea]:
        return []

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        return AreaResult(jobs=[], billed=0, complete=True)
