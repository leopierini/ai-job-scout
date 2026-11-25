# Closed Jobs Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every run archives kept jobs that no longer take applications (LinkedIn via an Apify actor, company job boards via their public feeds).

**Architecture:** A new `jobscout/closed.py` decides, for a list of job rows, which are closed: LinkedIn rows go to one Apify actor run (batched), board rows are matched against their board's feed read through `sources/careers.py`. `cleanup.py` picks the rows the rule applies to; the pipeline calls it in step 1 for all kept jobs and in step 5 for jobs kept this run, and bills it in the run's cost.

**Tech Stack:** Python 3.13, httpx, pydantic settings, pytest + respx; React + shadcn for the one switch.

**Spec:** `docs/superpowers/specs/2026-11-25-closed-jobs-check-design.md`

## Global Constraints

- Actor: `automation_craft~linkedin-job-details-scraper`; price $0.001 per run + $0.0002 per job delivered.
- Actor input: `jobUrls`, `exactPostedDate: false`, `includeDescriptionHtml: false`, `includeDescriptionMarkdown: false` (company, poster, e-mails stay off by default).
- Reasons: `"closed on LinkedIn"`, `"no longer on the company's job board"`.
- Only a clear "closed" answer archives; every error, block or unclear row keeps the job.
- Tests never touch the network; fictional companies only. No personal data anywhere.
- After the UI change: `npm run build` into `src/jobscout/server/static`, visual check with `shoot.mjs`.

## Review Focus

- An Apify limit / rejected token / actor failure during the check: the run goes on, nothing archived, the step message says why.
- A board feed that answers 200 with zero jobs: treat as unknown (keep), not as "everything closed".
- A job URL whose board feed lists the job under a different URL shape (slug, query string): matched by id in the path, not only by full URL.
- A job the user restored, or marked Applied/Interviewing/Offer/Not interested/Rejected: never checked, never archived.
- The cleanup master switch or the `closed` switch off: no actor run, no cost.

---

### Task 1: `closed.py`, deciding which jobs are closed

**Files:**
- Create: `src/jobscout/closed.py`
- Test: `tests/test_closed.py`

**Interfaces:**
- Consumes: `ApifyClient.run_actor(actor, payload) -> list[dict]` (raises `ApifyError` with `.kind`), `careers.board_from_url`, `careers.read_feed`, `sources.get_public`, `sources.USER_AGENT`, `models.JobRow`.
- Produces:
  - `CLOSED_ACTOR: str`, `RUN_FEE_USD = 0.001`, `JOB_PRICE_USD = 0.0002`, `BATCH_SIZE = 200`, `LINKEDIN_REASON`, `BOARD_REASON`
  - `linkedin_job_id(url: str) -> str | None` (digits at the end of the `/jobs/view/...` path segment)
  - `linkedin_verdicts(items: list[dict]) -> dict[str, bool]` (job id → True closed / False open; ids with no clear answer are absent)
  - `@dataclass ClosedCheck: closed: dict[str, str]` (pk → reason), `linkedin_jobs: int` (rows billed), `actor_runs: int`, `note: str` (empty, or why a part was skipped)
  - `check_closed(rows: list[JobRow], apify: ApifyClient | None, client: httpx.Client | None, cancel: threading.Event) -> ClosedCheck`

- [ ] **Step 1: Write failing tests** in `tests/test_closed.py`:
  - `test_linkedin_job_id` for `https://uk.linkedin.com/jobs/view/associate-pm-at-acme-4478176641?position=5` → `"4478176641"`, `https://www.linkedin.com/jobs/view/4478176641/` → same, a non-LinkedIn URL → `None`.
  - `test_linkedin_verdicts`: job row `isClosed: true` → True; `jobState: "closed"` → True; `applyButton: null` with `jobState: "open"` → True; `applyButton: "offsite"` → False; status row `{"type":"status","status":"not_found","input":".../jobs/view/111"}` → True; status `blocked`/`failed` → absent; `summary` row ignored.
  - `test_check_closed_linkedin` (respx on the run-sync URL): two LinkedIn rows, one closed → `closed == {pk1: LINKEDIN_REASON}`, `linkedin_jobs == 2`, `actor_runs == 1`, payload carries the four input fields.
  - `test_check_closed_batches`: 201 LinkedIn rows → 2 actor calls.
  - `test_check_closed_apify_limit`: actor answers the limit error → `closed == {}`, `note` mentions the Apify limit, no exception.
  - `test_check_closed_board`: an Ashby row whose id is missing from the feed → `BOARD_REASON`; a Greenhouse row whose id is in the feed → kept; a feed with zero jobs → kept; a feed answering 500 → kept; one feed request per board even with two rows on it.
  - `test_check_closed_skips_other_sources`: Indeed / WTTJ rows → no request at all.
