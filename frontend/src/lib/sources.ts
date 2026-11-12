import type { ApifyBoardName, Settings } from "@/api/types"
import { SOURCE_LABELS } from "@/lib/jobs"

/** The job boards searched through an Apify actor besides LinkedIn and Indeed. */
export const APIFY_BOARDS: readonly ApifyBoardName[] = ["wttj", "wellfound", "yc", "atsindex"]

/** The startup sources that take the shared searches at the top of the Startups tab (not career pages). */
export type SearchingStartupSource = "builtineurope" | "jobgether" | "vcboards" | ApifyBoardName

/** What a startup source searches for: its own searches, or the shared ones when it has none (as `apply_shared`). */
export function queriesFor(s: Settings, name: SearchingStartupSource): string[] {
  return s[name].queries.length > 0 ? s[name].queries : s.startup_queries
}

/** Each source that is on and has something to search, by its key ("linkedin", "careers", …). */
export function sourcesInUse(s: Settings): string[] {
  const on: string[] = []
  if (s.linkedin.enabled && s.linkedin.keywords.length > 0) on.push("linkedin")
  if (s.indeed.enabled && s.indeed.queries.length > 0) on.push("indeed")
  if (s.builtineurope.enabled && queriesFor(s, "builtineurope").length > 0) on.push("builtineurope")
  if (s.jobgether.enabled && queriesFor(s, "jobgether").length > 0) on.push("jobgether")
  if (s.vcboards.enabled && s.vcboards.boards.length > 0) on.push("vcboards")
  if (s.careers.enabled && s.careers.pages.length > 0) on.push("careers")
  for (const name of APIFY_BOARDS) if (s[name].enabled && queriesFor(s, name).length > 0) on.push(name)
  return on
}

/** True when a run would use Apify, so the token is needed: the same checks as `build_pipeline`. */
export function usesApify(s: Settings): boolean {
  const { linkedin, indeed } = s
  return (
    (linkedin.enabled && linkedin.keywords.length > 0 && linkedin.regions.length > 0) ||
    (indeed.enabled && indeed.queries.length > 0 && indeed.countries.length > 0 && indeed.job_types.length > 0) ||
    APIFY_BOARDS.some((name) => s[name].enabled && queriesFor(s, name).length > 0)
  )
}

/** "LinkedIn", "LinkedIn and Indeed", "LinkedIn, Indeed and 2 more": the sources a run searches. */
export function sourcesText(s: Settings): string {
  const names = sourcesInUse(s).map((key) => SOURCE_LABELS[key] ?? key)
  if (names.length === 0) return "LinkedIn"
  if (names.length <= 2) return names.join(" and ")
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`
}
