/**
 * The setup wizard's state, and how each step's part of it is written into Settings. Pure functions, so the
 * rules (what each step saves, when Next is allowed) are tested without a browser.
 */
import { ApiError } from "@/api/client"
import type {
  AppState,
  Dealbreaker,
  IndeedJobType,
  Keys,
  ProviderId,
  Region,
  SecretName,
  Settings,
  WizardAnswers,
} from "@/api/types"
import { fieldError } from "@/lib/form"
import { splitPlaces } from "@/lib/places"
import { PROVIDERS, providerInfo } from "@/lib/providers"
import { profileHasText, type Missing, type MissingId } from "@/lib/readiness"

export const STEP_NAMES = ["AI", "You", "Search", "Results go to", "Done"] as const
export const STEP_COUNT = STEP_NAMES.length

/** The step in "#/setup?step=N"; anything else is step 1. */
export function stepFrom(value: string | null): number {
  const n = Number(value)
  return Number.isInteger(n) && n >= 1 && n <= STEP_COUNT ? n : 1
}

export function progressLine(step: number): string {
  return `Step ${step} of ${STEP_COUNT} · ${STEP_NAMES[step - 1]}`
}

export const CAREER_LEVELS = [
  "Student or intern",
  "Graduate or entry level",
  "Early career (1–3 years)",
  "Mid level (3–7 years)",
  "Senior (7+ years)",
] as const

export const PROFILE_TEMPLATE = ["## About me", "", "## What I want", "", "## Strengths", "", "## Constraints", ""].join("\n")

/** A test result, remembered with what was tested: it only counts while the inputs still match. */
export interface TestResult {
  sig: string
  ok: boolean
  message: string
}

export interface AiState {
  provider: Exclude<ProviderId, "fake">
  /** The model typed or picked for each provider, so switching back and forth loses nothing. */
  models: Partial<Record<ProviderId, string>>
  /** Keys typed in this session (not saved until Next). */
  keys: Partial<Record<SecretName, string>>
  test: TestResult | null
}

export interface CvFile {
  name: string
  base64: string
}

export interface YouState {
  cvMode: "upload" | "paste"
  cvText: string
  cvFile: CvFile | null
  roles: string[]
  careerLevel: string
  regions: Region[]
  avoid: string[]
  /** "YYYY-MM-DD" or "". */
  earliestStart: string
  languages: string[]
  extra: string
  /** The review area is shown: after a draft, "Write it myself", or a profile saved before. */
  reviewOpen: boolean
  /** A draft came back in this session (the button then reads "Draft again"). */
  drafted: boolean
  headline: string
  profile: string
  dealbreakers: Dealbreaker[]
}

export interface SearchState {
  apifyToken: string
  apifyTest: (TestResult & { credit: number | null }) | null
  keywords: string[]
  linkedin: boolean
  indeed: boolean
  indeedCountries: string[]
  indeedQueries: string[]
  indeedJobTypes: IndeedJobType[]
}

export interface OutputsState {
  notion: {
    enabled: boolean
    token: string
    parent: string
    /** notionSignature of what the database was created with, or null when there is none to count. */
    connectedSig: string | null
    /** A database exists on the server: creating another needs a confirmation. */
    hasDatabase: boolean
    message: TestResult | null
  }
  csv: { enabled: boolean; path: string; autoExport: boolean; test: TestResult | null }
  webhook: { enabled: boolean; url: string; test: TestResult | null }
}

export interface WizardState {
  ai: AiState
  you: YouState
  search: SearchState
  outputs: OutputsState
}

export function defaultCsvPath(state: AppState): string {
  return state.paths.app_dir + "/jobs.csv"
}

