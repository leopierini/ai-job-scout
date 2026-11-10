"""One run: clean up, search, filter, score, save. Every step reports StepEvents (the run's log)."""

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Self

import httpx

from jobscout import _dedup as dedup
from jobscout import _output_sync as sync
from jobscout import _search as search
from jobscout._steps import STEPS, UNEXPECTED, StepResult, StepRunner, ignore  # noqa: F401 - STEPS is public here
from jobscout._text import plural
from jobscout.cleanup import run_cleanup
from jobscout.costs import billed_cost
from jobscout.db import Database
from jobscout.errors import ConfigError, NotionError
from jobscout.models import JobRow, JobState, RunSummary, ScoreResult, StepEvent
from jobscout.normalize import PlaceMatcher, TermMatcher, dedup_key
from jobscout.outputs.notion import NotionClient
from jobscout.outputs.webhook import WebhookOutput
from jobscout.scoring.providers import FakeProvider, Provider, make_provider
from jobscout.scoring.scorer import ScoreOutcome, score_jobs
from jobscout.settings import OutputSettings, Secrets, Settings
from jobscout.sources import Source
from jobscout.sources import sample as sample_mode
from jobscout.sources.apify import ApifyClient
from jobscout.sources.indeed import IndeedSource
from jobscout.sources.linkedin import LinkedInSource
from jobscout.sources.sample import SampleSource, sample_answers

COUNT_KEYS = (
    "archived", "searched", "billed_linkedin", "billed_indeed", "new", "duplicates", "excluded",
    "scored", "kept", "below", "failed_scoring", "delivered", "delivery_failed",
)  # fmt: skip


class _Heartbeat:
    """Wraps the AI provider: the run shows a sign of life before every (possibly long) AI call."""

    def __init__(self, provider: Provider, beat: Callable[[], None]) -> None:
        self._provider, self._beat = provider, beat

    def complete(self, prompt: str) -> str:
        self._beat()
        return self._provider.complete(prompt)


