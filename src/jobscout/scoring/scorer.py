"""Score jobs in batches with one retry for jobs the model skipped."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from jobscout._text import plural
from jobscout.errors import ProviderError
from jobscout.models import Job, ScoreResult
from jobscout.scoring.parse import parse_scores
from jobscout.scoring.prompt import build_prompt
from jobscout.scoring.providers import Provider
from jobscout.settings import ScoringSettings

UNEXPECTED = "The AI request failed unexpectedly."


@dataclass
class ScoreOutcome:
    results: list[ScoreResult] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _ask(
    jobs: list[Job], provider: Provider, profile_md: str, cfg: ScoringSettings, today: date
) -> tuple[list[ScoreResult], list[Job], str | None]:
    """Score one batch. Returns the results, the jobs left without one, and an error message."""
    try:
        text = provider.complete(build_prompt(profile_md, cfg, jobs, today))
    except ProviderError as exc:
        return [], jobs, str(exc)
    except Exception:  # noqa: BLE001 - one broken batch must not lose the others; the text may hold secrets
        return [], jobs, UNEXPECTED
    results, missing = parse_scores(text, {j.pk for j in jobs})
    return results, [j for j in jobs if j.pk in missing], None


OnResults = Callable[[list[ScoreResult]], None]


def _hand_over(results: list[ScoreResult], out: ScoreOutcome, on_results: OnResults) -> None:
    """Give paid-for scores to the caller at once, so nothing is lost if a later batch crashes."""
    out.results += results
    if results:
        on_results(results)


def _ignore(_results: list[ScoreResult]) -> None:
    pass


def _score_batch(
    batch: list[Job], provider: Provider, profile_md: str, cfg: ScoringSettings, today: date,
    cancel: threading.Event, out: ScoreOutcome, on_results: OnResults,
) -> None:
    results, missing, error = _ask(batch, provider, profile_md, cfg, today)
    _hand_over(results, out, on_results)
    if missing and error is None and not cancel.is_set():
        retried, missing, error = _ask(missing, provider, profile_md, cfg, today)
        _hand_over(retried, out, on_results)
    out.failed += [j.pk for j in missing]
    if error:
        out.errors.append(error)
    elif missing:
        out.errors.append(f"The AI didn't return a valid score for {plural(len(missing), 'job')}.")


def score_jobs(
    jobs: list[Job],
    provider: Provider,
    profile_md: str,
    cfg: ScoringSettings,
    today: date,
    on_progress: Callable[[int, int], None],
    cancel: threading.Event,
    on_results: OnResults = _ignore,
) -> ScoreOutcome:
    """Score `jobs` batch by batch. `on_results` gets each batch's scores (and the retry's) as soon as they arrive."""
    out = ScoreOutcome()
    total = len(jobs)
    on_progress(0, total)
    for start in range(0, total, cfg.batch_size):
        if cancel.is_set():
            out.failed += [j.pk for j in jobs[start:]]
            break
        batch = jobs[start : start + cfg.batch_size]
        _score_batch(batch, provider, profile_md, cfg, today, cancel, out, on_results)
        on_progress(start + len(batch), total)
    return out
