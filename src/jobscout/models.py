"""Data types shared across the engine."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Status(StrEnum):
    TO_REVIEW = "to_review"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"


STATUS_LABELS: dict[Status, str] = {
    Status.TO_REVIEW: "To review",
    Status.INTERESTED: "Interested",
    Status.NOT_INTERESTED: "Not interested",
    Status.APPLIED: "Applied",
    Status.INTERVIEWING: "Interviewing",
    Status.OFFER: "Offer",
    Status.REJECTED: "Rejected",
}

ACTIVE_STATUSES = {Status.APPLIED, Status.INTERVIEWING, Status.OFFER}

# How each job source is named to people (a job's `source` field is the key).
SOURCE_LABELS: dict[str, str] = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "builtineurope": "Built in Europe",
    "jobgether": "Jobgether",
    "vcboards": "VC job boards",
    "careers": "Career pages",
    "wttj": "Welcome to the Jungle",
    "wellfound": "Wellfound",
    "yc": "Y Combinator",
    "atsindex": "Company job boards",
    "sample": "Sample",
}


class JobState(StrEnum):
    SCRAPED = "scraped"
    EXCLUDED = "excluded"
    DUPLICATE = "duplicate"
    KEPT = "kept"
    BELOW_THRESHOLD = "below_threshold"


@dataclass
class Job:
    source: str
    job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    posted: str
    keyword: str
    area: str
    recruiter: str = ""
    recruiter_title: str = ""
    recruiter_url: str = ""
    employment_type: str = ""
    salary: str = ""  # as the source states it, e.g. "EUR 38,000–45,000 a year"; empty when not stated

    @property
    def pk(self) -> str:
        return f"{self.source}:{self.job_id}"


@dataclass(kw_only=True)
class JobRow(Job):
    """A job as stored in the database, with its pipeline state and score."""

    state: JobState
    status: Status
    score: int | None
    reason: str
    flags: list[str]
    start_date: str
    deadline: str
    archived_reason: str | None
    dedup_key: str
    first_seen_at: str
    status_updated_at: str
    restored: bool = False  # restored from the archive: cleanup's date rules skip it until its status changes


@dataclass
class ScoreResult:
    pk: str
    score: int
    reason: str
    flags: list[str]
    start_date: str
    deadline: str


StepName = Literal["cleanup", "search", "filter", "score", "save"]
StepStatus = Literal["pending", "running", "done", "error", "skipped"]


@dataclass
class StepEvent:
    step: StepName
    status: StepStatus
    message: str
    done: int | None = None
    total: int | None = None
    elapsed_s: float = 0.0
    data: dict = field(default_factory=dict)


@dataclass
class RunSummary:
    run_id: int
    status: Literal["done", "error", "cancelled"]
    counts: dict[str, int]
    cost_usd: float
    error: str | None = None