class Pipeline(StepRunner):
    def __init__(
        self,
        settings: Settings,
        secrets: Secrets,
        db: Database,
        profile_md: str,
        sources: list[Source],
        provider: Provider | None,
        *,
        notion: NotionClient | None = None,
        webhook: WebhookOutput | None = None,
        now: Callable[[], datetime] = datetime.now,
        on_event: Callable[[StepEvent], None] = ignore,
        cancel: threading.Event | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(db, on_event, cancel)
        self._settings = settings
        self._secrets = secrets
        self._profile_md = profile_md
        self._sources = sources
        self._provider = provider
        self._notion = notion
        self._webhook = webhook
        self._now = now
        self._client = client  # owned: closed by close()
        self._start_time = now()
        self._counts: dict[str, int] = {}
        self._billed: dict[str, int] = {}
        self._notion_blocked = False  # Notion's statuses couldn't be read: push nothing there this run
        self._found_now: set[str] = set()  # pks this run's search inserted

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- runs -------------------------------------------------------------

    def run(self, trigger: str = "cli", override_days: int | None = None, run_id: int | None = None) -> RunSummary:
        return self._execute(
            trigger,
            {
                "cleanup": self._cleanup,
                "search": lambda: self._search(override_days),
                "filter": self._filter,
                "score": self._score,
                "save": self._save,
            },
            run_id,
        )

    def rescore(self, trigger: str = "cli", run_id: int | None = None) -> RunSummary:
        """Score again every stored job still To review (AI tokens only, never Apify), then save."""
        return self._execute(trigger, {"score": self._rescore, "save": self._save}, run_id)

    def _execute(
        self, trigger: str, steps: dict[str, Callable[[], StepResult]], run_id: int | None = None
    ) -> RunSummary:
        """Run the steps. A `run_id` is a run the caller already started (the server answers with it first)."""
        self._start_time = self._now()
        self._run_id = run_id if run_id is not None else self._db.start_run(trigger, self._start_time)
        self._counts = dict.fromkeys(COUNT_KEYS, 0)
        self._billed = {}
        self._closed = set()
        self._notion_blocked = False
        self._found_now = set()
        status, error = "error", UNEXPECTED
        try:
            status, error = self._run_steps(steps)
        except BaseException:
            self._close_open_steps()
            raise
        finally:
            summary = RunSummary(
                run_id=self._run_id,
                status=status,
                counts=dict(self._counts),
                cost_usd=billed_cost(self._billed, self._settings),
                error=error,
            )
            self._db.finish_run(self._run_id, summary)
        return summary

    def _beat(self) -> None:
        self._db.heartbeat(self._run_id)

    def _apply_threshold(self) -> None:
        """A changed minimum score applies to stored scores too, without asking the AI again. It runs after
        the Notion read-back: a job marked Applied there is no longer To review, so it is never dropped."""
        self._db.apply_threshold(self._settings.scoring.min_score)

    # --- 1. cleanup -------------------------------------------------------

    def _cleanup(self) -> StepResult:
        changes = not_archived = 0
        if self._notion is not None:
            try:
                changes = sync.read_back_notion_statuses(
                    self._db, self._notion, self._settings.outputs.notion.database_id
                )
            except NotionError as exc:
                # Without Notion's statuses a job marked Applied there could be archived: archive nothing,
                # and push nothing to Notion this run.
                self._notion_blocked = True
                self._apply_threshold()
                return StepResult(f"Nothing archived: couldn't read statuses from Notion. {exc}")
        self._apply_threshold()
        archived = run_cleanup(self._db, self._settings, self._now().date())
        self._counts["archived"] = len(archived)
        if self._notion is not None:
            include_below = self._settings.scoring.send_below_threshold
            not_archived = sync.archive_orphaned_pages(self._db, self._notion, include_below, self._beat)
        message = f"{len(archived)} archived"
        if changes:
            message += f" · {plural(changes, 'status change')} from Notion"
        if not_archived:
            message += f" · {plural(not_archived, 'Notion page')} not archived"
        return StepResult(message, data={"archived": [{"id": pk, "reason": why} for pk, why in archived]})

    # --- 2. search --------------------------------------------------------

    def _search(self, override_days: int | None) -> StepResult:
        plan = [(source, area) for source in self._sources for area in source.areas()]
        if not plan:
            return StepResult("No searches configured", status="skipped")
        total = sum(area.searches for _, area in plan)
        searches_done = postings = 0
        incomplete: list[str] = []
        areas: list[dict] = []
        stop: str | None = None  # a limit or a rejected token: no further area can succeed

        def summary() -> str:
            return f"{searches_done} of {plural(total, 'search', 'searches')} · {plural(postings, 'posting')}"

        for source, area in plan:
            self._check_cancel(summary())
            area_done = 0

            def progress(done: int, area_total: int, found: int) -> None:
                nonlocal area_done
                area_done = done
                message = (
                    f"{area.label}: {done} of {plural(area_total, 'search', 'searches')}"
                    f" · {plural(postings + found, 'posting')} so far"
                )
                self._emit("running", message, searches_done + done, total)

            seconds, may_commit = search.area_window(
                self._db, area, self._start_time, self._settings.windows, override_days
            )
            result = search.search(source, area, seconds, progress)
            # Save first: these results are paid for, whatever happens next.
            self._found_now.update(
                self._db.insert_scraped(
                    result.jobs, {j.pk: dedup_key(j.title, j.company, j.location) for j in result.jobs}
                )
            )
            searches_done += area_done
            postings += len(result.jobs)
            self._counts["searched"] += len(result.jobs)
            self._billed[source.name] = self._billed.get(source.name, 0) + result.billed
            if f"billed_{source.name}" in self._counts:
                self._counts[f"billed_{source.name}"] += result.billed
            error = result.error
            complete = result.complete and error is None
            areas.append(
                {
                    "key": area.key, "label": area.label, "postings": len(result.jobs), "billed": result.billed,
                    "complete": complete, "error": str(error) if error else None,
                }
            )  # fmt: skip
            if complete:
                if may_commit:
                    self._db.commit_window(area.key, self._start_time)
            elif error is not None and error.kind in search.STOP_ALL:
                stop = str(error)
                break
            elif error is not None:
                incomplete.append(f"{area.label} ({error})")
            else:
                self._check_cancel(summary())  # the source stopped early because of a cancel

        data = {"areas": areas}
        if stop is not None:
            return StepResult(stop, status="error", done=searches_done, total=total, data=data)
        message = summary()
        if incomplete:
            message += f" · incomplete, retried next run: {'; '.join(incomplete)}"
        if not any(a["complete"] for a in areas):
            first = next((a["error"] for a in areas if a["error"]), "")
            error_text = f"No search completed. {first}".strip()
            return StepResult(message, "error", searches_done, total, data, error_text)
        return StepResult(message, done=searches_done, total=total, data=data)

    # --- 3. filter --------------------------------------------------------

    def _filter(self) -> StepResult:
        f = self._settings.filters
        c = dedup.filter_scraped(
            self._db,
            PlaceMatcher(f.excluded_countries, f.excluded_cities),
            self._found_now,
            TermMatcher(f.excluded_title_words),
            frozenset(f.languages),
        )
        self._counts.update(new=c.new, duplicates=c.duplicates, excluded=c.excluded)
        message = f"{c.new} new · {plural(c.duplicates, 'duplicate')} · {plural(c.places, 'excluded place')}"
        if c.titles:
            message += f" · {plural(c.titles, 'excluded title')}"
        if c.languages:
            message += f" · {c.languages} in another language"
        if c.waiting:
            message += f" · {c.waiting} waiting from earlier runs"
        return StepResult(message, done=c.checked, total=c.checked)

    # --- 4. score ---------------------------------------------------------

    def _score(self) -> StepResult:
        if self._provider is None:
            return StepResult("No AI provider configured", status="skipped")
        cap = self._settings.scoring.max_jobs_per_run
        waiting = self._db.jobs_in_state(JobState.SCRAPED)
        batch = waiting if cap is None else waiting[:cap]
        outcome = self._score_rows(batch, self._provider)
        self._check_cancel(f"{len(outcome.results)} of {len(batch)} scored")
        retry = len(outcome.failed) + len(waiting) - len(batch)
        return self._score_result(outcome, len(batch), f"{retry} will be retried next run" if retry else None)

    def _rescore(self) -> StepResult:
        """Score the candidates in place: a job whose new score fails keeps its previous score and state."""
        if self._provider is None:
            return StepResult("No AI provider configured", status="skipped")
        cfg = self._settings.scoring
        notes: list[str] = []
        notion = self._notion
        if notion is not None:
            # Notion's statuses first: a job marked Applied there is no longer a candidate.
            try:
                changes = sync.read_back_notion_statuses(self._db, notion, self._settings.outputs.notion.database_id)
            except NotionError as exc:
                self._notion_blocked, notion = True, None
                notes.append(f"Notion not updated: couldn't read statuses from Notion. {exc}")
            else:
                notes += [f"{plural(changes, 'status change')} from Notion"] if changes else []
        self._apply_threshold()
        rows = [row for pk in self._db.rescore_candidates() if (row := self._db.get(pk)) is not None]
        not_archived = 0

        def requeue(results: list[ScoreResult]) -> None:
            nonlocal not_archived
            not_archived += sync.mark_rescored(
                self._db, notion, results, cfg.min_score, cfg.send_below_threshold, self._beat
            )

        outcome = self._score_rows(rows, self._provider, requeue)
        self._check_cancel(f"{len(outcome.results)} of {len(rows)} scored")
        unchanged = len(rows) - len(outcome.results)
        result = self._score_result(outcome, len(rows), f"{plural(unchanged, 'job')} kept {'its' if unchanged == 1 else 'their'} previous score" if unchanged else None)
        if not_archived:
            notes.append(f"{plural(not_archived, 'Notion page')} not archived")
        result.message = " · ".join([result.message, *notes])
        return result

    def _score_rows(
        self, rows: list[JobRow], provider: Provider, after_save: Callable[[list[ScoreResult]], None] | None = None
    ) -> ScoreOutcome:
        cfg = self._settings.scoring

        def save(results: list[ScoreResult]) -> None:
            # Each batch is saved as soon as it is paid for: a crash later in the step loses nothing.
            for r in results:
                self._db.save_score(r, cfg.min_score)
                self._counts["kept" if r.score >= cfg.min_score else "below"] += 1
            self._counts["scored"] += len(results)
            if after_save is not None:
                after_save(results)

        provider = _Heartbeat(provider, self._beat)
        outcome = score_jobs(
            list(rows), provider, self._profile_md, cfg, self._now().date(),
            lambda done, total: self._emit("running", f"{done} of {total} scored", done, total),
            self._cancel, on_results=save,
        )  # fmt: skip
        self._counts["failed_scoring"] += len(outcome.failed)
        return outcome

    def _score_result(self, outcome: ScoreOutcome, total: int, note: str | None) -> StepResult:
        """Message "{done} of {total} scored"; the step fails only when every job failed with an AI error."""
        scored = len(outcome.results)
        message = f"{scored} of {total} scored"
        if note:
            message += f" · {note}"
        if outcome.errors:
            message += f" · {outcome.errors[0]}"
        if total and not scored and outcome.errors:
            return StepResult(message, "error", scored, total, {"errors": outcome.errors}, outcome.errors[0])
        return StepResult(message, done=scored, total=total, data={"errors": outcome.errors})

    # --- 5. save ----------------------------------------------------------

    def _save(self) -> StepResult:
        self._counts["duplicates"] += dedup.drop_kept_duplicates(self._db)
        csv_note = sync.auto_export_csv(self._db, self._settings.outputs.csv)
        include_below = self._settings.scoring.send_below_threshold
        sends: list[Callable[[], bool]] = []
        if self._webhook is not None:
            hook = self._webhook
            # The webhook gets each job once; only Notion follows status changes.
            for row in self._db.deliveries_needed(sync.WEBHOOK, include_below, sync_status=False):
                sends.append(lambda row=row: sync.deliver_webhook(self._db, hook, row))
        if self._notion is not None and not self._notion_blocked:
            notion, database_id = self._notion, self._settings.outputs.notion.database_id
            for row in self._db.deliveries_needed(sync.NOTION, include_below):
                sends.append(lambda row=row: sync.deliver_notion(self._db, notion, database_id, row))
        delivered = failed = 0

        def message() -> str:
            return f"{self._counts['kept']} kept · {delivered} sent · {failed} failed"

        for send in sends:
            self._check_cancel(message())
            if send():
                delivered += 1
            else:
                failed += 1
            self._emit("running", message(), delivered + failed, len(sends))
        self._counts.update(delivered=delivered, delivery_failed=failed)
        final = f"{message()} · {csv_note}" if csv_note else message()
        return StepResult(final, done=delivered + failed, total=len(sends))


def build_pipeline(
    settings: Settings,
    secrets: Secrets,
    db: Database,
    profile_md: str,
    *,
    sample: bool = False,
    for_rescore: bool = False,
    paced: bool = False,
    on_event: Callable[[StepEvent], None] = ignore,
    cancel: threading.Event | None = None,
) -> Pipeline:
    """Wire a pipeline from settings. Raises ConfigError before anything is spent.

    `for_rescore`: no sources (a re-score never searches), so no Apify token is needed.
    `paced`: a sample run pauses briefly between steps so its progress can be watched (the app turns this on).
    """
    cancel = cancel if cancel is not None else threading.Event()
    if sample:
        quiet = settings.model_copy(update={"outputs": OutputSettings()})
        pace = sample_mode.PACE_SECONDS if paced else 0.0  # read through the module so tests can set it to 0
        return Pipeline(
            quiet, secrets, db, profile_md,
            [SampleSource(cancel, pace=pace)], FakeProvider(answers=sample_answers(), delay=pace, cancel=cancel),
            on_event=on_event, cancel=cancel,
        )  # fmt: skip
    if settings.scoring.provider == "fake":
        raise ConfigError("Pick an AI provider first.")

    li, ind = settings.linkedin, settings.indeed
    # An enabled source with nothing to search is skipped, not an error.
    use_linkedin = not for_rescore and li.enabled and bool(li.keywords) and bool(li.regions)
    use_indeed = not for_rescore and ind.enabled and bool(ind.queries) and bool(ind.countries) and bool(ind.job_types)
    if (use_linkedin or use_indeed) and not secrets.apify_token:
        raise ConfigError("Add your Apify token first.")
    provider = make_provider(settings.scoring, secrets)
    notion_cfg, hook_cfg = settings.outputs.notion, settings.outputs.webhook
    if notion_cfg.enabled and not secrets.notion_token:
        raise ConfigError("Notion is on but has no token. Add your Notion token, or turn Notion off.")
    if notion_cfg.enabled and not notion_cfg.database_id:
        raise ConfigError("Notion is on but has no database yet. Create the database, or turn Notion off.")
    if hook_cfg.enabled and not hook_cfg.url.strip():
        raise ConfigError("The webhook is on but has no URL. Add one, or turn the webhook off.")

    needs_http = use_linkedin or use_indeed or notion_cfg.enabled or hook_cfg.enabled
    client = httpx.Client() if needs_http else None
    sources: list[Source] = []
    if client is not None and (use_linkedin or use_indeed):
        apify = ApifyClient(secrets.apify_token or "", client)
        if use_linkedin:
            sources.append(LinkedInSource(li, apify, cancel))
        if use_indeed:
            sources.append(IndeedSource(ind, apify, cancel))
    notion = NotionClient(secrets.notion_token or "", client) if client and notion_cfg.enabled else None
    webhook = WebhookOutput(hook_cfg.url.strip(), client) if client and hook_cfg.enabled else None
    return Pipeline(
        settings, secrets, db, profile_md, sources, provider,
        notion=notion, webhook=webhook, on_event=on_event, cancel=cancel, client=client,
    )  # fmt: skip


def build_app_pipeline(*args, **kwargs) -> Pipeline:
    """What the web app uses: `build_pipeline`, with the sample run paced so its steps can be watched."""
    return build_pipeline(*args, paced=True, **kwargs)
