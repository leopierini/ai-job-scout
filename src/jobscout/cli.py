"""Command-line interface: run, estimate, cleanup, rescore, export, paths, start."""

import argparse
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

import httpx

from jobscout import paths
from jobscout._text import plural
from jobscout.cleanup import run_cleanup
from jobscout.costs import apify_credit, estimate
from jobscout.db import Database
from jobscout._output_sync import notion_status_changes, read_back_notion_statuses
from jobscout.errors import ConfigError, NotionError, RunInProgress
from jobscout.models import RunSummary, Status, StepEvent
from jobscout.outputs.csv_out import export_csv
from jobscout.outputs.notion import NotionClient
from jobscout.pipeline import STEPS, Pipeline, build_pipeline
from jobscout.settings import Secrets, Settings, load_profile, load_secrets, load_settings
from jobscout.sources.sample import SAMPLE_PROFILE, sample_settings

STEP_TITLES = {"cleanup": "Clean up", "search": "Search", "filter": "Filter", "score": "Score", "save": "Save"}
STATUS_LABELS = {"pending": "Pending", "running": "Running", "done": "Done", "error": "Error", "skipped": "Skipped"}
WINDOW_WORDS = {"today": 1, "week": 7, "month": 30}
PROGRESS_STEPS = ("search", "score")  # the steps whose running events are worth printing
RUN_IN_PROGRESS = "Another run is in progress. Wait for it to finish, or try again in a few minutes."


def parse_window(value: str) -> int:
    """`today`, `week`, `month` or a positive number of days."""
    if value in WINDOW_WORDS:
        return WINDOW_WORDS[value]
    try:
        days = int(value)
    except ValueError:
        days = 0
    if days < 1:
        raise argparse.ArgumentTypeError(f"{value!r} is not today, week, month or a number of days")
    return days


class _Printer:
    """Prints a run's events: each step's final event, and search and score progress when it changes."""

    def __init__(self) -> None:
        self._last_done: dict[str, int | None] = {}

    def __call__(self, ev: StepEvent) -> None:
        if ev.status == "running":
            if ev.step not in PROGRESS_STEPS or not ev.done or self._last_done.get(ev.step) == ev.done:
                return
            self._last_done[ev.step] = ev.done
        index = STEPS.index(ev.step) + 1
        title, label = STEP_TITLES[ev.step], STATUS_LABELS[ev.status]
        print(f"[{index}/{len(STEPS)}] {title} · {label} · {ev.message}", flush=True)


@contextmanager
def _cancel_on_ctrl_c(cancel: threading.Event) -> Iterator[None]:
    """The first Ctrl+C asks the run to stop cleanly; the second one behaves as usual."""
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum: int, _frame: object) -> None:
        cancel.set()
        signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)
        print("Cancelling after the current step... press Ctrl+C again to quit now.", file=sys.stderr, flush=True)

    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread: no handler, the run simply can't be interrupted this way
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)


def _real_inputs() -> tuple[Settings, Secrets, str]:
    settings, secrets, profile = load_settings(), load_secrets(), load_profile()
    if not profile.strip():
        raise ConfigError(
            f"Your profile is empty. Write it in {paths.profile_path()} (or use the app's setup wizard)."
        )
    return settings, secrets, profile


Inputs = tuple[Settings, Secrets, str]


def _open_pipeline(
    inputs: Inputs | None, cancel: threading.Event, *, for_rescore: bool = False
) -> tuple[Pipeline, Database]:
    """A pipeline on the real database with these inputs, or on the sample one (no keys) when `inputs`
    is None. The caller closes both."""
    sample = inputs is None
    if inputs is None:
        settings, secrets, profile = sample_settings(), Secrets(), SAMPLE_PROFILE
        db_file = paths.app_dir() / "sample.db"
    else:
        (settings, secrets, profile), db_file = inputs, paths.db_path()
    db = Database(db_file)
    try:
        pipeline = build_pipeline(
            settings, secrets, db, profile, sample=sample, for_rescore=for_rescore, on_event=_Printer(), cancel=cancel
        )
    except BaseException:
        db.close()
        raise
    return pipeline, db


def _finish(summary: RunSummary) -> int:
    if summary.status == "done":
        print(f"Done · {summary.counts.get('kept', 0)} kept · ~${summary.cost_usd:.2f}")
        return 0
    if summary.status == "cancelled":
        print("Cancelled")
        return 1
    print(summary.error or "The run failed.", file=sys.stderr)
    return 1


def _execute(
    inputs: Inputs | None, action: Callable[[Pipeline], RunSummary], *, for_rescore: bool = False
) -> int:
    cancel = threading.Event()
    pipeline, db = _open_pipeline(inputs, cancel, for_rescore=for_rescore)
    try:
        if inputs is not None and not for_rescore:
            print(_cost_line(inputs[0]), flush=True)  # what a run can cost, before anything is spent
        with pipeline, _cancel_on_ctrl_c(cancel):
            summary = action(pipeline)
    finally:
        db.close()
    return _finish(summary)


def _cost_line(settings: Settings) -> str:
    est = estimate(settings)
    searches = est.linkedin_searches + est.indeed_searches
    results = est.linkedin_max_results + est.indeed_max_results
    return f"This run can cost up to ${est.max_usd:.2f} on Apify ({plural(searches, 'search', 'searches')}, at most {plural(results, 'result')})."


