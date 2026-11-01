import type { Keys, Mode, Settings } from "@/api/types"
import { PROVIDERS } from "@/lib/providers"

/** A Settings tab, by its id in the address (#/settings/<tab>). Settings.tsx checks these ids exist there. */
export type SettingsTab = "profile" | "search" | "ai" | "keys"

/** Which thing is missing: the wizard sends each to the step that fixes it. */
export type MissingId = "profile" | "keyword" | "region" | "apify_token" | "ai_key"

/** One thing a real search still needs, and the Settings tab where it's fixed. */
export interface Missing {
  id: MissingId
  what: string
  tab: SettingsTab
}

const HEADING = /^\s{0,3}#{1,6}(\s|$)/

/** True when the profile has any line that isn't blank or a Markdown heading (so the empty template doesn't count). */
export function profileHasText(md: string): boolean {
  return md.split("\n").some((line) => line.trim() !== "" && !HEADING.test(line))
}

/** What stops a real search from running, in the order to fix it. Sample data needs nothing. */
export function readiness(settings: Settings, keys: Keys, profile: string, mode: Mode): Missing[] {
  if (mode === "sample") return []
  const out: Missing[] = []
  const { linkedin, indeed, scoring } = settings
  if (!profileHasText(profile)) out.push({ id: "profile", what: "Write your profile", tab: "profile" })
  if (linkedin.keywords.length === 0 && indeed.queries.length === 0)
    out.push({ id: "keyword", what: "Add a keyword to search for", tab: "search" })
  if (linkedin.enabled && linkedin.regions.length === 0)
    out.push({ id: "region", what: "Choose where to search", tab: "search" })
  if (!keys.apify_token.set) out.push({ id: "apify_token", what: "Add your Apify token", tab: "keys" })
  const provider = PROVIDERS.find((p) => p.id === scoring.provider)
  if (provider?.needsKey && provider.keyField && !keys[provider.keyField].set)
    out.push({ id: "ai_key", what: `Add your ${provider.label} API key`, tab: "keys" })
  return out
}

/** Where an item is fixed outside the wizard: its Settings tab. */
export function settingsHref(m: Missing): string {
  return `#/settings/${m.tab}`
}

/** The item as it reads inside a sentence: "add your Apify token". */
export function missingLabel(m: Missing): string {
  return m.what.charAt(0).toLowerCase() + m.what.slice(1)
}
