// Shapes of the local API. They mirror the Python side; keep them in step with:
// settings.py (Settings), models.py (Status, StepName, StepStatus), drafting.py (WizardAnswers, Draft),
// connections.py (ConnectionResult) and server/routes_*.py (responses).

// --- jobs ------------------------------------------------------------------

export type Status =
  | "to_review"
  | "interested"
  | "not_interested"
  | "applied"
  | "interviewing"
  | "offer"
  | "rejected"

/** A list of jobs in the results table: a Status, the archive, or the jobs below the minimum score. */
export type View = Status | "archived" | "below"

export type JobState = "scraped" | "excluded" | "duplicate" | "kept" | "below_threshold"

/** GET /api/jobs?view=… (the description and dedup key are never sent). */
export interface JobOut {
  pk: string
  source: string
  job_id: string
  title: string
  company: string
  location: string
  url: string
  posted: string
  keyword: string
  area: string
  recruiter: string
  recruiter_title: string
  recruiter_url: string
  employment_type: string
  state: JobState
  status: Status
  score: number | null
  reason: string
  flags: string[]
  start_date: string
  deadline: string
  archived_reason: string | null
  first_seen_at: string
  status_updated_at: string
  restored: boolean
}

/** GET /api/jobs/counts: one count per view. */
export type ViewCounts = Record<View, number>

// --- runs ------------------------------------------------------------------

export type StepName = "cleanup" | "search" | "filter" | "score" | "save"
export type StepStatus = "pending" | "running" | "done" | "error" | "skipped"
export type RunStatus = "running" | "done" | "error" | "cancelled"

export type CountKey =
  | "archived"
  | "searched"
  | "billed_linkedin"
  | "billed_indeed"
  | "new"
  | "duplicates"
  | "excluded"
  | "scored"
  | "kept"
  | "below"
  | "failed_scoring"
  | "delivered"
  | "delivery_failed"

export interface RunOut {
  id: number
  /** "app", "app:rescore", "cli", "cli:rescore", … */
  trigger: string
  started_at: string
  finished_at: string | null
  status: RunStatus
  /** Empty while the run is going; filled when it ends. */
  counts: Partial<Record<CountKey, number>>
  cost_usd: number
  error: string | null
}

export interface EventOut {
  seq: number
  step: StepName
  status: StepStatus
  message: string
  done: number | null
  total: number | null
  elapsed_s: number
  data: Record<string, unknown>
}

/** Where one step stands: the last event seen for it, or pending when there is none. */
export interface StepState {
  status: StepStatus
  message: string
  done: number | null
  total: number | null
  elapsed_s: number
  data: Record<string, unknown>
}

/** GET /api/runs/latest (null when there are no runs) and GET /api/runs/{id}?after=… */
export interface RunWithEvents {
  run: RunOut
  events: EventOut[]
}

export interface StartRunBody {
  kind: "run" | "rescore"
  window_days?: number | null
}

/** POST /api/estimate */
export interface Estimate {
  searches: number
  max_results: number
  max_usd: number
  /** Apify credit left; null in sample mode, without a token, or when Apify can't be reached. */
  credit_usd: number | null
}

export interface RescorePreview {
  count: number
  approx_tokens: number
}

// --- settings --------------------------------------------------------------

export interface Region {
  name: string
  geo_id: string
}

export interface LinkedInSettings {
  enabled: boolean
  actor: string
  keywords: string[]
  regions: Region[]
  cap_per_keyword: number
  /** Most recent, most relevant, or LinkedIn's default. */
  sort_by: "DD" | "R" | ""
  scrape_company: boolean
  price_per_result: number
}

export type IndeedJobType =
  | "fulltime"
  | "parttime"
  | "contract"
  | "internship"
  | "temporary"
  | "permanent"
  | "seasonal"
  | "freelance"

export interface IndeedSettings {
  enabled: boolean
  actor: string
  countries: string[]
  queries: string[]
  job_types: IndeedJobType[]
  max_rows_per_query: number
  price_per_result: number
}

export interface WindowSettings {
  default_days: number
  overlap_minutes: number
}

export type PostingLanguage = "en" | "it" | "de" | "fr" | "nl" | "es" | "pt" | "sv" | "da" | "no" | "pl"