/** The wizard as the saved settings, profile and keys leave it. Nothing typed is invented here. */
export function seedWizard(settings: Settings, profile: string, keys: Keys, state: AppState): WizardState {
  const { scoring, linkedin, indeed, filters, cleanup, outputs, ui } = settings
  let provider: AiState["provider"] = scoring.provider === "fake" ? "anthropic" : scoring.provider
  const models: AiState["models"] = Object.fromEntries(PROVIDERS.map((p) => [p.id, p.models[0] ?? ""]))
  models[provider] = scoring.model
  // Nothing chosen yet (the default provider without its key): Claude Code is the easiest start when it's here.
  const untouched = !ui.setup_complete && scoring.provider === "anthropic" && !keys.anthropic_api_key.set
  if (state.claude_code_found && untouched) provider = "claude_code"

  return {
    ai: { provider, models, keys: {}, test: null },
    you: {
      cvMode: "upload",
      cvText: "",
      cvFile: null,
      roles: [],
      careerLevel: "",
      regions: linkedin.regions,
      avoid: [...filters.excluded_countries, ...filters.excluded_cities],
      earliestStart: cleanup.earliest_start ?? "",
      languages: [],
      extra: "",
      reviewOpen: profile.trim() !== "",
      drafted: false,
      headline: ui.headline,
      profile,
      dealbreakers: scoring.dealbreakers,
    },
    search: {
      apifyToken: "",
      apifyTest: null,
      keywords: linkedin.keywords,
      linkedin: linkedin.enabled,
      indeed: indeed.enabled,
      indeedCountries: indeed.countries,
      indeedQueries: indeed.queries,
      indeedJobTypes: indeed.job_types,
    },
    outputs: {
      notion: {
        enabled: outputs.notion.enabled,
        token: "",
        parent: outputs.notion.parent_page_id,
        connectedSig:
          outputs.notion.database_id !== "" && keys.notion_token.set ? notionSignature("", outputs.notion.parent_page_id) : null,
        hasDatabase: outputs.notion.database_id !== "",
        message: null,
      },
      csv: {
        enabled: outputs.csv.enabled,
        path: outputs.csv.path || defaultCsvPath(state),
        autoExport: outputs.csv.auto_export,
        test: null,
      },
      webhook: { enabled: outputs.webhook.enabled, url: outputs.webhook.url, test: null },
    },
  }
}

// --- AI ----------------------------------------------------------------------

export function modelOf(ai: AiState): string {
  return (ai.models[ai.provider] ?? "").trim()
}

/** The key typed for the current provider, or "" (then the saved one is used). */
export function typedKey(ai: AiState): string {
  const field = providerInfo(ai.provider).keyField
  return field ? (ai.keys[field] ?? "").trim() : ""
}

export function aiSignature(ai: AiState): string {
  return [ai.provider, modelOf(ai), typedKey(ai)].join("\n")
}

/** Next is allowed once a test of exactly what is on screen passed. */
export function aiReady(ai: AiState): boolean {
  return ai.test !== null && ai.test.ok && ai.test.sig === aiSignature(ai)
}

export function applyAi(s: Settings, ai: AiState): Settings {
  return { ...s, scoring: { ...s.scoring, provider: ai.provider, model: modelOf(ai) } }
}

// --- You ---------------------------------------------------------------------

export function draftAnswers(you: YouState): WizardAnswers {
  return {
    target_roles: you.roles,
    career_level: you.careerLevel,
    regions: you.regions.map((r) => r.name),
    avoid: you.avoid,
    earliest_start: you.earliestStart || null,
    languages: you.languages,
    extra_dealbreakers: you.extra.trim(),
  }
}

export function applyYou(s: Settings, you: YouState, regionNames: string[]): Settings {
  const { countries, cities } = splitPlaces(you.avoid, regionNames)
  return {
    ...s,
    linkedin: { ...s.linkedin, regions: you.regions },
    filters: { ...s.filters, excluded_countries: countries, excluded_cities: cities },
    cleanup: { ...s.cleanup, earliest_start: you.earliestStart || null },
    scoring: {
      ...s.scoring,
      dealbreakers: you.dealbreakers
        .map((d) => ({ text: d.text.trim(), consequence: d.consequence.trim() }))
        .filter((d) => d.text !== ""),
    },
    ui: { ...s.ui, headline: you.headline.trim() },
  }
}

/** Why Next can't leave the You step yet, or null when it can. */
export function canLeaveYou(you: Pick<YouState, "roles" | "reviewOpen" | "profile">): string | null {
  if (you.roles.length === 0) return "Add at least one target role"
  if (!you.reviewOpen) return "Draft your profile, or choose Write it myself"
  if (!profileHasText(you.profile)) return "Write a few lines about yourself in your profile"
  return null
}

// --- Search ------------------------------------------------------------------

export const NEED_APIFY_TOKEN = "Add your Apify token to continue"
export const NEED_KEYWORD = "Add a keyword to search for"
export const NEED_REGION = "Choose where to search"

/** The Apify test that applies to the token on screen ("" tests the saved one), or null. */
export function apifyResult(search: SearchState): SearchState["apifyTest"] {
  const token = search.apifyToken.trim()
  return search.apifyTest && search.apifyTest.sig === token ? search.apifyTest : null
}

/**
 * Why Next can't leave the Search step yet, or null when it can. A typed token counts once its test passed; with
 * nothing typed, a token saved before counts unless its test just failed. `regions` are LinkedIn's (from the You step).
 */
