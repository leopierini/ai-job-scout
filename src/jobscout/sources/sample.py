"""Sample mode: fictional postings, profile and settings, so the app can be tried with no keys and no spending."""

import json
import threading
from collections.abc import Callable
from datetime import date, timedelta
from importlib.resources import files

from jobscout.models import Job
from jobscout.settings import (
    Dealbreaker,
    FilterSettings,
    LinkedInSettings,
    Region,
    ScoringSettings,
    Settings,
    UiSettings,
)
from jobscout.sources import AreaResult, SearchArea

_DATA = files("jobscout.data")
SAMPLE_PROFILE: str = _DATA.joinpath("sample_profile.md").read_text(encoding="utf-8")
AREAS = ("Europe", "UK")
# When the app runs the sample, each search and each scoring batch takes about this long, so the steps can be
# watched. The command line and the tests run it with no pause.
PACE_SECONDS = 1.2


def _postings() -> list[dict]:
    return json.loads(_DATA.joinpath("sample_jobs.json").read_text(encoding="utf-8"))


ANSWER_FIELDS = ("score", "reason", "flags", "start_date")


def sample_answers(today: date | None = None) -> dict[str, dict]:
    """What the offline scorer answers for each sample posting it scores, keyed by the job id it is shown (the
    pk). The excluded and duplicate postings are never scored and have none. A start date given as
    `start_in_days` is counted from today, like `days_ago`, so the sample never goes stale."""
    today = today or date.today()

    def answer(p: dict) -> dict:
        out = {k: p[k] for k in ANSWER_FIELDS if k in p}
        if "start_in_days" in p:
            out["start_date"] = (today + timedelta(days=p["start_in_days"])).isoformat()
        return out

    return {
        f"{SampleSource.name}:{p['id']}": answer(p)
        for p in _postings()
        if all(k in p for k in ANSWER_FIELDS if k != "start_date") and ("start_date" in p or "start_in_days" in p)
    }


def sample_settings() -> Settings:
    """Settings for the fictional sample person: two keywords, Europe and the UK, Switzerland excluded."""
    return Settings(
        linkedin=LinkedInSettings(
            keywords=["Junior software engineer", "Backend developer"],
            regions=[
                Region(name="European Economic Area", geo_id="91000002"),
                Region(name="United Kingdom", geo_id="101165590"),
            ],
        ),
        filters=FilterSettings(excluded_countries=["Switzerland"]),
        ui=UiSettings(headline="Software engineer, early career · Europe and the UK"),
        scoring=ScoringSettings(
            provider="fake",
            model="fake",
            dealbreakers=[Dealbreaker(text="the role needs five or more years of experience", consequence="below 40")],
        ),
    )


class SampleSource:
    """Returns the bundled fictional postings. No network, nothing billed."""

    name = "sample"

    def __init__(self, cancel: threading.Event, *, today: date | None = None, pace: float = 0.0) -> None:
        self._cancel = cancel
        self._today = today
        self._pace = pace
        self._postings = _postings()

    def areas(self) -> list[SearchArea]:
        areas = []
        for area in AREAS:
            keywords = list(dict.fromkeys(p["keyword"] for p in self._postings if p["area"] == area))
            areas.append(
                SearchArea(
                    key=f"sample:{area}",
                    label=area,
                    source=self.name,
                    searches=len(keywords),
                    params={"area": area, "keywords": keywords},
                )
            )
        return areas

    def search_area(
        self,
        area: SearchArea,
        window_seconds: int,
        progress: Callable[[int, int, int], None],
    ) -> AreaResult:
        """One "search" per keyword; the window is ignored (sample postings are always recent)."""
        keywords = area.params["keywords"]
        today = self._today or date.today()
        jobs: list[Job] = []
        complete = True
        progress(0, len(keywords), 0)
        for done, keyword in enumerate(keywords):
            if self._pace:
                self._cancel.wait(self._pace)  # a pause the Cancel button cuts short
            if self._cancel.is_set():
                complete = False
                break
            for p in self._postings:
                if p["area"] == area.params["area"] and p["keyword"] == keyword:
                    jobs.append(self._job(p, area.label, today))
            progress(done + 1, len(keywords), len(jobs))
        return AreaResult(jobs=jobs, billed=0, complete=complete)

    def _job(self, p: dict, area_label: str, today: date) -> Job:
        description = p["description"]
        if "deadline_in_days" in p:  # like `days_ago`, counted from today so the sample never goes stale
            description += f"\nDeadline: {(today + timedelta(days=p['deadline_in_days'])).isoformat()}"
        return Job(
            source=self.name,
            job_id=p["id"],
            title=p["title"],
            company=p["company"],
            location=p["location"],
            url=f"https://example.com/sample-jobs/{p['id']}",
            description=description,
            posted=(today - timedelta(days=p["days_ago"])).isoformat(),
            keyword=p["keyword"],
            area=area_label,
            recruiter=p.get("recruiter", ""),
            recruiter_title=p.get("recruiter_title", ""),
            employment_type=p.get("employment_type", ""),
        )
