"""Y Combinator's Work at a Startup through an Apify actor. STUB: to be implemented."""

import threading
from collections.abc import Callable

from jobscout.settings import YcSettings
from jobscout.sources import AreaResult, SearchArea
from jobscout.sources.apify import ApifyClient


class YcSource:
    name = "yc"

    def __init__(self, settings: YcSettings, apify: ApifyClient, cancel: threading.Event) -> None:
        self._settings = settings
        self._apify = apify
        self._cancel = cancel

    def areas(self) -> list[SearchArea]:
        return []

    def search_area(
        self, area: SearchArea, window_seconds: int, progress: Callable[[int, int, int], None]
    ) -> AreaResult:
        return AreaResult(jobs=[], billed=0, complete=True)