export function canLeaveSearch(state: { search: SearchState; regions: Region[]; tokenSaved: boolean }): string | null {
  const { search, regions, tokenSaved } = state
  const result = apifyResult(search)
  if (result && !result.ok) return NEED_APIFY_TOKEN
  if (search.apifyToken.trim() !== "") {
    if (!result?.ok) return "Test your Apify token to continue"
  } else if (!tokenSaved) return NEED_APIFY_TOKEN
  const noKeyword = search.keywords.length === 0
  if ((search.linkedin && noKeyword) || (noKeyword && search.indeedQueries.length === 0)) return NEED_KEYWORD
  if (search.linkedin && regions.length === 0) return NEED_REGION
  return null
}

export function applySearch(s: Settings, search: SearchState): Settings {
  return {
    ...s,
    linkedin: { ...s.linkedin, enabled: search.linkedin, keywords: search.keywords },
    indeed: {
      ...s.indeed,
      enabled: search.indeed,
      countries: search.indeedCountries,
      queries: search.indeedQueries,
      job_types: search.indeedJobTypes,
    },
  }
}

// --- Done --------------------------------------------------------------------

/** The step that fixes each missing item. Regions are chosen on the You step, keywords and the token on Search. */
const STEP_FOR: Record<MissingId, number> = { ai_key: 1, profile: 2, region: 2, keyword: 3, apify_token: 3 }

/**
 * Where a readiness item links inside the wizard: its step, not Settings (which the app sends back to the wizard
 * until setup is complete).
 */
export function wizardHref(m: Missing): string {
  return `#/setup?step=${STEP_FOR[m.id]}`
}

// --- Results go to -----------------------------------------------------------

export type OutputName = "notion" | "csv" | "webhook"

const OUTPUT_NAMES: Record<OutputName, string> = { notion: "Notion", csv: "the CSV file", webhook: "the webhook" }

/** Shown before "Create database" replaces a database that already exists. */
export const NOTION_REPLACE_TEXT =
  "Your current database stays in Notion as it is, but its jobs stop syncing: the statuses you set there are read " +
  "back one last time first. From the next run, your jobs go to the new database."

/** What "Create database" was given: a token typed on screen ("" for the saved one) and the page link. */
export function notionSignature(token: string, parent: string): string {
  return `${token.trim()}\n${parent.trim()}`
}

/** Connected for exactly the token and page on screen. */
export function notionConnected(n: OutputsState["notion"]): boolean {
  return n.connectedSig !== null && n.connectedSig === notionSignature(n.token, n.parent)
}

export function csvSignature(path: string): string {
  return path.trim()
}

export function webhookSignature(url: string): string {
  return url.trim()
}

/** The outputs switched on whose test hasn't passed for what is on screen. */
export function notReadyOutputs(o: OutputsState): OutputName[] {
  const out: OutputName[] = []
  if (o.notion.enabled && !notionConnected(o.notion)) out.push("notion")
  if (o.csv.enabled && !(o.csv.test?.ok && o.csv.test.sig === csvSignature(o.csv.path))) out.push("csv")
  if (o.webhook.enabled && !(o.webhook.test?.ok && o.webhook.test.sig === webhookSignature(o.webhook.url)))
    out.push("webhook")
  return out
}

/** "Notion isn't connected yet. Continue without it?" */
export function notReadyQuestion(names: OutputName[]): string {
  const labels = names.map((n) => OUTPUT_NAMES[n])
  const first = labels[0].charAt(0).toUpperCase() + labels[0].slice(1)
  const list = [first, ...labels.slice(1)]
  const joined = list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}` : list[0]
  return list.length > 1
    ? `${joined} aren't connected yet. Continue without them?`
    : `${joined} isn't connected yet. Continue without it?`
}

export function applyOutputs(s: Settings, o: OutputsState, csvDefault: string): Settings {
  return {
    ...s,
    outputs: {
      csv: { enabled: o.csv.enabled, path: o.csv.path.trim() || csvDefault, auto_export: o.csv.autoExport },
      webhook: { enabled: o.webhook.enabled, url: o.webhook.url.trim() },
      // The parent page and database id are the server's (set by "Create database"): only the switch is ours.
      notion: { ...s.outputs.notion, enabled: o.notion.enabled },
    },
  }
}

// --- errors ------------------------------------------------------------------

export type { FieldErrors } from "@/lib/form"
/** The server's message for `path` or anything under it (the same as lib/form's fieldError). */
export const fieldMessage = fieldError

const UNEXPECTED = "Something went wrong. Check the terminal window for details."

export function messageOf(err: unknown): string {
  return err instanceof ApiError ? err.message : UNEXPECTED
}
