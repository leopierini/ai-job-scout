"""Apify cost estimates and the remaining account credit."""

from dataclasses import dataclass

import httpx

from jobscout.settings import ApifyBoardSettings, Settings
from jobscout.startup_places import apply_shared
from jobscout.sources.indeed import searches as indeed_searches

LIMITS_URL = "https://api.apify.com/v2/users/me/limits"

# Job boards searched through an Apify actor with ApifyBoardSettings; each bills per result.
APIFY_BOARDS = ("wttj", "wellfound", "yc", "atsindex")
PAID_SOURCES = ("linkedin", "indeed", *APIFY_BOARDS)
# What an actor charges each time it starts, on top of its results (Wellfound's default actor, checked 2026-11-12).
START_FEES_USD = {"wellfound": 0.005}


def board_searches(b: ApifyBoardSettings) -> int:
    """Searches one run of an Apify board makes: each query in each location (or once, with no location)."""
    return len(b.queries) * max(1, len(b.locations)) if b.enabled else 0


@dataclass
class CostEstimate:
    linkedin_searches: int
    linkedin_max_results: int
    indeed_searches: int
    indeed_max_results: int
    max_usd: float
    board_searches: int = 0  # the other Apify boards (Welcome to the Jungle, Wellfound, ...) together
    board_max_results: int = 0

    @property
    def searches(self) -> int:
        """Every paid (Apify) search. Free sources are not counted: they cost nothing."""
        return self.linkedin_searches + self.indeed_searches + self.board_searches

    @property
    def max_results(self) -> int:
        return self.linkedin_max_results + self.indeed_max_results + self.board_max_results


def estimate(s: Settings) -> CostEstimate:
    """Worst-case searches, results and dollars for one run with these settings."""
    s = apply_shared(s)
    li, ind = s.linkedin, s.indeed
    li_searches = len(li.regions) * len(li.keywords) if li.enabled else 0
    li_results = li_searches * li.cap_per_keyword
    ind_searches = len(ind.countries) * len(indeed_searches(ind)) if ind.enabled else 0
    ind_results = ind_searches * ind.max_rows_per_query
    max_usd = li_results * li.price_per_result + ind_results * ind.price_per_result
    other_searches = other_results = 0
    for name in APIFY_BOARDS:
        board: ApifyBoardSettings = getattr(s, name)
        n = board_searches(board)
        other_searches += n
        other_results += n * board.max_results_per_search
        max_usd += n * (board.max_results_per_search * board.price_per_result + START_FEES_USD.get(name, 0.0))
    return CostEstimate(li_searches, li_results, ind_searches, ind_results, max_usd, other_searches, other_results)


def billed_cost(billed: dict[str, int], s: Settings) -> float:
    """Dollars for the results each source actually billed, keyed by source name. Free sources cost nothing."""
    return sum(count * getattr(s, name).price_per_result for name, count in billed.items() if name in PAID_SOURCES)


def apify_credit(token: str, client: httpx.Client) -> float | None:
    """Remaining monthly Apify usage in USD, or None on any error or unexpected response.

    Response shape per "Get account limits" (GET /v2/users/me/limits), checked 2026-11-01:
    https://docs.apify.com/api/v2/users-me-limits-get
    {"data": {"limits": {"maxMonthlyUsageUsd": <number>}, "current": {"monthlyUsageUsd": <number>}, ...}}
    """
    try:
        response = client.get(LIMITS_URL, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        data = response.json()["data"]
        limit = data["limits"]["maxMonthlyUsageUsd"]
        used = data["current"]["monthlyUsageUsd"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if isinstance(limit, bool) or isinstance(used, bool):
        return None
    if not isinstance(limit, (int, float)) or not isinstance(used, (int, float)):
        return None
    return max(0.0, float(limit) - float(used))
