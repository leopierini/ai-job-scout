"""Apify cost estimates and the remaining account credit."""

from dataclasses import dataclass

import httpx

from jobscout.settings import Settings
from jobscout.sources.indeed import searches as indeed_searches

LIMITS_URL = "https://api.apify.com/v2/users/me/limits"


@dataclass
class CostEstimate:
    linkedin_searches: int
    linkedin_max_results: int
    indeed_searches: int
    indeed_max_results: int
    max_usd: float


def estimate(s: Settings) -> CostEstimate:
    """Worst-case searches, results and dollars for one run with these settings."""
    li, ind = s.linkedin, s.indeed
    li_searches = len(li.regions) * len(li.keywords) if li.enabled else 0
    li_results = li_searches * li.cap_per_keyword
    ind_searches = len(ind.countries) * len(indeed_searches(ind)) if ind.enabled else 0
    ind_results = ind_searches * ind.max_rows_per_query
    max_usd = li_results * li.price_per_result + ind_results * ind.price_per_result
    return CostEstimate(li_searches, li_results, ind_searches, ind_results, max_usd)


def billed_cost(billed: dict[str, int], s: Settings) -> float:
    """Dollars for the results each source actually billed (keys "linkedin" and "indeed")."""
    return (
        billed.get("linkedin", 0) * s.linkedin.price_per_result
        + billed.get("indeed", 0) * s.indeed.price_per_result
    )


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