- [ ] **Step 2: Run** `uv run pytest tests/test_closed.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `closed.py`.** Board rows: group by `board_from_url(row.url)` (key `(ats, token, host)`), one `get_public` GET per board (no retries; any error or non-200 → unknown), `read_feed`; a row is open when any posting's id is a path segment of the row URL (or the leading part of one, for Teamtailor's `123-slug`), or its normalised URL equals the row's; closed only when the feed held ≥1 posting and none matched. LinkedIn rows: dedupe by job id, batches of `BATCH_SIZE`, stop on `cancel`; an `ApifyError` stops the LinkedIn part and sets `note`; `linkedin_jobs` counts job rows returned (what the actor bills).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `Add a check that tells which kept jobs no longer take applications`.

### Task 2: Wire the rule into settings, cleanup, the run, costs and the CLI

**Files:**
- Modify: `src/jobscout/settings.py` (`CleanupSettings.closed: bool = True`), `src/jobscout/cleanup.py`, `src/jobscout/pipeline.py`, `src/jobscout/costs.py`, `src/jobscout/cli.py`
- Test: `tests/test_cleanup.py`, `tests/test_pipeline.py`, `tests/test_windows_costs.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1's `check_closed`, `ClosedCheck`, `RUN_FEE_USD`, `JOB_PRICE_USD`.
- Produces:
  - `cleanup.closed_candidates(rows: list[JobRow], cfg: CleanupSettings) -> list[JobRow]` (enabled + closed on; status To review or Interested; not archived; not restored)
  - `cleanup.archive_closed(db, rows, apify, client, cancel, dry_run=False) -> tuple[list[tuple[str, str]], ClosedCheck]` (archives through `db.archive`, which re-checks status)
  - `costs.closed_check_cost(jobs: int, runs: int) -> float`; `estimate()` adds `RUN_FEE_USD + JOB_PRICE_USD * n` for `n` kept LinkedIn candidates when the rule is on (estimate takes an optional `kept_linkedin: int = 0`).
  - Pipeline counts `closed` (archived as closed) and adds the check's cost to `RunSummary.cost_usd`.

- [ ] **Step 1: Write failing tests:**
  - `test_cleanup.py::test_closed_candidates` covers each excluded case from Review Focus (restored, Applied, archived, switch off, master off).
  - `test_pipeline.py::test_cleanup_archives_closed_jobs`: a pipeline with a fake `check` (inject `closed_check: Callable` into `Pipeline`, default `check_closed`) archives a kept LinkedIn row in step 1 with `"closed on LinkedIn"`; message `"1 archived (1 closed)"`.
  - `test_pipeline.py::test_save_checks_jobs_kept_this_run`: a job kept in this run and reported closed is archived before deliveries (the webhook fake receives nothing); a job already checked in step 1 is not sent to the check again.
  - `test_pipeline.py::test_closed_check_failure_does_not_fail_run`: the check returns `note="Apify monthly limit reached…"` → run status `done`, message carries the note.
  - `test_windows_costs.py::test_estimate_counts_closed_check`: `estimate(settings, kept_linkedin=50)` adds `0.001 + 0.0002 * 50`.
  - `test_cli.py::test_cleanup_dry_run_reports_closed`: with a fake check, `cleanup --dry-run` prints the closed job and archives nothing.
- [ ] **Step 2: Run** `uv run pytest -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** The pipeline builds the `ApifyClient` whenever there is an Apify token and an HTTP client (also for runs that search only free sources), keeps the set of pks checked this run, and in `_save` checks `self._found_now` rows that are kept and not yet checked. Step messages: cleanup `"{n} archived ({m} closed)"` when m > 0, save appends `" · {m} closed"`; a non-empty `note` is appended as `" · closed-job check skipped: {note}"`. `jobscout estimate` passes the number of kept LinkedIn candidates; the CLI cleanup builds the clients from settings/secrets (no Apify token → board check only).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `Archive kept jobs that no longer take applications on every run`.

### Task 3: The switch in Settings → Clean up

**Files:**
- Modify: `frontend/src/screens/settings/CleanupSection.tsx`, the frontend settings type (where `deadline_passed` is typed), rebuilt `src/jobscout/server/static/**`

- [ ] **Step 1:** Add `closed: boolean` to the cleanup settings type and a `SwitchRow` after "Archive jobs whose deadline has passed": label `"Archive jobs that no longer take applications"`, hint `"Checks LinkedIn (about $0.0002 a job on Apify) and companies' own job boards (free) on every run."`, disabled when cleanup is off.
- [ ] **Step 2:** `npm test` and `npm run build` in `frontend/`. Expected: pass, static rebuilt.
- [ ] **Step 3:** Visual check: start the sample app on port 8799, `npm run shoot -- http://127.0.0.1:8799 ../.artifacts/screens settings` at both sizes, compare against DESIGN.md, console clean.
- [ ] **Step 4: Commit** `Add the closed-jobs switch to the Clean up settings` with the rebuilt static files.