def _cmd_run(args: argparse.Namespace) -> int:
    inputs = None if args.sample else _real_inputs()
    return _execute(inputs, lambda p: p.run(override_days=args.window))


def _cmd_rescore(args: argparse.Namespace) -> int:
    inputs = _real_inputs()  # read once; fails early on a missing profile
    with closing(Database(paths.db_path())) as db:
        count = len(db.rescore_candidates())
    if count == 0:
        print("Nothing to re-score.")
        return 0
    print(f"{plural(count, 'job')} will be scored again with the AI (no Apify credit is used).")
    if not args.yes and not _confirm("Continue? [y/N] "):
        print("Cancelled")
        return 1
    return _execute(inputs, lambda p: p.rescore("cli:rescore"), for_rescore=True)


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _cmd_estimate(_args: argparse.Namespace) -> int:
    settings, secrets = load_settings(), load_secrets()
    est = estimate(settings)
    print(f"Searches: {est.linkedin_searches + est.indeed_searches}")
    print(f"Max results: {est.linkedin_max_results + est.indeed_max_results}")
    print(f"Max cost: ${est.max_usd:.2f}")
    if secrets.apify_token:
        with httpx.Client(timeout=15) as client:
            credit = apify_credit(secrets.apify_token, client)
        if credit is not None:
            print(f"Apify credit left: ${credit:.2f}")
    return 0


def _read_notion_statuses(db: Database, settings: Settings, secrets: Secrets, dry_run: bool) -> dict[str, Status]:
    """When Notion is on, learn the statuses changed there first, so a job marked Applied is never archived.

    Saves them, except on a dry run, which saves nothing and returns them instead (pk -> status).
    Raises NotionError if they can't be read.
    """
    notion_cfg = settings.outputs.notion
    if not (notion_cfg.enabled and notion_cfg.database_id and secrets.notion_token):
        return {}
    with httpx.Client() as client:
        notion = NotionClient(secrets.notion_token, client)
        if dry_run:
            return notion_status_changes(db, notion, notion_cfg.database_id)
        read_back_notion_statuses(db, notion, notion_cfg.database_id)
    return {}


def _cmd_cleanup(args: argparse.Namespace) -> int:
    settings, secrets = load_settings(), load_secrets()
    verb = "would archive" if args.dry_run else "archived"
    with closing(Database(paths.db_path())) as db:
        try:
            overrides = _read_notion_statuses(db, settings, secrets, args.dry_run)
        except NotionError as exc:
            print(f"Nothing archived: couldn't read statuses from Notion. {exc}", file=sys.stderr)
            return 1
        stale = run_cleanup(db, settings, date.today(), dry_run=args.dry_run, status_overrides=overrides)
        for pk, reason in stale:
            row = db.get(pk)
            name = f"{row.title} at {row.company}" if row else pk
            print(f"{verb}: {name} · {reason}")
    print(f"{len(stale)} {verb}." if stale else "Nothing to archive.")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    with closing(Database(paths.db_path())) as db:
        count = export_csv(db.tracked(), path)
    print(f"Exported {plural(count, 'job')} to {path}")
    return 0


def _cmd_paths(_args: argparse.Namespace) -> int:
    print(f"App data folder: {paths.app_dir()}")
    print(f"Settings: {paths.settings_path()}")
    print(f"Profile: {paths.profile_path()}")
    print(f"Database: {paths.db_path()}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    from jobscout.server import launch  # imported here so the other commands don't load the web server

    return launch.serve(args.port, not args.no_browser, args.sample)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobscout", description="Find, score and track job postings.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    run = sub.add_parser("run", help="search, score and save new jobs", description="Run the whole pipeline.")
    run.add_argument("--window", type=parse_window, metavar="today|week|month|N",
                     help="search back this far instead of since the last run (N is a number of days)")  # fmt: skip
    run.add_argument("--sample", action="store_true", help="try it with fictional jobs: no keys, no cost")
    run.set_defaults(func=_cmd_run)

    est = sub.add_parser("estimate", help="show the most a run can cost", description="Show the most a run can cost.")
    est.set_defaults(func=_cmd_estimate)

    clean = sub.add_parser(
        "cleanup",
        help="archive jobs that are no longer worth looking at",
        description="Archive stale jobs in the local database (statuses changed in Notion are read first). "
        "Notion pages are archived on your next run.",
    )
    clean.add_argument("--dry-run", action="store_true", help="only list what would be archived")
    clean.set_defaults(func=_cmd_cleanup)

    rescore = sub.add_parser(
        "rescore", help="score stored jobs again", description="Score again every stored job you haven't acted on."
    )
    rescore.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    rescore.set_defaults(func=_cmd_rescore)

    export = sub.add_parser(
        "export", help="write tracked jobs to a CSV file", description="Export tracked jobs to CSV."
    )
    export.add_argument("path", help="the CSV file to write")
    export.set_defaults(func=_cmd_export)

    where = sub.add_parser("paths", help="show where your files are", description="Show where your files are.")
    where.set_defaults(func=_cmd_paths)

    start = sub.add_parser(
        "start", help="open the app in your browser", description="Start the app and open it in your browser."
    )
    start.add_argument("--port", type=int, metavar="N", help="the port to use (default 8765)")
    start.add_argument("--no-browser", action="store_true", help="don't open the browser")
    start.add_argument("--sample", action="store_true", help="start on fictional sample jobs")
    start.set_defaults(func=_cmd_start)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RunInProgress:
        print(RUN_IN_PROGRESS, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
