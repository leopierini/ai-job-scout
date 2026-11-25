# Archive jobs that no longer take applications

## Why

Jobs are kept when they are open, then close days later. Nothing re-checks them, so the app lists postings
that say "No longer accepting applications" (or "Not currently accepting applications") on LinkedIn, or
"Job not found" on the company's job board. The app should list only jobs one can still apply to.

## Goals

- Every run archives kept jobs that no longer take applications, with the reason shown in the app.
- A job found closed during a run never reaches the lists, Notion or the webhook.
- Cheap: cents per run, counted in the cost estimate and the run's cost.
- Checks run in the cloud or through public job-board APIs; never the user's browser or LinkedIn account.

## Non-goals

- Indeed, Welcome to the Jungle, Wellfound and custom career pages are not checked (Indeed blocks direct
  reads; the others have no cheap reliable signal). Their jobs stay as today.
- Jobs below the threshold are not checked (they are not shown, and checking them would cost money).

## Anti-goals

- Archiving an open job because a check failed. Only a clear "closed" answer archives; any error, block or
  unknown answer keeps the job.

## Approaches considered

1. **Apify actor for LinkedIn, public board APIs for ATS jobs (chosen).** Cloud, cents per run, no risk to
   the user's connection or account.
2. Direct reads of LinkedIn's public job pages from the user's machine. Free, but LinkedIn answered 429 after
   ~60 reads in a test; rejected for the user's IP.
3. Apify's generic Cheerio Scraper. Near free, but it needs full access to the user's Apify account; rejected.

LinkedIn actors compared on the same jobs (2026-11-25): `automation_craft~linkedin-job-details-scraper`
($0.001 per run + $0.0002 per job) was the only one that flagged both kinds of closed posting. On all 67 kept
LinkedIn jobs it agreed with LinkedIn's page on every job that could be read (38 open, 7 closed, 16 without
an apply button). `piotrv1001` costs 20x more; `northbell`, `hydedata` and `kaix` missed "Not currently
accepting". The user confirmed two "no apply button" jobs show "Not currently accepting applications".

## Design

- **Rule.** New `CleanupSettings.closed: bool = True`, a switch in Settings → Clean up ("Archive jobs that
  no longer take applications"), off when cleanup is off. Same scope as the other rules: kept jobs in To
  review or Interested, not archived; a job the user restored is left alone.
- **When.** In step 1 (Clean up) for every kept job, and at the start of step 5 (Save) for jobs kept in this
  run, before anything is sent to Notion or the webhook. Each job is checked at most once per run.
  `jobscout cleanup` runs it too (a dry run checks and reports, archives nothing).
- **LinkedIn jobs** (source `linkedin`): one run of the actor with every job's URL (`jobUrls`), in batches of
  at most 200. Closed when a row says `isClosed`/`jobState: "closed"`, or has no `applyButton`, or the actor's
  status row says the job is not on LinkedIn any more. Rows that are blocked, missing or unclear keep the job.
  Reason: "closed on LinkedIn".
- **Company job boards** (job URLs on Ashby, Greenhouse, Lever and the other boards `sources/careers.py`
  reads): read each board once through the existing careers code (public JSON APIs, no account) and archive
  jobs whose id is no longer listed. A board that fails to load keeps all its jobs. Reason: "no longer on the
  company's job board".
- **Cost.** `estimate()` adds $0.001 + $0.0002 × kept LinkedIn jobs; the run's cost adds what the check
  billed. An Apify limit or rejected token skips the LinkedIn check (nothing archived) and says so in the
  step message; it never fails the run.
- **Messages.** Clean up: "N archived (M closed)"; Save adds "· M closed" when it archived any.

## Left to the builder

Module layout (e.g. `jobscout/closed.py`), how a job URL maps to its board and id, batch size details, the
exact actor input fields beyond `jobUrls` (company details and extras off), test fixtures (respx, fictional
companies only), and the UI wording within DESIGN.md.