export interface FilterSettings {
  excluded_countries: string[]
  excluded_cities: string[]
  excluded_title_words: string[]
  languages: PostingLanguage[]
}

export interface Dealbreaker {
  text: string
  consequence: string
}

export type ProviderId = "anthropic" | "openai" | "gemini" | "openrouter" | "ollama" | "claude_code" | "fake"

export interface ScoringSettings {
  provider: ProviderId
  model: string
  min_score: number
  send_below_threshold: boolean
  reasoning_language: string
  batch_size: number
  max_jobs_per_run: number | null
  bands: string
  dealbreakers: Dealbreaker[]
  extra_instructions: string
}

export interface CleanupSettings {
  enabled: boolean
  deadline_passed: boolean
  start_too_early: boolean
  too_old: boolean
  excluded_place: boolean
  /** ISO date (YYYY-MM-DD) or null. */
  earliest_start: string | null
  max_age_days: number
  interested_exempt_from_age: boolean
}

export interface CsvOutputSettings {
  enabled: boolean
  path: string
  auto_export: boolean
}

export interface WebhookOutputSettings {
  enabled: boolean
  url: string
}

export interface NotionOutputSettings {
  enabled: boolean
  parent_page_id: string
  database_id: string
}

export interface OutputSettings {
  csv: CsvOutputSettings
  webhook: WebhookOutputSettings
  notion: NotionOutputSettings
}

export type TableColumn = "score" | "role" | "why" | "starts" | "posted" | "deadline" | "source"

export interface UiSettings {
  setup_complete: boolean
  /** Top-bar profile summary, at most 120 characters. */
  headline: string
  columns: TableColumn[]
  default_sort: "score" | "posted" | "starts"
}

/** GET and PUT /api/settings */
export interface Settings {
  linkedin: LinkedInSettings
  indeed: IndeedSettings
  windows: WindowSettings
  filters: FilterSettings
  scoring: ScoringSettings
  cleanup: CleanupSettings
  outputs: OutputSettings
  ui: UiSettings
}

// --- keys, state, profile ---------------------------------------------------

export type SecretName =
  | "apify_token"
  | "anthropic_api_key"
  | "openai_api_key"
  | "gemini_api_key"
  | "openrouter_api_key"
  | "notion_token"

/** A key's status: never its value, at most "…" plus its last 4 characters. */
export interface KeyStatus {
  set: boolean
  hint: string
}

/** GET /api/keys */
export type Keys = Record<SecretName, KeyStatus>

/** PUT /api/keys: a new value, or null to remove the key. Keys left out stay as they are. */
export type KeysUpdate = Partial<Record<SecretName, string | null>>

export type Mode = "real" | "sample"

/** GET /api/state (PUT /api/mode returns the same). */
export interface AppState {
  mode: Mode
  setup_complete: boolean
  headline: string
  claude_code_found: boolean
  paths: {
    app_dir: string
    database: string
    settings: string
    profile: string
  }
}

export interface Profile {
  text: string
}

// --- setup -----------------------------------------------------------------

/** GET /api/regions */
export interface Regions {
  linkedin: Region[]
  /** Indeed country codes, e.g. "uk", "de". */
  indeed: string[]
}

/** POST /api/test/ai, /api/test/webhook, /api/test/csv; /api/test/apify adds credit_usd. */
export interface ConnectionResult {
  ok: boolean
  message: string
  credit_usd?: number | null
}

export interface NotionConnectResult {
  ok: boolean
  message: string
  database_id: string
}

export interface WizardAnswers {
  target_roles: string[]
  career_level: string
  regions?: string[]
  avoid?: string[]
  /** ISO date (YYYY-MM-DD) or null. */
  earliest_start?: string | null
  languages?: string[]
  extra_dealbreakers?: string
}

/** POST /api/draft body: either the pasted text or an uploaded file. */
export interface DraftBody {
  cv_text?: string | null
  cv_file?: { name: string; base64: string } | null
  answers: WizardAnswers
}

/** POST /api/draft */
export interface Draft {
  headline: string
  profile_md: string
  dealbreakers: Dealbreaker[]
  keywords: string[]
  indeed_queries: string[]
}

// --- errors ----------------------------------------------------------------

/** The body of every non-2xx response. `fields` maps a dotted path (e.g. "scoring.min_score") to a message. */
export interface ErrorBody {
  error: string
  fields: Record<string, string> | null
}
